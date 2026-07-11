from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass
from pathlib import Path
from importlib import metadata as importlib_metadata
import json
import os
import shutil
from tempfile import mkdtemp
from time import perf_counter
from typing import Any

import numpy as np
import pandas as pd
import yaml

from ibl_ai_agent.datasets import bwm_ephys, bwm_simple
from ibl_ai_agent.datasets import bwm_session_assets as session_assets
from ibl_ai_agent.datasets import bwm_shared


DATASET_NAME = "bwm_behavior"
DATASET_VERSION = "2.0.0"
SCHEMA_VERSION = 2
PARQUET_ENGINE = "pyarrow"
PARQUET_COMPRESSION = "zstd"
SIGNAL_CONTAINER_FORMAT = "zip_blosc_shards"
SIGNAL_COMPRESSION = "blosc_zstd_shuffle"
CAMERA_NAMES = session_assets.CAMERA_NAMES
# Maps full ALF camera names to brainbox.io.one.SessionLoader's short view names.
CAMERA_VIEW_NAMES = {"leftCamera": "left", "rightCamera": "right", "bodyCamera": "body"}
EVENT_COLUMNS = bwm_ephys.EVENT_COLUMNS
SESSION_SHARD_SUFFIX = ".zip"
BEHAVIOR_EVENT_FEATURE_COLUMNS = (("wheel", "wheel"), ("leftCamera", "pose"), ("rightCamera", "pose"), ("bodyCamera", "pose"))
BEHAVIOR_EVENT_WINDOW_SPEC = "pre=0.200|post=0.300|event-aligned"
WHEEL_STATE_DETECTOR_NAME = "ibllib.io.extractors.training_wheel.extract_wheel_moves"
QUIESCENCE_MIN_DURATION_S = 0.2
# Uniform sampling rate for the interpolated wheel signal produced by
# brainbox.io.one.SessionLoader.load_wheel. Matches the `wheel_downsample_rate_hz`
# field of the 100 Hz compression strategies in bwm_behavior_compression.STRATEGIES
# so build-time interpolation and stored sampling rate agree.
WHEEL_FS_HZ = 100.0
BEHAVIOR_SESSION_SHARD_FORMAT_V1 = "ibl_ai_agent_behavior_session_shard_v1"
BEHAVIOR_SESSION_SHARD_FORMAT_V2 = "ibl_ai_agent_behavior_session_shard_v2"
LEGACY_BEHAVIOR_SESSION_SHARD_FORMAT_V2 = "ibl" + "_agent_behavior_session_shard_v2"
BEHAVIOR_SESSION_SHARD_FORMATS_V2 = {
    BEHAVIOR_SESSION_SHARD_FORMAT_V2,
    LEGACY_BEHAVIOR_SESSION_SHARD_FORMAT_V2,
}


class BuildError(RuntimeError):
    """Raised when the dataset build cannot complete successfully."""


DEFAULT_BUILD_JOBS = max(1, (os.cpu_count() or 2) // 2)
FEATURE_PROGRESS_INTERVAL_S = 5.0


@dataclass(frozen=True)
class BuildConfig:
    output_root: Path
    cache_root: Path
    allow_remote_fetch: bool = False
    limit_insertions: int | None = None
    prefetch_missing: bool = True
    require_signals: bool = False
    resume: bool = True
    jobs: int = DEFAULT_BUILD_JOBS
    verbose: bool = True


@dataclass(frozen=True)
class BuildOutputs:
    dataset_dir: Path
    sessions_path: Path
    trials_path: Path
    events_path: Path
    wheel_availability_path: Path
    pose_availability_path: Path
    trial_behavior_features_path: Path
    wheel_trial_features_path: Path
    pose_trial_features_path: Path
    event_aligned_behavior_features_path: Path
    behavior_session_features_path: Path
    movement_state_epochs_path: Path
    quiescence_state_epochs_path: Path
    behavior_state_session_features_path: Path
    manifest_path: Path
    schema_path: Path
    provenance_path: Path
    prefetch_report_path: Path
    build_report_path: Path
    summary_path: Path
    wheel_store_path: Path
    pose_store_path: Path
    archive_path: Path | None = None
    archive_checksum_path: Path | None = None


@dataclass(frozen=True)
class DatasetLayout:
    kind: str
    expected_dataset_version: str
    expected_schema_version: int
    compression_profile: str | None = None


EXPECTED_TABLE_OUTPUT_ATTRS = {
    "sessions": "sessions_path",
    "trials": "trials_path",
    "events": "events_path",
    "wheel_availability": "wheel_availability_path",
    "pose_availability": "pose_availability_path",
    "trial_behavior_features": "trial_behavior_features_path",
    "wheel_trial_features": "wheel_trial_features_path",
    "pose_trial_features": "pose_trial_features_path",
    "event_aligned_behavior_features": "event_aligned_behavior_features_path",
    "behavior_session_features": "behavior_session_features_path",
    "movement_state_epochs": "movement_state_epochs_path",
    "quiescence_state_epochs": "quiescence_state_epochs_path",
    "behavior_state_session_features": "behavior_state_session_features_path",
}
DERIVED_TABLE_NAMES = {
    "wheel_availability",
    "pose_availability",
    "trial_behavior_features",
    "wheel_trial_features",
    "pose_trial_features",
    "event_aligned_behavior_features",
    "behavior_session_features",
    "movement_state_epochs",
    "quiescence_state_epochs",
    "behavior_state_session_features",
}


def _load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _first_behavior_shard_meta(sessions_dir: Path) -> dict[str, Any]:
    if not sessions_dir.exists():
        return {}
    for shard_path in sorted(sessions_dir.glob(f"*{SESSION_SHARD_SUFFIX}")):
        try:
            shard = _read_behavior_session_store_shard(shard_path)
        except Exception:
            continue
        return dict(shard.get("meta", {}) or {})
    return {}


def _detect_dataset_layout(*, dataset_dir: Path, outputs: BuildOutputs, schema: dict[str, Any] | None = None) -> DatasetLayout:
    schema = schema or {}
    schema_dataset_version = schema.get("dataset_version")
    schema_schema_version = schema.get("schema_version")
    schema_profile = schema.get("compression_profile")

    try:
        from ibl_ai_agent.datasets import bwm_behavior_upgrade
    except Exception:
        bwm_behavior_upgrade = None

    if (
        bwm_behavior_upgrade is not None
        and (
            schema_dataset_version == bwm_behavior_upgrade.TARGET_DATASET_VERSION
            or schema_schema_version == 3
            or schema_profile == bwm_behavior_upgrade.COMPRESSION_PROFILE
        )
    ):
        return DatasetLayout(
            kind="upgrade_v1_1",
            expected_dataset_version=bwm_behavior_upgrade.TARGET_DATASET_VERSION,
            expected_schema_version=3,
            compression_profile=bwm_behavior_upgrade.COMPRESSION_PROFILE,
        )

    shard_meta = _first_behavior_shard_meta(outputs.wheel_store_path)
    shard_profile = ((shard_meta.get("compression") or {}).get("profile"))
    if (
        bwm_behavior_upgrade is not None
        and (
            shard_meta.get("format") in BEHAVIOR_SESSION_SHARD_FORMATS_V2
            or shard_profile == bwm_behavior_upgrade.COMPRESSION_PROFILE
            or dataset_dir.name == bwm_behavior_upgrade.TARGET_DATASET_VERSION
        )
    ):
        return DatasetLayout(
            kind="upgrade_v1_1",
            expected_dataset_version=bwm_behavior_upgrade.TARGET_DATASET_VERSION,
            expected_schema_version=3,
            compression_profile=bwm_behavior_upgrade.COMPRESSION_PROFILE,
        )

    return DatasetLayout(
        kind="base_v1_0",
        expected_dataset_version=DATASET_VERSION,
        expected_schema_version=SCHEMA_VERSION,
    )


def build_bwm_behavior_dataset(config: BuildConfig) -> BuildOutputs:
    target_dir = config.output_root / DATASET_NAME / DATASET_VERSION
    if target_dir.exists():
        raise BuildError(
            f"Output directory already exists: {target_dir}. "
            "Remove it or build to a different output root before rerunning."
        )

    tmp_parent = target_dir.parent
    tmp_parent.mkdir(parents=True, exist_ok=True)
    tmp_dir, resumed = _resolve_behavior_build_dir(tmp_parent, config=config)
    if resumed:
        _emit(config, f"Resume: reusing existing partial build directory {tmp_dir}")
    else:
        _emit(config, f"Build: created working directory {tmp_dir}")
    _write_build_state(tmp_dir, {"status": "running", "resumed": resumed, "started_at": bwm_shared.now_iso()})

    try:
        build_started_at = perf_counter()
        roster = bwm_simple._load_roster(limit_insertions=config.limit_insertions)
        _emit(config, f"Preflight: scanning behavior cache for {roster['eid'].nunique()} session(s).")
        initial_scan = inspect_bwm_behavior_cache(config, roster=roster)
        _emit(config, _format_scan_summary(initial_scan, title="Initial behavior cache scan"))

        final_scan = initial_scan
        prefetch_report = {
            "dataset_name": DATASET_NAME,
            "dataset_version": DATASET_VERSION,
            "generated_at": bwm_shared.now_iso(),
            "config": {
                "allow_remote_fetch": bool(config.allow_remote_fetch),
                "prefetch_missing": bool(config.prefetch_missing),
                "require_signals": bool(config.require_signals),
                "resume": bool(config.resume),
                "limit_insertions": config.limit_insertions,
                "pose_dtype": "float32",
            },
            "initial": initial_scan,
            "actions": [],
        }

        if config.prefetch_missing and _scan_has_missing_required_assets(initial_scan):
            if not config.allow_remote_fetch:
                _emit(config, "Missing behavior assets detected, but remote fetch is disabled.")
            else:
                _emit(config, "Prefetch: missing behavior assets detected, attempting to populate local cache now.")
                prefetch_report["actions"] = _prefetch_missing_assets(config, scan=initial_scan)
                final_scan = inspect_bwm_behavior_cache(config, roster=roster)
                _emit(config, _format_scan_summary(final_scan, title="Post-prefetch behavior cache scan"))
        else:
            if not _scan_has_missing_required_assets(initial_scan):
                _emit(config, "Preflight: all required behavior assets are already present in the local cache.")
        prefetch_report["final"] = final_scan

        final_missing_required_assets = _scan_has_missing_required_assets(final_scan)
        prefetch_report["partial_build"] = bool(final_missing_required_assets)
        prefetch_report["release_status"] = "partial" if final_missing_required_assets else "complete"
        if final_missing_required_assets:
            failure_report_path = _write_failure_prefetch_report(target_dir.parent, prefetch_report)
            if config.require_signals:
                raise BuildError(
                    "Required behavior assets are still missing after preflight/prefetch. "
                    f"Strict mode is enabled, so the dataset was not finalized. See {failure_report_path} for details."
                )
            _emit(
                config,
                "Warning: required behavior assets are still missing after preflight/prefetch. "
                f"Finalizing a partial dataset and recording the gaps in the reports; see {failure_report_path} for details."
            )

        one_remote = bwm_simple._make_one(config.cache_root, mode="remote") if config.allow_remote_fetch else None
        trials_aggregate_path = bwm_simple._resolve_aggregate_table(
            config.cache_root,
            "trials",
            allow_remote_fetch=config.allow_remote_fetch,
            one_remote=one_remote,
        )

        metadata_dir = tmp_dir / "metadata"
        features_dir = tmp_dir / "features"
        sessions_dir = tmp_dir / "sessions"
        metadata_dir.mkdir(parents=True, exist_ok=True)
        features_dir.mkdir(parents=True, exist_ok=True)
        sessions_dir.mkdir(parents=True, exist_ok=True)

        _emit(config, "Build: preparing behavior metadata tables from cached aggregate data.")
        metadata_started_at = perf_counter()
        trials_df = bwm_simple._build_trials(roster, trials_aggregate_path)
        sessions_df = _build_sessions(roster, trials_df)
        events_df = bwm_ephys._build_events(trials_df)
        trial_behavior_features_df = _compute_trial_behavior_features(trials_df)
        _emit(config, f"Build: computing per-session wheel/pose/event features with jobs={max(1, config.jobs)}.")
        (
            wheel_availability_df,
            pose_availability_df,
            wheel_trial_features_df,
            pose_trial_features_df,
            event_aligned_behavior_features_df,
            movement_state_epochs_df,
            quiescence_state_epochs_df,
            behavior_state_session_features_df,
        ) = _build_behavior_feature_tables(
            sessions_df=sessions_df,
            trials_df=trials_df,
            cache_root=config.cache_root,
            jobs=config.jobs,
            verbose=config.verbose,
        )
        sessions_df = sessions_df.merge(wheel_availability_df[['eid', 'wheel_present']].drop_duplicates('eid'), on='eid', how='left')
        pose_session_presence = pose_availability_df.groupby('eid')['pose_present'].max().rename('pose_present') if not pose_availability_df.empty else pd.Series(dtype=bool)
        if not pose_session_presence.empty:
            sessions_df = sessions_df.merge(pose_session_presence, on='eid', how='left')
        camera_lists = pose_availability_df.loc[pose_availability_df['pose_present']].groupby('eid')['camera'].apply(lambda s: sorted({str(v) for v in s if str(v)})).rename('present_cameras') if not pose_availability_df.empty else pd.Series(dtype=object)
        if not camera_lists.empty:
            sessions_df = sessions_df.merge(camera_lists, on='eid', how='left')
        if 'wheel_present' not in sessions_df.columns:
            sessions_df['wheel_present'] = False
        if 'pose_present' not in sessions_df.columns:
            sessions_df['pose_present'] = False
        sessions_df['wheel_present'] = sessions_df['wheel_present'].fillna(False).astype(bool)
        sessions_df['pose_present'] = sessions_df['pose_present'].fillna(False).astype(bool)
        if 'present_cameras' not in sessions_df.columns:
            sessions_df['present_cameras'] = [[] for _ in range(len(sessions_df))]
        sessions_df['present_cameras'] = sessions_df['present_cameras'].apply(lambda x: x if isinstance(x, list) else [])
        behavior_session_features_df = _build_behavior_session_features(sessions_df=sessions_df, trial_behavior_features_df=trial_behavior_features_df, wheel_availability_df=wheel_availability_df, pose_availability_df=pose_availability_df)
        sessions_df.sort_values(["lab", "subject", "date", "session_number"], inplace=True, kind="mergesort")
        trials_df.sort_values(["lab", "subject", "date", "session_number", "trial_id"], inplace=True, kind="mergesort")
        if not events_df.empty:
            events_df.sort_values(["lab", "subject", "date", "session_number", "trial_id", "event_id"], inplace=True, kind="mergesort")
        if not trial_behavior_features_df.empty:
            trial_behavior_features_df.sort_values(['eid', 'trial_id'], inplace=True, kind='mergesort')
        if not wheel_trial_features_df.empty:
            wheel_trial_features_df.sort_values(['eid', 'trial_id'], inplace=True, kind='mergesort')
        if not behavior_session_features_df.empty:
            behavior_session_features_df.sort_values(['eid'], inplace=True, kind='mergesort')
        if not pose_trial_features_df.empty:
            pose_trial_features_df.sort_values(['eid', 'trial_id', 'camera'], inplace=True, kind='mergesort')
        if not event_aligned_behavior_features_df.empty:
            event_aligned_behavior_features_df.sort_values(['eid', 'trial_id', 'signal_name', 'event_name'], inplace=True, kind='mergesort')
        if not movement_state_epochs_df.empty:
            movement_state_epochs_df.sort_values(['eid', 'movement_id'], inplace=True, kind='mergesort')
        if not quiescence_state_epochs_df.empty:
            quiescence_state_epochs_df.sort_values(['eid', 'quiescence_id'], inplace=True, kind='mergesort')
        if not behavior_state_session_features_df.empty:
            behavior_state_session_features_df.sort_values(['eid'], inplace=True, kind='mergesort')
        _emit(
            config,
            "Build: metadata frames ready "
            f"(sessions={len(sessions_df):,}, trials={len(trials_df):,}, wheel_features={len(wheel_trial_features_df):,}, "
            f"pose_features={len(pose_trial_features_df):,}) in {perf_counter() - metadata_started_at:.1f}s."
        )

        outputs = _write_metadata_tables(
            metadata_dir=metadata_dir,
            features_dir=features_dir,
            sessions_df=sessions_df,
            trials_df=trials_df,
            events_df=events_df,
            wheel_availability_df=wheel_availability_df,
            pose_availability_df=pose_availability_df,
            trial_behavior_features_df=trial_behavior_features_df,
            wheel_trial_features_df=wheel_trial_features_df,
            pose_trial_features_df=pose_trial_features_df,
            event_aligned_behavior_features_df=event_aligned_behavior_features_df,
            behavior_session_features_df=behavior_session_features_df,
            movement_state_epochs_df=movement_state_epochs_df,
            quiescence_state_epochs_df=quiescence_state_epochs_df,
            behavior_state_session_features_df=behavior_state_session_features_df,
            dataset_dir=tmp_dir,
        )
        _write_build_state(
            tmp_dir,
            {
                "status": "metadata_written",
                "resumed": resumed,
                "updated_at": bwm_shared.now_iso(),
                "sessions": int(len(sessions_df)),
                "trials": int(len(trials_df)),
            },
        )
        _emit(config, f"Build: metadata tables written under {tmp_dir}.")

        _emit(config, "Build: writing packed per-session behavior shards.")
        behavior_stats = _write_behavior_session_shards(
            sessions_dir,
            roster=roster,
            cache_root=config.cache_root,
            jobs=config.jobs,
            verbose=config.verbose,
            resume=config.resume,
        )
        _write_build_state(
            tmp_dir,
            {
                "status": "shards_written",
                "resumed": resumed,
                "updated_at": bwm_shared.now_iso(),
                "sessions_written": int(behavior_stats["sessions_written"]),
                "sessions_skipped": int(behavior_stats.get("sessions_skipped", 0)),
            },
        )
        _emit(
            config,
            "Build: behavior shards complete "
            f"(written={behavior_stats['sessions_written']:,}, skipped={behavior_stats.get('sessions_skipped', 0):,}, "
            f"wheel_present={behavior_stats['wheel_sessions_written']:,}, pose_present={behavior_stats['pose_sessions_written']:,})."
        )

        schema = _build_schema(outputs)
        provenance = _build_provenance(config=config, trials_path=trials_aggregate_path)
        build_report = _build_report(
            config=config,
            sessions_df=sessions_df,
            trials_df=trials_df,
            events_df=events_df,
            wheel_availability_df=wheel_availability_df,
            pose_availability_df=pose_availability_df,
            trial_behavior_features_df=trial_behavior_features_df,
            wheel_trial_features_df=wheel_trial_features_df,
            pose_trial_features_df=pose_trial_features_df,
            event_aligned_behavior_features_df=event_aligned_behavior_features_df,
            behavior_session_features_df=behavior_session_features_df,
            movement_state_epochs_df=movement_state_epochs_df,
            quiescence_state_epochs_df=quiescence_state_epochs_df,
            behavior_state_session_features_df=behavior_state_session_features_df,
            behavior_stats=behavior_stats,
            prefetch_report=prefetch_report,
        )
        summary = _build_summary(
            sessions_df=sessions_df,
            trials_df=trials_df,
            events_df=events_df,
            wheel_availability_df=wheel_availability_df,
            pose_availability_df=pose_availability_df,
            trial_behavior_features_df=trial_behavior_features_df,
            wheel_trial_features_df=wheel_trial_features_df,
            pose_trial_features_df=pose_trial_features_df,
            event_aligned_behavior_features_df=event_aligned_behavior_features_df,
            behavior_session_features_df=behavior_session_features_df,
            movement_state_epochs_df=movement_state_epochs_df,
            quiescence_state_epochs_df=quiescence_state_epochs_df,
            behavior_state_session_features_df=behavior_state_session_features_df,
            behavior_stats=behavior_stats,
            prefetch_report=prefetch_report,
        )

        outputs.schema_path.write_text(yaml.safe_dump(schema, sort_keys=False), encoding="utf-8")
        outputs.provenance_path.write_text(yaml.safe_dump(provenance, sort_keys=False), encoding="utf-8")
        outputs.prefetch_report_path.write_text(yaml.safe_dump(prefetch_report, sort_keys=False), encoding="utf-8")
        outputs.build_report_path.write_text(yaml.safe_dump(build_report, sort_keys=False), encoding="utf-8")
        outputs.summary_path.write_text(summary, encoding="utf-8")
        manifest = bwm_shared.build_manifest(dataset_name=DATASET_NAME, dataset_version=DATASET_VERSION, dataset_dir=tmp_dir)
        outputs.manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        _write_build_state(tmp_dir, {"status": "finalizing", "resumed": resumed, "updated_at": bwm_shared.now_iso()})

        target_dir.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir.rename(target_dir)
        _emit(config, f"Done: behavior dataset built at {target_dir} in {perf_counter() - build_started_at:.1f}s")
    except Exception as exc:
        _write_build_state(
            tmp_dir,
            {"status": "failed", "resumed": resumed, "updated_at": bwm_shared.now_iso(), "error": str(exc)},
        )
        if config.resume:
            _emit(config, f"Build interrupted; preserved partial work in {tmp_dir} for resume.")
        else:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    return BuildOutputs(
        dataset_dir=target_dir,
        sessions_path=target_dir / "metadata" / "sessions.parquet",
        trials_path=target_dir / "metadata" / "trials.parquet",
        events_path=target_dir / "metadata" / "events.parquet",
        wheel_availability_path=target_dir / "metadata" / "wheel_availability.parquet",
        pose_availability_path=target_dir / "metadata" / "pose_availability.parquet",
        trial_behavior_features_path=target_dir / "features" / "trial_behavior_features.parquet",
        wheel_trial_features_path=target_dir / "features" / "wheel_trial_features.parquet",
        pose_trial_features_path=target_dir / "features" / "pose_trial_features.parquet",
        event_aligned_behavior_features_path=target_dir / "features" / "event_aligned_behavior_features.parquet",
        behavior_session_features_path=target_dir / "features" / "behavior_session_features.parquet",
        movement_state_epochs_path=target_dir / "features" / "movement_state_epochs.parquet",
        quiescence_state_epochs_path=target_dir / "features" / "quiescence_state_epochs.parquet",
        behavior_state_session_features_path=target_dir / "features" / "behavior_state_session_features.parquet",
        manifest_path=target_dir / "manifest.json",
        schema_path=target_dir / "schema.yaml",
        provenance_path=target_dir / "provenance.yaml",
        prefetch_report_path=target_dir / "prefetch_report.yaml",
        build_report_path=target_dir / "build_report.yaml",
        summary_path=target_dir / "SUMMARY.md",
        wheel_store_path=target_dir / "sessions",
        pose_store_path=target_dir / "sessions",
    )


def inspect_bwm_behavior_cache(config: BuildConfig, *, roster: pd.DataFrame | None = None) -> dict[str, Any]:
    roster_df = roster.copy() if roster is not None else bwm_simple._load_roster(limit_insertions=config.limit_insertions)
    aggregate_tables = {"trials": bwm_shared.scan_aggregate_table(config.cache_root, "trials")}
    sessions = roster_df[["eid", "subject", "date", "session_number", "lab"]].drop_duplicates("eid")

    wheel_missing: list[dict[str, Any]] = []
    pose_missing: list[dict[str, Any]] = []
    pose_camera_status: list[dict[str, Any]] = []
    for row in sessions.itertuples(index=False):
        session_alf = session_assets.resolve_session_alf_dir(
            config.cache_root,
            lab=str(row.lab),
            subject=str(row.subject),
            date=str(row.date),
            session_number=int(row.session_number),
        )
        if session_alf is None or not session_assets.wheel_assets_present(session_alf):
            wheel_missing.append(bwm_ephys._row_identity(row, key_name="eid"))
        present_cameras = session_assets.pose_cameras_present(session_alf) if session_alf is not None else []
        pose_camera_status.append({**bwm_ephys._row_identity(row, key_name="eid"), "present_cameras": present_cameras})
        if not present_cameras:
            pose_missing.append(bwm_ephys._row_identity(row, key_name="eid"))

    return {
        "generated_at": bwm_shared.now_iso(),
        "selection": {"sessions": int(sessions.shape[0])},
        "aggregate_tables": aggregate_tables,
        "signals": {
            "wheel": {
                "required_sessions": int(sessions.shape[0]),
                "present_sessions": int(sessions.shape[0] - len(wheel_missing)),
                "missing": wheel_missing,
            },
            "pose": {
                "required_sessions": int(sessions.shape[0]),
                "present_sessions": int(sessions.shape[0] - len(pose_missing)),
                "missing": pose_missing,
                "camera_status": pose_camera_status,
            },
        },
    }


def _scan_has_missing_required_assets(scan: dict[str, Any]) -> bool:
    if not scan["aggregate_tables"]["trials"]["present"]:
        return True
    return bool(scan["signals"]["wheel"]["missing"] or scan["signals"]["pose"]["missing"])


def _prefetch_missing_assets(config: BuildConfig, *, scan: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    one_remote = bwm_shared.make_remote_one(config.cache_root)

    state = scan["aggregate_tables"]["trials"]
    if state["present"]:
        actions.append({"kind": "aggregate_table", "name": "trials", "status": "already_present"})
    else:
        try:
            path = bwm_simple._resolve_aggregate_table(config.cache_root, "trials", allow_remote_fetch=True, one_remote=one_remote)
            actions.append({"kind": "aggregate_table", "name": "trials", "status": "fetched", "path": str(path)})
            _emit(config, "Prefetch: fetched aggregate table 'trials'.")
        except Exception as exc:
            actions.append({"kind": "aggregate_table", "name": "trials", "status": "failed", "error": str(exc)})
            _emit(config, f"Prefetch: failed to fetch aggregate table 'trials': {exc}")

    wheel_missing = scan["signals"]["wheel"]["missing"]
    for index, item in enumerate(wheel_missing, start=1):
        try:
            bwm_shared.prefetch_wheel(one_remote, eid=item["eid"])
            actions.append({"kind": "wheel", "eid": item["eid"], "status": "fetched"})
            if _should_emit_progress(index, len(wheel_missing)):
                _emit(config, f"Prefetch wheel: {index}/{len(wheel_missing)} sessions processed; latest={item['eid']} status=fetched")
        except Exception as exc:
            actions.append({"kind": "wheel", "eid": item["eid"], "status": "failed", "error": str(exc)})
            _emit(config, f"Prefetch wheel: {index}/{len(wheel_missing)} sessions processed; latest={item['eid']} status=failed error={exc}")

    pose_missing = scan["signals"]["pose"]["missing"]
    for index, item in enumerate(pose_missing, start=1):
        try:
            bwm_shared.prefetch_pose(one_remote, eid=item["eid"])
            actions.append({"kind": "pose", "eid": item["eid"], "status": "fetched"})
            if _should_emit_progress(index, len(pose_missing)):
                _emit(config, f"Prefetch pose: {index}/{len(pose_missing)} sessions processed; latest={item['eid']} status=fetched")
        except Exception as exc:
            actions.append({"kind": "pose", "eid": item['eid'], "status": "failed", "error": str(exc)})
            _emit(config, f"Prefetch pose: {index}/{len(pose_missing)} sessions processed; latest={item['eid']} status=failed error={exc}")
    return actions


def _compute_trial_behavior_features(trials_df: pd.DataFrame) -> pd.DataFrame:
    features = trials_df[[col for col in [
        'eid', 'trial_id', 'choice', 'feedbackType', 'probabilityLeft', 'contrastLeft', 'contrastRight',
        'stimOn_times', 'firstMovement_times', 'feedback_times', 'response_times', 'bwm_include'
    ] if col in trials_df.columns]].copy()
    if 'contrastRight' in features.columns and 'contrastLeft' in features.columns:
        features['signed_contrast'] = pd.to_numeric(features['contrastRight'], errors='coerce').fillna(0.0) - pd.to_numeric(features['contrastLeft'], errors='coerce').fillna(0.0)
    else:
        features['signed_contrast'] = np.nan
    if 'choice' in features.columns:
        choice_num = pd.to_numeric(features['choice'], errors='coerce')
        features['choice_label'] = np.where(choice_num > 0, 'right', np.where(choice_num < 0, 'left', 'other'))
    else:
        features['choice_label'] = 'unknown'
    if 'feedbackType' in features.columns:
        feedback_num = pd.to_numeric(features['feedbackType'], errors='coerce')
        features['correct'] = (feedback_num > 0).astype(bool)
    else:
        features['correct'] = False
    if 'firstMovement_times' in features.columns and 'stimOn_times' in features.columns:
        features['reaction_time'] = pd.to_numeric(features['firstMovement_times'], errors='coerce') - pd.to_numeric(features['stimOn_times'], errors='coerce')
    else:
        features['reaction_time'] = np.nan
    if 'response_times' in features.columns and 'firstMovement_times' in features.columns:
        features['movement_time'] = pd.to_numeric(features['response_times'], errors='coerce') - pd.to_numeric(features['firstMovement_times'], errors='coerce')
    else:
        features['movement_time'] = np.nan
    if 'feedback_times' in features.columns and 'stimOn_times' in features.columns:
        features['stim_to_feedback_time'] = pd.to_numeric(features['feedback_times'], errors='coerce') - pd.to_numeric(features['stimOn_times'], errors='coerce')
    else:
        features['stim_to_feedback_time'] = np.nan
    for col in ('signed_contrast', 'reaction_time', 'movement_time', 'stim_to_feedback_time'):
        features[col] = pd.to_numeric(features[col], errors='coerce').astype(np.float32)
    return features[['eid', 'trial_id', 'signed_contrast', 'choice_label', 'correct', 'reaction_time', 'movement_time', 'stim_to_feedback_time']]


def _wheel_detector_version() -> str:
    for dist_name in ("ibllib", "brainbox-ibl", "brainbox"):
        try:
            return str(importlib_metadata.version(dist_name))
        except importlib_metadata.PackageNotFoundError:
            continue
    return "unknown"


def _detect_wheel_state_rows(
    *,
    eid: str,
    timestamps: np.ndarray,
    position: np.ndarray,
    quiescence_min_duration_s: float = QUIESCENCE_MIN_DURATION_S,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    movement_rows: list[dict[str, Any]] = []
    quiescence_rows: list[dict[str, Any]] = []
    session_summary = {
        "eid": str(eid),
        "wheel_present": False,
        "movement_epoch_count": 0,
        "quiescence_epoch_count": 0,
        "fraction_time_moving": np.nan,
        "fraction_time_quiescent": np.nan,
        "median_movement_duration": np.nan,
        "median_quiescence_duration": np.nan,
    }
    wheel_times = np.asarray(timestamps, dtype=np.float64)
    wheel_position = np.asarray(position, dtype=np.float64)
    # Compressed/native wheel traces can contain repeated timestamps after codec
    # reconstruction; the canonical IBL detector still handles these correctly.
    if wheel_times.size < 2 or wheel_position.shape != wheel_times.shape or np.any(np.diff(wheel_times) < 0):
        return movement_rows, quiescence_rows, session_summary

    session_summary["wheel_present"] = True
    try:
        from ibllib.io.extractors.training_wheel import extract_wheel_moves

        wheel_moves = extract_wheel_moves(wheel_times, wheel_position, display=False)
    except Exception:
        return movement_rows, quiescence_rows, session_summary

    intervals = np.asarray(wheel_moves.get("intervals", []), dtype=np.float64)
    peak_amplitudes = np.asarray(wheel_moves.get("peakAmplitude", []), dtype=np.float64)
    peak_velocity_times = np.asarray(wheel_moves.get("peakVelocity_times", []), dtype=np.float64)
    detector_version = _wheel_detector_version()

    durations: list[float] = []
    if intervals.ndim == 2 and intervals.shape[1] == 2:
        for movement_id, (interval, peak_amplitude, peak_velocity_time) in enumerate(
            zip(intervals, peak_amplitudes, peak_velocity_times),
            start=1,
        ):
            t_start = float(interval[0])
            t_end = float(interval[1])
            duration_s = t_end - t_start
            if not (np.isfinite(t_start) and np.isfinite(t_end) and duration_s > 0):
                continue
            durations.append(duration_s)
            movement_rows.append(
                {
                    "eid": str(eid),
                    "movement_id": int(movement_id),
                    "t_start": t_start,
                    "t_end": t_end,
                    "duration_s": duration_s,
                    "peak_amplitude": float(peak_amplitude) if np.isfinite(peak_amplitude) else np.nan,
                    "peak_velocity_time": float(peak_velocity_time) if np.isfinite(peak_velocity_time) else np.nan,
                    "source_signal": "wheel",
                    "detector_name": WHEEL_STATE_DETECTOR_NAME,
                    "detector_version": detector_version,
                }
            )

    session_start = float(wheel_times[0])
    session_end = float(wheel_times[-1])
    valid_intervals = [(row["t_start"], row["t_end"]) for row in movement_rows]
    cursor = session_start
    quiescence_durations: list[float] = []
    quiescence_id = 1
    for start, end in valid_intervals:
        gap_start = cursor
        gap_end = start
        gap_duration = gap_end - gap_start
        if np.isfinite(gap_duration) and gap_duration >= quiescence_min_duration_s:
            quiescence_durations.append(gap_duration)
            quiescence_rows.append(
                {
                    "eid": str(eid),
                    "quiescence_id": int(quiescence_id),
                    "t_start": float(gap_start),
                    "t_end": float(gap_end),
                    "duration_s": float(gap_duration),
                    "derived_from": WHEEL_STATE_DETECTOR_NAME,
                    "min_duration_s": float(quiescence_min_duration_s),
                }
            )
            quiescence_id += 1
        cursor = max(cursor, end)
    tail_duration = session_end - cursor
    if np.isfinite(tail_duration) and tail_duration >= quiescence_min_duration_s:
        quiescence_durations.append(tail_duration)
        quiescence_rows.append(
            {
                "eid": str(eid),
                "quiescence_id": int(quiescence_id),
                "t_start": float(cursor),
                "t_end": float(session_end),
                "duration_s": float(tail_duration),
                "derived_from": WHEEL_STATE_DETECTOR_NAME,
                "min_duration_s": float(quiescence_min_duration_s),
            }
        )

    observed_duration = session_end - session_start
    total_movement_duration = float(np.sum(durations, dtype=np.float64)) if durations else 0.0
    total_quiescence_duration = float(np.sum(quiescence_durations, dtype=np.float64)) if quiescence_durations else 0.0
    session_summary.update(
        {
            "movement_epoch_count": int(len(movement_rows)),
            "quiescence_epoch_count": int(len(quiescence_rows)),
            "fraction_time_moving": (total_movement_duration / observed_duration if observed_duration > 0 else np.nan),
            "fraction_time_quiescent": (total_quiescence_duration / observed_duration if observed_duration > 0 else np.nan),
            "median_movement_duration": (float(np.median(durations)) if durations else np.nan),
            "median_quiescence_duration": (float(np.median(quiescence_durations)) if quiescence_durations else np.nan),
        }
    )
    return movement_rows, quiescence_rows, session_summary


def _summarize_wheel_window(*, timestamps: np.ndarray, position: np.ndarray, velocity: np.ndarray | None, start: float, end: float) -> dict[str, float | str]:
    if not np.isfinite(start) or not np.isfinite(end) or end <= start:
        return {'movement_direction': 'unknown', 'movement_amplitude': float('nan'), 'mean_velocity': float('nan'), 'max_velocity': float('nan')}
    mask = (timestamps >= start) & (timestamps <= end)
    if mask.sum() < 2:
        return {'movement_direction': 'unknown', 'movement_amplitude': float('nan'), 'mean_velocity': float('nan'), 'max_velocity': float('nan')}
    pos = np.asarray(position[mask], dtype=float)
    amp = float(pos[-1] - pos[0])
    if velocity is not None and velocity.shape == timestamps.shape:
        vel = np.asarray(velocity[mask], dtype=float)
    else:
        window_times = np.asarray(timestamps[mask], dtype=float)
        vel = np.gradient(pos, window_times) if np.all(np.diff(window_times) > 0) else np.full(pos.shape, np.nan, dtype=float)
    mean_velocity = float(np.nanmean(vel)) if vel.size else float('nan')
    max_velocity = float(np.nanmax(np.abs(vel))) if vel.size else float('nan')
    direction = 'right' if amp > 0 else ('left' if amp < 0 else 'none')
    return {'movement_direction': direction, 'movement_amplitude': amp, 'mean_velocity': mean_velocity, 'max_velocity': max_velocity}


def _trial_groups_by_eid(trials_df: pd.DataFrame, *, columns: list[str] | None = None) -> dict[str, pd.DataFrame]:
    frame = trials_df[columns].copy() if columns is not None else trials_df.copy()
    if frame.empty:
        return {}
    if 'eid' in frame.columns:
        frame['eid'] = frame['eid'].astype(str)
    return {str(eid): group.copy() for eid, group in frame.groupby('eid', sort=False)}


def _session_behavior_result(*, row: Any, trial_group: pd.DataFrame, cache_root: Path) -> dict[str, Any]:
    wheel = _prepare_wheel_payload(row=row, cache_root=cache_root)
    pose = _prepare_pose_payload(row=row, cache_root=cache_root)

    wheel_availability = {
        'eid': str(row.eid),
        'wheel_present': False,
        'n_samples': 0,
        't_start': np.nan,
        't_end': np.nan,
    }
    wheel_features: list[dict[str, Any]] = []
    movement_state_epochs: list[dict[str, Any]] = []
    quiescence_state_epochs: list[dict[str, Any]] = []
    behavior_state_session_features = {
        'eid': str(row.eid),
        'wheel_present': False,
        'movement_epoch_count': 0,
        'quiescence_epoch_count': 0,
        'fraction_time_moving': np.nan,
        'fraction_time_quiescent': np.nan,
        'median_movement_duration': np.nan,
        'median_quiescence_duration': np.nan,
    }
    if wheel.get('status') == 'ok':
        timestamps = np.asarray(wheel['timestamps'], dtype=np.float64)
        position = np.asarray(wheel['position'], dtype=np.float64)
        velocity = np.asarray(wheel['velocity'], dtype=np.float32) if 'velocity' in wheel else None
        wheel_availability = {
            'eid': str(row.eid),
            'wheel_present': True,
            'n_samples': int(timestamps.size),
            't_start': float(timestamps[0]) if timestamps.size else np.nan,
            't_end': float(timestamps[-1]) if timestamps.size else np.nan,
        }
        movement_state_epochs, quiescence_state_epochs, behavior_state_session_features = _detect_wheel_state_rows(
            eid=str(row.eid),
            timestamps=timestamps,
            position=position,
        )
        for trial in trial_group.itertuples(index=False):
            stim = float(getattr(trial, 'stimOn_times', np.nan)) if hasattr(trial, 'stimOn_times') else np.nan
            move = float(getattr(trial, 'firstMovement_times', np.nan)) if hasattr(trial, 'firstMovement_times') else np.nan
            resp = float(getattr(trial, 'response_times', np.nan)) if hasattr(trial, 'response_times') else np.nan
            start = stim if np.isfinite(stim) else move
            end = resp if np.isfinite(resp) else move
            if np.isfinite(start) and np.isfinite(end) and end < start and np.isfinite(move):
                end = move
            stats = _summarize_wheel_window(timestamps=timestamps, position=position, velocity=velocity, start=start, end=end)
            wheel_features.append({
                'eid': str(row.eid),
                'trial_id': int(getattr(trial, 'trial_id')),
                'window_spec': 'stimOn:response',
                'wheel_present': True,
                'movement_onset_time': move if np.isfinite(move) else np.nan,
                'movement_peak_time': resp if np.isfinite(resp) else np.nan,
                **stats,
            })

    pose_availability: list[dict[str, Any]] = []
    pose_features: list[dict[str, Any]] = []
    event_features: list[dict[str, Any]] = []
    if pose.get('status') == 'ok':
        for camera_name, camera in pose['cameras'].items():
            timestamps = np.asarray(camera['timestamps'], dtype=np.float64)
            features = np.asarray(camera['features'], dtype=np.float32)
            pose_availability.append({
                'eid': str(row.eid),
                'camera': camera_name,
                'pose_present': True,
                'n_frames': int(timestamps.size),
                't_start': float(timestamps[0]) if timestamps.size else np.nan,
                't_end': float(timestamps[-1]) if timestamps.size else np.nan,
                'tracker': camera.get('tracker'),
            })
            mag = np.nanmean(np.abs(features.astype(float, copy=False)), axis=1) if features.size else np.asarray([], dtype=float)
            prepared_mag = _prepare_event_aligned_signal(timestamps=timestamps, values=mag)
            for trial in trial_group.itertuples(index=False):
                stim = float(getattr(trial, 'stimOn_times', np.nan)) if hasattr(trial, 'stimOn_times') else np.nan
                fb = float(getattr(trial, 'feedback_times', np.nan)) if hasattr(trial, 'feedback_times') else np.nan
                stats = _summarize_pose_window(timestamps=timestamps, features=features, start=stim, end=(fb if np.isfinite(fb) else stim))
                pose_features.append({
                    'eid': str(row.eid),
                    'trial_id': int(getattr(trial, 'trial_id')),
                    'camera': camera_name,
                    'window_spec': 'stimOn:feedback',
                    'pose_present': True,
                    'feature_mean': stats['feature_mean'],
                    'feature_peak': stats['feature_peak'],
                })
                for event_name, source_col in EVENT_COLUMNS:
                    if not hasattr(trial, source_col):
                        continue
                    event_time = float(getattr(trial, source_col, np.nan))
                    if not np.isfinite(event_time):
                        continue
                    summary = _event_aligned_signal_summary_prepared(prepared=prepared_mag, event_time=event_time)
                    event_features.append({
                        'eid': str(row.eid),
                        'trial_id': int(getattr(trial, 'trial_id')),
                        'signal_name': str(camera_name),
                        'event_name': str(event_name),
                        'window_spec': BEHAVIOR_EVENT_WINDOW_SPEC,
                        **summary,
                    })
    else:
        pose_availability.append({'eid': str(row.eid), 'camera': '', 'pose_present': False, 'n_frames': 0, 't_start': np.nan, 't_end': np.nan, 'tracker': None})

    if wheel.get('status') == 'ok':
        wheel_values = np.asarray(wheel.get('velocity', wheel['position']), dtype=float)
        wheel_times = np.asarray(wheel['timestamps'], dtype=float)
        prepared_wheel = _prepare_event_aligned_signal(timestamps=wheel_times, values=wheel_values)
        for trial in trial_group.itertuples(index=False):
            for event_name, source_col in EVENT_COLUMNS:
                if not hasattr(trial, source_col):
                    continue
                event_time = float(getattr(trial, source_col, np.nan))
                if not np.isfinite(event_time):
                    continue
                summary = _event_aligned_signal_summary_prepared(prepared=prepared_wheel, event_time=event_time)
                event_features.append({
                    'eid': str(row.eid),
                    'trial_id': int(getattr(trial, 'trial_id')),
                    'signal_name': 'wheel',
                    'event_name': str(event_name),
                    'window_spec': BEHAVIOR_EVENT_WINDOW_SPEC,
                    **summary,
                })

    return {
        'eid': str(row.eid),
        'wheel_availability': wheel_availability,
        'pose_availability': pose_availability,
        'wheel_features': wheel_features,
        'pose_features': pose_features,
        'event_features': event_features,
        'movement_state_epochs': movement_state_epochs,
        'quiescence_state_epochs': quiescence_state_epochs,
        'behavior_state_session_features': behavior_state_session_features,
    }


def _build_behavior_feature_tables(*, sessions_df: pd.DataFrame, trials_df: pd.DataFrame, cache_root: Path, jobs: int = DEFAULT_BUILD_JOBS, verbose: bool = True) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    session_rows = sessions_df[['eid', 'subject', 'date', 'session_number', 'lab']].drop_duplicates('eid')
    trial_columns = [col for col in ['eid', 'trial_id', 'stimOn_times', 'goCue_times', 'firstMovement_times', 'response_times', 'feedback_times'] if col in trials_df.columns]
    trial_groups = _trial_groups_by_eid(trials_df, columns=trial_columns)

    wheel_availability_rows: list[dict[str, Any]] = []
    pose_availability_rows: list[dict[str, Any]] = []
    wheel_feature_rows: list[dict[str, Any]] = []
    pose_feature_rows: list[dict[str, Any]] = []
    event_feature_rows: list[dict[str, Any]] = []
    movement_state_epoch_rows: list[dict[str, Any]] = []
    quiescence_state_epoch_rows: list[dict[str, Any]] = []
    behavior_state_session_feature_rows: list[dict[str, Any]] = []

    total = int(len(session_rows))
    started_at = perf_counter()
    completed = 0

    def _record(result: dict[str, Any]) -> None:
        wheel_availability_rows.append(result['wheel_availability'])
        pose_availability_rows.extend(result['pose_availability'])
        wheel_feature_rows.extend(result['wheel_features'])
        pose_feature_rows.extend(result['pose_features'])
        event_feature_rows.extend(result['event_features'])
        movement_state_epoch_rows.extend(result['movement_state_epochs'])
        quiescence_state_epoch_rows.extend(result['quiescence_state_epochs'])
        behavior_state_session_feature_rows.append(result['behavior_state_session_features'])

    if max(1, jobs) == 1:
        for row in session_rows.itertuples(index=False):
            result = _session_behavior_result(row=row, trial_group=trial_groups.get(str(row.eid), pd.DataFrame(columns=trial_columns)), cache_root=cache_root)
            _record(result)
            completed += 1
            if verbose and _should_emit_progress(completed, total):
                print(_behavior_feature_progress_line('metadata session features', completed, total, started_at, wheel_rows=len(wheel_feature_rows), pose_rows=len(pose_feature_rows), event_rows=len(event_feature_rows)))
    else:
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as executor:
            futures = {
                executor.submit(
                    _session_behavior_result,
                    row=row,
                    trial_group=trial_groups.get(str(row.eid), pd.DataFrame(columns=trial_columns)),
                    cache_root=cache_root,
                ): str(row.eid)
                for row in session_rows.itertuples(index=False)
            }
            pending = set(futures)
            while pending:
                done, pending = wait(pending, timeout=FEATURE_PROGRESS_INTERVAL_S, return_when=FIRST_COMPLETED)
                if not done:
                    if verbose:
                        print(_behavior_feature_progress_line('metadata session features', completed, total, started_at, wheel_rows=len(wheel_feature_rows), pose_rows=len(pose_feature_rows), event_rows=len(event_feature_rows)))
                    continue
                for future in done:
                    _record(future.result())
                    completed += 1
                if verbose:
                    print(_behavior_feature_progress_line('metadata session features', completed, total, started_at, wheel_rows=len(wheel_feature_rows), pose_rows=len(pose_feature_rows), event_rows=len(event_feature_rows), current=futures[next(iter(done))]))

    wheel_availability = pd.DataFrame(wheel_availability_rows) if wheel_availability_rows else pd.DataFrame(columns=['eid', 'wheel_present', 'n_samples', 't_start', 't_end'])
    if not wheel_availability.empty:
        wheel_availability['n_samples'] = wheel_availability['n_samples'].astype(np.int32)
        wheel_availability['wheel_present'] = wheel_availability['wheel_present'].astype(bool)
        for col in ('t_start', 't_end'):
            wheel_availability[col] = pd.to_numeric(wheel_availability[col], errors='coerce').astype(np.float32)

    pose_availability = pd.DataFrame(pose_availability_rows) if pose_availability_rows else pd.DataFrame(columns=['eid', 'camera', 'pose_present', 'n_frames', 't_start', 't_end', 'tracker'])
    if not pose_availability.empty:
        pose_availability['n_frames'] = pose_availability['n_frames'].astype(np.int32)
        pose_availability['pose_present'] = pose_availability['pose_present'].astype(bool)
        for col in ('t_start', 't_end'):
            pose_availability[col] = pd.to_numeric(pose_availability[col], errors='coerce').astype(np.float32)
        pose_availability['tracker'] = pose_availability['tracker'].astype(object)

    wheel_features = pd.DataFrame(wheel_feature_rows) if wheel_feature_rows else pd.DataFrame(columns=['eid', 'trial_id', 'window_spec', 'wheel_present', 'movement_onset_time', 'movement_peak_time', 'movement_direction', 'movement_amplitude', 'mean_velocity', 'max_velocity'])
    if not wheel_features.empty:
        wheel_features['trial_id'] = wheel_features['trial_id'].astype(np.int32)
        wheel_features['wheel_present'] = wheel_features['wheel_present'].astype(bool)
        for col in ('movement_onset_time', 'movement_peak_time', 'movement_amplitude', 'mean_velocity', 'max_velocity'):
            wheel_features[col] = pd.to_numeric(wheel_features[col], errors='coerce').astype(np.float32)

    pose_features = pd.DataFrame(pose_feature_rows) if pose_feature_rows else pd.DataFrame(columns=['eid', 'trial_id', 'camera', 'window_spec', 'pose_present', 'feature_mean', 'feature_peak'])
    if not pose_features.empty:
        pose_features['trial_id'] = pose_features['trial_id'].astype(np.int32)
        pose_features['pose_present'] = pose_features['pose_present'].astype(bool)
        for col in ('feature_mean', 'feature_peak'):
            pose_features[col] = pd.to_numeric(pose_features[col], errors='coerce').astype(np.float32)

    event_features = pd.DataFrame(event_feature_rows) if event_feature_rows else pd.DataFrame(columns=['eid', 'trial_id', 'signal_name', 'event_name', 'window_spec', 'baseline', 'peak', 'peak_latency_ms', 'mean_response', 'modulation_index'])
    if not event_features.empty:
        event_features['trial_id'] = event_features['trial_id'].astype(np.int32)
        for col in ('baseline', 'peak', 'peak_latency_ms', 'mean_response', 'modulation_index'):
            event_features[col] = pd.to_numeric(event_features[col], errors='coerce').astype(np.float32)

    movement_state_epochs = pd.DataFrame(movement_state_epoch_rows) if movement_state_epoch_rows else pd.DataFrame(columns=['eid', 'movement_id', 't_start', 't_end', 'duration_s', 'peak_amplitude', 'peak_velocity_time', 'source_signal', 'detector_name', 'detector_version'])
    if not movement_state_epochs.empty:
        movement_state_epochs['movement_id'] = movement_state_epochs['movement_id'].astype(np.int32)
        for col in ('t_start', 't_end', 'duration_s', 'peak_amplitude', 'peak_velocity_time'):
            movement_state_epochs[col] = pd.to_numeric(movement_state_epochs[col], errors='coerce').astype(np.float32)

    quiescence_state_epochs = pd.DataFrame(quiescence_state_epoch_rows) if quiescence_state_epoch_rows else pd.DataFrame(columns=['eid', 'quiescence_id', 't_start', 't_end', 'duration_s', 'derived_from', 'min_duration_s'])
    if not quiescence_state_epochs.empty:
        quiescence_state_epochs['quiescence_id'] = quiescence_state_epochs['quiescence_id'].astype(np.int32)
        for col in ('t_start', 't_end', 'duration_s', 'min_duration_s'):
            quiescence_state_epochs[col] = pd.to_numeric(quiescence_state_epochs[col], errors='coerce').astype(np.float32)

    behavior_state_session_features = pd.DataFrame(behavior_state_session_feature_rows) if behavior_state_session_feature_rows else pd.DataFrame(columns=['eid', 'wheel_present', 'movement_epoch_count', 'quiescence_epoch_count', 'fraction_time_moving', 'fraction_time_quiescent', 'median_movement_duration', 'median_quiescence_duration'])
    if not behavior_state_session_features.empty:
        behavior_state_session_features['wheel_present'] = behavior_state_session_features['wheel_present'].fillna(False).astype(bool)
        for col in ('movement_epoch_count', 'quiescence_epoch_count'):
            behavior_state_session_features[col] = pd.to_numeric(behavior_state_session_features[col], errors='coerce').fillna(0).astype(np.int32)
        for col in ('fraction_time_moving', 'fraction_time_quiescent', 'median_movement_duration', 'median_quiescence_duration'):
            behavior_state_session_features[col] = pd.to_numeric(behavior_state_session_features[col], errors='coerce').astype(np.float32)

    return wheel_availability, pose_availability, wheel_features, pose_features, event_features, movement_state_epochs, quiescence_state_epochs, behavior_state_session_features


def _build_wheel_trial_features(*, sessions_df: pd.DataFrame, trials_df: pd.DataFrame, cache_root: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    availability, _, features, _, _, _, _, _ = _build_behavior_feature_tables(
        sessions_df=sessions_df,
        trials_df=trials_df,
        cache_root=cache_root,
        jobs=1,
        verbose=False,
    )
    return availability, features


def _summarize_pose_window(*, timestamps: np.ndarray, features: np.ndarray, start: float, end: float) -> dict[str, float]:
    if not np.isfinite(start) or not np.isfinite(end) or end <= start:
        return {'feature_mean': float('nan'), 'feature_peak': float('nan')}
    mask = (timestamps >= start) & (timestamps <= end)
    if mask.sum() == 0 or features.size == 0:
        return {'feature_mean': float('nan'), 'feature_peak': float('nan')}
    window = np.asarray(features[mask], dtype=float)
    if window.ndim == 1:
        window = window.reshape(-1, 1)
    mag = np.nanmean(np.abs(window), axis=1)
    return {
        'feature_mean': float(np.nanmean(mag)) if mag.size else float('nan'),
        'feature_peak': float(np.nanmax(mag)) if mag.size else float('nan'),
    }


def _build_pose_trial_features(*, sessions_df: pd.DataFrame, trials_df: pd.DataFrame, cache_root: Path) -> pd.DataFrame:
    _, _, _, df, _, _, _, _ = _build_behavior_feature_tables(
        sessions_df=sessions_df,
        trials_df=trials_df,
        cache_root=cache_root,
        jobs=1,
        verbose=False,
    )
    return df


def _build_pose_availability(sessions_df: pd.DataFrame, *, cache_root: Path) -> pd.DataFrame:
    empty_trials = pd.DataFrame(columns=['eid', 'trial_id', 'stimOn_times', 'goCue_times', 'firstMovement_times', 'response_times', 'feedback_times'])
    _, df, _, _, _, _, _, _ = _build_behavior_feature_tables(
        sessions_df=sessions_df,
        trials_df=empty_trials,
        cache_root=cache_root,
        jobs=1,
        verbose=False,
    )
    return df


def _event_aligned_signal_summary(*, timestamps: np.ndarray, values: np.ndarray, event_time: float, pre: float = 0.2, post: float = 0.3) -> dict[str, float]:
    prepared = _prepare_event_aligned_signal(values=values, timestamps=timestamps)
    return _event_aligned_signal_summary_prepared(prepared=prepared, event_time=event_time, pre=pre, post=post)


def _prepare_event_aligned_signal(*, timestamps: np.ndarray, values: np.ndarray) -> dict[str, np.ndarray]:
    time_index = np.asarray(timestamps, dtype=np.float64)
    signal = np.asarray(values, dtype=np.float64)
    finite_signal = np.where(np.isfinite(signal), signal, 0.0)
    finite_counts = np.isfinite(signal).astype(np.int32, copy=False)
    return {
        'timestamps': time_index,
        'values': signal,
        'prefix_sum': np.concatenate(([0.0], np.cumsum(finite_signal, dtype=np.float64))),
        'prefix_count': np.concatenate(([0], np.cumsum(finite_counts, dtype=np.int64))),
    }


def _event_aligned_signal_summary_prepared(*, prepared: dict[str, np.ndarray], event_time: float, pre: float = 0.2, post: float = 0.3) -> dict[str, float]:
    if not np.isfinite(event_time):
        return {'baseline': float('nan'), 'peak': float('nan'), 'peak_latency_ms': float('nan'), 'mean_response': float('nan'), 'modulation_index': float('nan')}
    time_index = prepared['timestamps']
    signal = prepared['values']
    if time_index.size == 0 or signal.size == 0 or time_index.shape[0] != signal.shape[0]:
        return {'baseline': float('nan'), 'peak': float('nan'), 'peak_latency_ms': float('nan'), 'mean_response': float('nan'), 'modulation_index': float('nan')}
    baseline_start = int(np.searchsorted(time_index, event_time - pre, side='left'))
    baseline_end = int(np.searchsorted(time_index, event_time, side='left'))
    response_start = int(np.searchsorted(time_index, event_time, side='left'))
    response_end = int(np.searchsorted(time_index, event_time + post, side='right'))
    if baseline_end <= baseline_start or response_end <= response_start:
        return {'baseline': float('nan'), 'peak': float('nan'), 'peak_latency_ms': float('nan'), 'mean_response': float('nan'), 'modulation_index': float('nan')}
    prefix_sum = prepared['prefix_sum']
    prefix_count = prepared['prefix_count']
    baseline_count = int(prefix_count[baseline_end] - prefix_count[baseline_start])
    response_count = int(prefix_count[response_end] - prefix_count[response_start])
    if baseline_count <= 0 or response_count <= 0:
        return {'baseline': float('nan'), 'peak': float('nan'), 'peak_latency_ms': float('nan'), 'mean_response': float('nan'), 'modulation_index': float('nan')}

    baseline = float((prefix_sum[baseline_end] - prefix_sum[baseline_start]) / baseline_count)
    mean_response = float((prefix_sum[response_end] - prefix_sum[response_start]) / response_count)
    response_vals = signal[response_start:response_end]
    if response_vals.size == 0 or np.isnan(response_vals).all():
        return {'baseline': baseline, 'peak': float('nan'), 'peak_latency_ms': float('nan'), 'mean_response': mean_response, 'modulation_index': float('nan')}
    response_times = time_index[response_start:response_end]
    peak_idx = int(np.nanargmax(response_vals))
    peak = float(response_vals[peak_idx])
    peak_latency_ms = float((response_times[peak_idx] - event_time) * 1000.0)
    denom = peak + baseline
    modulation_index = float((peak - baseline) / denom) if np.isfinite(denom) and abs(denom) > 1e-12 else float('nan')
    return {'baseline': baseline, 'peak': peak, 'peak_latency_ms': peak_latency_ms, 'mean_response': mean_response, 'modulation_index': modulation_index}


def _build_event_aligned_behavior_features(*, sessions_df: pd.DataFrame, trials_df: pd.DataFrame, cache_root: Path) -> pd.DataFrame:
    _, _, _, _, df, _, _, _ = _build_behavior_feature_tables(
        sessions_df=sessions_df,
        trials_df=trials_df,
        cache_root=cache_root,
        jobs=1,
        verbose=False,
    )
    return df


def _build_behavior_session_features(*, sessions_df: pd.DataFrame, trial_behavior_features_df: pd.DataFrame, wheel_availability_df: pd.DataFrame, pose_availability_df: pd.DataFrame) -> pd.DataFrame:
    features = sessions_df[['eid', 'n_trials', 'n_included_trials']].drop_duplicates('eid').copy()
    tb = trial_behavior_features_df.copy()
    performance = tb.groupby('eid')['correct'].mean().rename('performance') if not tb.empty else pd.Series(dtype=float)
    median_rt = tb.groupby('eid')['reaction_time'].median().rename('median_reaction_time') if not tb.empty else pd.Series(dtype=float)
    median_mt = tb.groupby('eid')['movement_time'].median().rename('median_movement_time') if not tb.empty else pd.Series(dtype=float)
    wheel_present = wheel_availability_df.groupby('eid')['wheel_present'].max().rename('wheel_present') if not wheel_availability_df.empty else pd.Series(dtype=bool)
    pose_present = pose_availability_df.groupby('eid')['pose_present'].max().rename('pose_present') if not pose_availability_df.empty else pd.Series(dtype=bool)
    for series in (performance, median_rt, median_mt, wheel_present, pose_present):
        if not series.empty:
            features = features.merge(series, on='eid', how='left')
    features['performance'] = pd.to_numeric(features.get('performance', np.nan), errors='coerce').astype(np.float32)
    for col in ('median_reaction_time', 'median_movement_time'):
        if col not in features.columns:
            features[col] = np.nan
        features[col] = pd.to_numeric(features[col], errors='coerce').astype(np.float32)
    for col in ('wheel_present', 'pose_present'):
        if col not in features.columns:
            features[col] = False
        features[col] = features[col].fillna(False).astype(bool)
    return features[['eid', 'n_trials', 'n_included_trials', 'performance', 'median_reaction_time', 'median_movement_time', 'wheel_present', 'pose_present']]


def _build_sessions(roster: pd.DataFrame, trials_df: pd.DataFrame) -> pd.DataFrame:
    sessions = roster[["eid", "subject", "date", "session_number", "lab"]].drop_duplicates("eid").copy()
    n_trials = trials_df.groupby("eid").size().rename("n_trials").astype(np.int32)
    n_included = trials_df.loc[trials_df["bwm_include"]].groupby("eid").size().rename("n_included_trials").astype(np.int32)
    sessions = sessions.merge(n_trials, on="eid", how="left")
    sessions = sessions.merge(n_included, on="eid", how="left")
    for col in ("n_trials", "n_included_trials"):
        sessions[col] = sessions[col].fillna(0).astype(np.int32)
    return sessions


def _write_metadata_tables(*, metadata_dir: Path, features_dir: Path, sessions_df: pd.DataFrame, trials_df: pd.DataFrame, events_df: pd.DataFrame, wheel_availability_df: pd.DataFrame, pose_availability_df: pd.DataFrame, trial_behavior_features_df: pd.DataFrame, wheel_trial_features_df: pd.DataFrame, pose_trial_features_df: pd.DataFrame, event_aligned_behavior_features_df: pd.DataFrame, behavior_session_features_df: pd.DataFrame, movement_state_epochs_df: pd.DataFrame, quiescence_state_epochs_df: pd.DataFrame, behavior_state_session_features_df: pd.DataFrame, dataset_dir: Path) -> BuildOutputs:
    sessions_path = metadata_dir / "sessions.parquet"
    trials_path = metadata_dir / "trials.parquet"
    events_path = metadata_dir / "events.parquet"
    wheel_availability_path = metadata_dir / 'wheel_availability.parquet'
    pose_availability_path = metadata_dir / 'pose_availability.parquet'
    trial_behavior_features_path = features_dir / 'trial_behavior_features.parquet'
    wheel_trial_features_path = features_dir / 'wheel_trial_features.parquet'
    behavior_session_features_path = features_dir / 'behavior_session_features.parquet'
    pose_trial_features_path = features_dir / 'pose_trial_features.parquet'
    event_aligned_behavior_features_path = features_dir / 'event_aligned_behavior_features.parquet'
    movement_state_epochs_path = features_dir / 'movement_state_epochs.parquet'
    quiescence_state_epochs_path = features_dir / 'quiescence_state_epochs.parquet'
    behavior_state_session_features_path = features_dir / 'behavior_state_session_features.parquet'
    for frame, path in ((sessions_df, sessions_path), (trials_df, trials_path), (events_df, events_path), (wheel_availability_df, wheel_availability_path), (pose_availability_df, pose_availability_path), (trial_behavior_features_df, trial_behavior_features_path), (wheel_trial_features_df, wheel_trial_features_path), (pose_trial_features_df, pose_trial_features_path), (event_aligned_behavior_features_df, event_aligned_behavior_features_path), (behavior_session_features_df, behavior_session_features_path), (movement_state_epochs_df, movement_state_epochs_path), (quiescence_state_epochs_df, quiescence_state_epochs_path), (behavior_state_session_features_df, behavior_state_session_features_path)):
        frame.to_parquet(path, engine=PARQUET_ENGINE, compression=PARQUET_COMPRESSION, index=False)
    return BuildOutputs(
        dataset_dir=dataset_dir,
        sessions_path=sessions_path,
        trials_path=trials_path,
        events_path=events_path,
        wheel_availability_path=wheel_availability_path,
        pose_availability_path=pose_availability_path,
        trial_behavior_features_path=trial_behavior_features_path,
        wheel_trial_features_path=wheel_trial_features_path,
        pose_trial_features_path=pose_trial_features_path,
        event_aligned_behavior_features_path=event_aligned_behavior_features_path,
        behavior_session_features_path=behavior_session_features_path,
        movement_state_epochs_path=movement_state_epochs_path,
        quiescence_state_epochs_path=quiescence_state_epochs_path,
        behavior_state_session_features_path=behavior_state_session_features_path,
        manifest_path=dataset_dir / "manifest.json",
        schema_path=dataset_dir / "schema.yaml",
        provenance_path=dataset_dir / "provenance.yaml",
        prefetch_report_path=dataset_dir / "prefetch_report.yaml",
        build_report_path=dataset_dir / "build_report.yaml",
        summary_path=dataset_dir / "SUMMARY.md",
        wheel_store_path=dataset_dir / "sessions",
        pose_store_path=dataset_dir / "sessions",
    )


def _prepare_wheel_payload(*, row: Any, cache_root: Path) -> dict[str, Any]:
    """Load wheel data on a uniform grid via ``SessionLoader.load_wheel``.

    ``brainbox.io.one.SessionLoader.load_wheel`` resolves the dataset revision
    (raw wheel frequently lives under a revision folder such as
    ``alf/#2024-05-06#/``), interpolates position onto a uniform ``WHEEL_FS_HZ``
    grid, and computes a Butterworth-filtered velocity. This supersedes the
    previous flat-file ``.npy`` scan, which missed revisioned sessions and
    skipped velocity on non-monotonic timestamps.

    The ONE instance is obtained via ``bwm_shared.make_remote_one`` which is a
    singleton (repeated calls return the same object), so it is shared across
    worker threads without extra caching. ``bwm_behavior`` is a maintainer
    build/release tool; using ONE to resolve revisions is intentional here.

    Returns
    -------
    dict
        ``{"status": "ok", "eid", "timestamps", "position", "velocity", "fs"}``
        on success, otherwise ``{"status": "missing", "eid", ...}``. Never raises.
    """
    eid = str(row.eid)
    try:
        from brainbox.io.one import SessionLoader

        loader = SessionLoader(one=bwm_shared.make_remote_one(cache_root), eid=eid)
        loader.load_wheel(fs=WHEEL_FS_HZ)
        wheel = loader.wheel
    except Exception as exc:  # keep total: a load failure is a genuine "missing"
        return {"status": "missing", "eid": eid, "error": str(exc)}

    if wheel is None or not {"times", "position"}.issubset(getattr(wheel, "columns", [])):
        return {"status": "missing", "eid": eid, "error": "empty wheel"}
    timestamps = np.asarray(wheel["times"], dtype=np.float64)
    position = np.asarray(wheel["position"], dtype=np.float64)
    if timestamps.size < 2 or position.shape != timestamps.shape:
        return {"status": "missing", "eid": eid, "error": "insufficient wheel samples"}
    payload = {
        "status": "ok",
        "eid": eid,
        "timestamps": timestamps,
        "position": position,
        "fs": float(WHEEL_FS_HZ),
    }
    if "velocity" in wheel.columns:
        velocity = np.asarray(wheel["velocity"], dtype=np.float32)
        if velocity.shape == timestamps.shape and np.isfinite(velocity).any():
            payload["velocity"] = velocity
    return payload


def _resolve_camera_trackers(one_remote: Any, *, eid: str) -> dict[str, str]:
    """Pick, per camera, whichever tracker's datasets are present for this session.

    Lightning Pose is preferred over DeepLabCut whenever both are available
    (measured coverage: LP backfills DLC almost everywhere across BWM
    sessions). Cameras with neither tracker's datasets are omitted.
    """
    try:
        dataset_names = one_remote.list_datasets(eid)
    except Exception:
        dataset_names = []
    trackers: dict[str, str] = {}
    for camera_name in CAMERA_NAMES:
        if any(camera_name in name and ".lightningPose." in name for name in dataset_names):
            trackers[camera_name] = "lightningPose"
        elif any(camera_name in name and ".dlc." in name for name in dataset_names):
            trackers[camera_name] = "dlc"
    return trackers


def _prepare_pose_payload(*, row: Any, cache_root: Path) -> dict[str, Any]:
    """Load pose, motion energy, and pupil diameter via ``SessionLoader``.

    Prefers Lightning Pose over DeepLabCut per camera (see
    ``_resolve_camera_trackers``); motion energy and pupil diameter are
    downstream estimations from pose tracking, so they are re-sourced from
    ``SessionLoader`` alongside the pose keypoints rather than glob-read from
    raw ALF files. This supersedes the previous flat-file ``.npy``/``.pqt``
    scan, which was revision-blind and hard-coded to DLC.

    Returns
    -------
    dict
        ``{"status": "ok", "eid", "cameras": {name: {...}}}`` on success,
        otherwise ``{"status": "missing", "eid", ...}``. Never raises.
    """
    eid = str(row.eid)
    try:
        from brainbox.io.one import SessionLoader

        one_remote = bwm_shared.make_remote_one(cache_root)
        trackers = _resolve_camera_trackers(one_remote, eid=eid)
        loader = SessionLoader(one=one_remote, eid=eid)
    except Exception as exc:  # keep total: a load failure is a genuine "missing"
        return {"status": "missing", "eid": eid, "error": str(exc)}

    pose_by_camera: dict[str, pd.DataFrame] = {}
    for tracker in ("lightningPose", "dlc"):
        views = [CAMERA_VIEW_NAMES[name] for name, cam_tracker in trackers.items() if cam_tracker == tracker]
        if not views:
            continue
        try:
            loader.load_pose(views=views, tracker=tracker)
        except Exception:
            continue
        pose_by_camera.update(loader.pose)
    if not pose_by_camera:
        return {"status": "missing", "eid": eid}

    motion_energy: dict[str, pd.DataFrame] = {}
    try:
        loader.load_motion_energy(views=[CAMERA_VIEW_NAMES[name] for name in pose_by_camera])
        motion_energy = loader.motion_energy
    except Exception:
        motion_energy = {}

    pupil: pd.DataFrame | None = None
    if "leftCamera" in pose_by_camera:
        try:
            loader.load_pupil()
            pupil = loader.pupil
        except Exception:
            pupil = None

    cameras: dict[str, Any] = {}
    for camera_name, pose_df in pose_by_camera.items():
        timestamps = np.asarray(pose_df["times"], dtype=np.float64)
        matrices = [pose_df.drop(columns=["times"]).to_numpy(dtype=np.float32, copy=True)]
        columns = [col for col in pose_df.columns if col != "times"]
        skipped_sources: list[str] = []

        me_df = motion_energy.get(camera_name)
        if me_df is not None and not me_df.empty:
            me_cols = [col for col in me_df.columns if col != "times"]
            if np.asarray(me_df["times"]).shape == timestamps.shape:
                matrices.append(me_df[me_cols].to_numpy(dtype=np.float32, copy=True))
                columns.extend(me_cols)
            else:
                skipped_sources.append("motion_energy")

        if camera_name == "leftCamera" and pupil is not None and not pupil.empty:
            pupil_cols = [col for col in pupil.columns if col != "times"]
            if np.asarray(pupil["times"]).shape == timestamps.shape:
                matrices.append(pupil[pupil_cols].to_numpy(dtype=np.float32, copy=True))
                columns.extend(pupil_cols)
            else:
                skipped_sources.append("pupil")

        features = np.concatenate(matrices, axis=1).astype(np.float32, copy=False) if matrices else np.empty((0, 0), dtype=np.float32)
        if features.size == 0:
            continue
        cameras[camera_name] = {
            "timestamps": timestamps,
            "features": features,
            "columns": columns,
            "tracker": trackers.get(camera_name),
            "skipped_sources": skipped_sources,
        }
    if not cameras:
        return {"status": "missing", "eid": eid}
    return {"status": "ok", "eid": eid, "cameras": cameras}


def _prepare_behavior_payload(*, row: Any, cache_root: Path) -> dict[str, Any]:
    wheel = _prepare_wheel_payload(row=row, cache_root=cache_root)
    pose = _prepare_pose_payload(row=row, cache_root=cache_root)
    return {
        "eid": str(row.eid),
        "subject": str(row.subject),
        "date": str(row.date),
        "session_number": int(row.session_number),
        "lab": str(row.lab),
        "wheel": wheel,
        "pose": pose,
    }


def _write_behavior_session_shards(path: Path, *, roster: pd.DataFrame, cache_root: Path, jobs: int = DEFAULT_BUILD_JOBS, verbose: bool = True, resume: bool = True) -> dict[str, Any]:
    path.mkdir(parents=True, exist_ok=True)
    sessions = roster[["eid", "subject", "date", "session_number", "lab"]].drop_duplicates("eid")

    sessions_written = 0
    sessions_skipped = 0
    wheel_sessions_written = 0
    pose_sessions_written = 0
    camera_groups_written = 0
    missing_wheel: list[str] = []
    missing_pose: list[str] = []
    total_items = int(len(sessions))
    completed = 0
    started_at = perf_counter()
    existing_shards = {shard.stem: shard for shard in path.glob(f"*{SESSION_SHARD_SUFFIX}")} if resume else {}
    if verbose and existing_shards:
        print(f"Resume: found {len(existing_shards)}/{total_items} existing behavior shard(s); they will be reused.")

    def _account_existing_shard(shard_path: Path) -> None:
        nonlocal sessions_written, sessions_skipped, wheel_sessions_written, pose_sessions_written, camera_groups_written
        shard = _read_behavior_session_store_shard(shard_path)
        meta = shard["meta"]
        sessions_written += 1
        sessions_skipped += 1
        if meta.get("wheel", {}).get("present"):
            wheel_sessions_written += 1
        else:
            missing_wheel.append(str(meta.get("eid", shard_path.stem)))
        cameras = meta.get("cameras", {})
        if cameras:
            pose_sessions_written += 1
            camera_groups_written += len(cameras)
        else:
            missing_pose.append(str(meta.get("eid", shard_path.stem)))

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as executor:
        futures = []
        for row in sessions.itertuples(index=False):
            eid = str(row.eid)
            shard_path = existing_shards.get(eid)
            if shard_path is not None:
                completed += 1
                _account_existing_shard(shard_path)
                if verbose and _should_emit_progress(completed, total_items):
                    print(_behavior_progress_line("behavior shards", completed, total_items, started_at, written=sessions_written, skipped=sessions_skipped))
                continue
            futures.append(executor.submit(_prepare_behavior_payload, row=row, cache_root=cache_root))
        for future in as_completed(futures):
            completed += 1
            payload = future.result()
            arrays: dict[str, np.ndarray] = {}
            metadata: dict[str, Any] = {
                "format": BEHAVIOR_SESSION_SHARD_FORMAT_V1,
                "dataset_name": DATASET_NAME,
                "dataset_version": DATASET_VERSION,
                "eid": payload["eid"],
                "subject": payload["subject"],
                "date": payload["date"],
                "session_number": payload["session_number"],
                "lab": payload["lab"],
                "compression": {"name": SIGNAL_COMPRESSION},
                "wheel": {"present": False},
                "cameras": {},
            }
            wheel = payload["wheel"]
            if wheel.get("status") == "ok":
                arrays["wheel.timestamps"] = np.asarray(wheel["timestamps"])
                arrays["wheel.position"] = np.asarray(wheel["position"])
                if "velocity" in wheel:
                    arrays["wheel.velocity"] = np.asarray(wheel["velocity"], dtype=np.float32)
                metadata["wheel"] = {
                    "present": True,
                    "has_velocity": "velocity" in wheel,
                    "fs": float(wheel.get("fs", WHEEL_FS_HZ)),
                }
                wheel_sessions_written += 1
            else:
                missing_wheel.append(payload["eid"])

            pose = payload["pose"]
            if pose.get("status") == "ok":
                for camera_name, camera in pose["cameras"].items():
                    arrays[f"{camera_name}.timestamps"] = camera["timestamps"]
                    arrays[f"{camera_name}.features"] = camera["features"]
                    metadata["cameras"][camera_name] = {
                        "n_frames": int(camera["timestamps"].shape[0]),
                        "n_features": int(camera["features"].shape[1]),
                        "columns": camera["columns"],
                        "tracker": camera["tracker"],
                        "skipped_sources": camera["skipped_sources"],
                        "float_dtype": "float32",
                    }
                pose_sessions_written += 1
                camera_groups_written += len(pose["cameras"])
            else:
                missing_pose.append(payload["eid"])

            if arrays:
                bwm_shared.write_array_shard(path / f"{payload['eid']}{SESSION_SHARD_SUFFIX}", metadata=metadata, arrays=arrays)
                sessions_written += 1

            if verbose and _should_emit_progress(completed, total_items):
                print(_behavior_progress_line("behavior shards", completed, total_items, started_at, written=sessions_written, skipped=sessions_skipped))

    return {
        "sessions_written": sessions_written,
        "sessions_skipped": sessions_skipped,
        "wheel_sessions_written": wheel_sessions_written,
        "pose_sessions_written": pose_sessions_written,
        "camera_groups_written": camera_groups_written,
        "missing_wheel_sessions": missing_wheel,
        "missing_pose_sessions": missing_pose,
        "jobs": int(max(1, jobs)),
        "container_format": SIGNAL_CONTAINER_FORMAT,
        "pose_float_dtype": "float32",
    }


def load_behavior_session_shard(path: Path) -> dict[str, Any]:
    shard = _read_behavior_session_store_shard(path)
    return {"meta": shard["meta"], **shard["arrays"]}


def _read_behavior_session_store_shard(path: Path) -> dict[str, Any]:
    from ibl_ai_agent.datasets import bwm_behavior_compression

    return bwm_behavior_compression.read_behavior_session_shard(path)


def _build_schema(outputs: BuildOutputs) -> dict[str, Any]:
    return {
        "dataset_name": DATASET_NAME,
        "dataset_version": DATASET_VERSION,
        "schema_version": SCHEMA_VERSION,
        "tables": {
            "sessions": {"path": str(outputs.sessions_path.relative_to(outputs.dataset_dir)), "primary_key": ["eid"]},
            "trials": {"path": str(outputs.trials_path.relative_to(outputs.dataset_dir)), "primary_key": ["eid", "trial_id"]},
            "events": {"path": str(outputs.events_path.relative_to(outputs.dataset_dir)), "primary_key": ["eid", "event_id"]},
            "wheel_availability": {"path": str(outputs.wheel_availability_path.relative_to(outputs.dataset_dir)), "primary_key": ["eid"]},
            "pose_availability": {"path": str(outputs.pose_availability_path.relative_to(outputs.dataset_dir)), "primary_key": ["eid", "camera"]},
            "trial_behavior_features": {"path": str(outputs.trial_behavior_features_path.relative_to(outputs.dataset_dir)), "primary_key": ["eid", "trial_id"]},
            "wheel_trial_features": {"path": str(outputs.wheel_trial_features_path.relative_to(outputs.dataset_dir)), "primary_key": ["eid", "trial_id", "window_spec"]},
            "pose_trial_features": {"path": str(outputs.pose_trial_features_path.relative_to(outputs.dataset_dir)), "primary_key": ["eid", "trial_id", "camera", "window_spec"]},
            "event_aligned_behavior_features": {"path": str(outputs.event_aligned_behavior_features_path.relative_to(outputs.dataset_dir)), "primary_key": ["eid", "trial_id", "signal_name", "event_name", "window_spec"]},
            "behavior_session_features": {"path": str(outputs.behavior_session_features_path.relative_to(outputs.dataset_dir)), "primary_key": ["eid"]},
            "movement_state_epochs": {"path": str(outputs.movement_state_epochs_path.relative_to(outputs.dataset_dir)), "primary_key": ["eid", "movement_id"]},
            "quiescence_state_epochs": {"path": str(outputs.quiescence_state_epochs_path.relative_to(outputs.dataset_dir)), "primary_key": ["eid", "quiescence_id"]},
            "behavior_state_session_features": {"path": str(outputs.behavior_state_session_features_path.relative_to(outputs.dataset_dir)), "primary_key": ["eid"]},
        },
        "stores": {
            "behavior_sessions": {
                "path": str(outputs.wheel_store_path.relative_to(outputs.dataset_dir)),
                "shard_key": "eid",
                "container_format": SIGNAL_CONTAINER_FORMAT,
                "file_pattern": f"*{SESSION_SHARD_SUFFIX}",
            },
        },
    }


def _build_provenance(*, config: BuildConfig, trials_path: Path) -> dict[str, Any]:
    return {
        "dataset_name": DATASET_NAME,
        "dataset_version": DATASET_VERSION,
        "created_at": bwm_shared.now_iso(),
        "source": {
            "freeze": bwm_simple.FREEZE,
            "trials_table": bwm_simple._artifact_id(trials_path),
        },
        "storage": {
            "metadata_format": "parquet",
            "metadata_compression": PARQUET_COMPRESSION,
            "signal_format": SIGNAL_CONTAINER_FORMAT,
            "signal_compression": SIGNAL_COMPRESSION,
            "included_signal_stores": ["wheel", "pose"],
            "pose_float_dtype": "float32",
        },
    }


def _build_report(*, config: BuildConfig, sessions_df: pd.DataFrame, trials_df: pd.DataFrame, events_df: pd.DataFrame, wheel_availability_df: pd.DataFrame, pose_availability_df: pd.DataFrame, trial_behavior_features_df: pd.DataFrame, wheel_trial_features_df: pd.DataFrame, pose_trial_features_df: pd.DataFrame, event_aligned_behavior_features_df: pd.DataFrame, behavior_session_features_df: pd.DataFrame, movement_state_epochs_df: pd.DataFrame, quiescence_state_epochs_df: pd.DataFrame, behavior_state_session_features_df: pd.DataFrame, behavior_stats: dict[str, Any], prefetch_report: dict[str, Any]) -> dict[str, Any]:
    prefetch_attempted = bool(prefetch_report.get("actions"))
    initial_missing = _scan_has_missing_required_assets(prefetch_report["initial"])
    final_missing = _scan_has_missing_required_assets(prefetch_report["final"])
    return {
        "dataset_name": DATASET_NAME,
        "dataset_version": DATASET_VERSION,
        "build_timestamp": bwm_shared.now_iso(),
        "build_mode": ("partial-remote-prefetch-used" if prefetch_attempted and final_missing else ("partial-local-cache-only" if final_missing and not config.allow_remote_fetch else ("local-cache-only" if not config.allow_remote_fetch else ("remote-prefetch-used" if prefetch_attempted else "remote-prefetch-allowed-not-needed")))),
        "package_versions": bwm_simple._package_versions(),
        "row_counts": {
            "sessions": int(len(sessions_df)),
            "trials": int(len(trials_df)),
            "events": int(len(events_df)),
            "wheel_availability": int(len(wheel_availability_df)),
            "pose_availability": int(len(pose_availability_df)),
            "trial_behavior_features": int(len(trial_behavior_features_df)),
            "wheel_trial_features": int(len(wheel_trial_features_df)),
            "pose_trial_features": int(len(pose_trial_features_df)),
            "event_aligned_behavior_features": int(len(event_aligned_behavior_features_df)),
            "behavior_session_features": int(len(behavior_session_features_df)),
            "movement_state_epochs": int(len(movement_state_epochs_df)),
            "quiescence_state_epochs": int(len(quiescence_state_epochs_df)),
            "behavior_state_session_features": int(len(behavior_state_session_features_df)),
        },
        "stores": {"behavior_sessions": behavior_stats},
        "prefetch": {
            "enabled": bool(config.prefetch_missing),
            "attempted": prefetch_attempted,
            "initial_missing_required_assets": initial_missing,
            "final_missing_required_assets": final_missing,
            "partial_build": bool(prefetch_report.get("partial_build", False)),
        },
        "release_status": str(prefetch_report.get("release_status", "partial" if final_missing else "complete")),
    }


def _build_summary(*, sessions_df: pd.DataFrame, trials_df: pd.DataFrame, events_df: pd.DataFrame, wheel_availability_df: pd.DataFrame, pose_availability_df: pd.DataFrame, trial_behavior_features_df: pd.DataFrame, wheel_trial_features_df: pd.DataFrame, pose_trial_features_df: pd.DataFrame, event_aligned_behavior_features_df: pd.DataFrame, behavior_session_features_df: pd.DataFrame, movement_state_epochs_df: pd.DataFrame, quiescence_state_epochs_df: pd.DataFrame, behavior_state_session_features_df: pd.DataFrame, behavior_stats: dict[str, Any], prefetch_report: dict[str, Any]) -> str:
    return "\n".join([
        "# BWM Behavior Dataset Build Summary",
        "",
        f"- Sessions: {len(sessions_df):,}",
        f"- Trials: {len(trials_df):,}",
        f"- Events: {len(events_df):,}",
        f"- Wheel availability rows: {len(wheel_availability_df):,}",
        f"- Pose availability rows: {len(pose_availability_df):,}",
        f"- Trial behavior feature rows: {len(trial_behavior_features_df):,}",
        f"- Wheel trial feature rows: {len(wheel_trial_features_df):,}",
        f"- Pose trial feature rows: {len(pose_trial_features_df):,}",
        f"- Event-aligned behavior feature rows: {len(event_aligned_behavior_features_df):,}",
        f"- Behavior session feature rows: {len(behavior_session_features_df):,}",
        f"- Movement state epoch rows: {len(movement_state_epochs_df):,}",
        f"- Quiescence state epoch rows: {len(quiescence_state_epochs_df):,}",
        f"- Behavior state session feature rows: {len(behavior_state_session_features_df):,}",
        f"- Session shards written: {behavior_stats['sessions_written']:,}",
        f"- Wheel sessions written: {behavior_stats['wheel_sessions_written']:,}",
        f"- Pose sessions written: {behavior_stats['pose_sessions_written']:,}",
        "",
        "## Workflow",
        "",
        f"- Release status: `{prefetch_report.get('release_status', 'partial' if _scan_has_missing_required_assets(prefetch_report['final']) else 'complete')}`",
        f"- Initial missing required assets: `{_scan_has_missing_required_assets(prefetch_report['initial'])}`",
        f"- Prefetch enabled: `{prefetch_report['config']['prefetch_missing']}`",
        f"- Prefetch attempted: `{bool(prefetch_report.get('actions'))}`",
        f"- Final missing required assets: `{_scan_has_missing_required_assets(prefetch_report['final'])}`",
        f"- Partial build: `{prefetch_report.get('partial_build', False)}`",
        "",
        "## Encoding",
        "",
        f"- Container format: `{behavior_stats['container_format']}`",
        "- Compression: `blosc_zstd_shuffle`",
        "- Pose float dtype: `float32`",
        "",
    ]) + "\n"


def _refresh_sidecars(*, outputs: BuildOutputs, config: BuildConfig, sessions_df: pd.DataFrame, trials_df: pd.DataFrame, events_df: pd.DataFrame, wheel_availability_df: pd.DataFrame, pose_availability_df: pd.DataFrame, trial_behavior_features_df: pd.DataFrame, wheel_trial_features_df: pd.DataFrame, pose_trial_features_df: pd.DataFrame, event_aligned_behavior_features_df: pd.DataFrame, behavior_session_features_df: pd.DataFrame, movement_state_epochs_df: pd.DataFrame, quiescence_state_epochs_df: pd.DataFrame, behavior_state_session_features_df: pd.DataFrame, behavior_stats: dict[str, Any], prefetch_report: dict[str, Any]) -> None:
    schema = _build_schema(outputs)
    provenance = yaml.safe_load(outputs.provenance_path.read_text(encoding='utf-8')) if outputs.provenance_path.exists() else {}
    build_report = _build_report(config=config, sessions_df=sessions_df, trials_df=trials_df, events_df=events_df, wheel_availability_df=wheel_availability_df, pose_availability_df=pose_availability_df, trial_behavior_features_df=trial_behavior_features_df, wheel_trial_features_df=wheel_trial_features_df, pose_trial_features_df=pose_trial_features_df, event_aligned_behavior_features_df=event_aligned_behavior_features_df, behavior_session_features_df=behavior_session_features_df, movement_state_epochs_df=movement_state_epochs_df, quiescence_state_epochs_df=quiescence_state_epochs_df, behavior_state_session_features_df=behavior_state_session_features_df, behavior_stats=behavior_stats, prefetch_report=prefetch_report)
    summary = _build_summary(sessions_df=sessions_df, trials_df=trials_df, events_df=events_df, wheel_availability_df=wheel_availability_df, pose_availability_df=pose_availability_df, trial_behavior_features_df=trial_behavior_features_df, wheel_trial_features_df=wheel_trial_features_df, pose_trial_features_df=pose_trial_features_df, event_aligned_behavior_features_df=event_aligned_behavior_features_df, behavior_session_features_df=behavior_session_features_df, movement_state_epochs_df=movement_state_epochs_df, quiescence_state_epochs_df=quiescence_state_epochs_df, behavior_state_session_features_df=behavior_state_session_features_df, behavior_stats=behavior_stats, prefetch_report=prefetch_report)
    outputs.schema_path.write_text(yaml.safe_dump(schema, sort_keys=False), encoding='utf-8')
    outputs.provenance_path.write_text(yaml.safe_dump(provenance, sort_keys=False), encoding='utf-8')
    outputs.build_report_path.write_text(yaml.safe_dump(build_report, sort_keys=False), encoding='utf-8')
    outputs.summary_path.write_text(summary, encoding='utf-8')
    manifest = bwm_shared.build_manifest(dataset_name=DATASET_NAME, dataset_version=DATASET_VERSION, dataset_dir=outputs.dataset_dir)
    outputs.manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding='utf-8')


def refresh_bwm_behavior_features(*, dataset_dir: Path, cache_root: Path, jobs: int = DEFAULT_BUILD_JOBS, verbose: bool = True) -> BuildOutputs:
    if not dataset_dir.exists():
        raise BuildError(f'Dataset directory does not exist: {dataset_dir}')
    outputs = _final_outputs(dataset_dir)
    reporter = BuildConfig(output_root=dataset_dir.parent.parent, cache_root=cache_root, allow_remote_fetch=False, prefetch_missing=False, require_signals=False, verbose=verbose)
    sessions_df = pd.read_parquet(outputs.sessions_path)
    trials_df = pd.read_parquet(outputs.trials_path)
    events_df = pd.read_parquet(outputs.events_path)
    trial_behavior_features_df = _compute_trial_behavior_features(trials_df)
    if verbose:
        print(f"Refresh: computing per-session wheel/pose/event features with jobs={max(1, jobs)}.")
    (
        wheel_availability_df,
        pose_availability_df,
        wheel_trial_features_df,
        pose_trial_features_df,
        event_aligned_behavior_features_df,
        movement_state_epochs_df,
        quiescence_state_epochs_df,
        behavior_state_session_features_df,
    ) = _build_behavior_feature_tables(
        sessions_df=sessions_df,
        trials_df=trials_df,
        cache_root=cache_root,
        jobs=jobs,
        verbose=verbose,
    )
    behavior_session_features_df = _build_behavior_session_features(sessions_df=sessions_df, trial_behavior_features_df=trial_behavior_features_df, wheel_availability_df=wheel_availability_df, pose_availability_df=pose_availability_df)
    for df, sort_cols in ((trial_behavior_features_df, ['eid', 'trial_id']), (wheel_trial_features_df, ['eid', 'trial_id']), (pose_trial_features_df, ['eid', 'trial_id', 'camera']), (event_aligned_behavior_features_df, ['eid', 'trial_id', 'signal_name', 'event_name']), (behavior_session_features_df, ['eid']), (movement_state_epochs_df, ['eid', 'movement_id']), (quiescence_state_epochs_df, ['eid', 'quiescence_id']), (behavior_state_session_features_df, ['eid'])):
        if not df.empty:
            df.sort_values(sort_cols, inplace=True, kind='mergesort')
    wheel_availability_df.to_parquet(outputs.wheel_availability_path, engine=PARQUET_ENGINE, compression=PARQUET_COMPRESSION, index=False)
    pose_availability_df.to_parquet(outputs.pose_availability_path, engine=PARQUET_ENGINE, compression=PARQUET_COMPRESSION, index=False)
    trial_behavior_features_df.to_parquet(outputs.trial_behavior_features_path, engine=PARQUET_ENGINE, compression=PARQUET_COMPRESSION, index=False)
    wheel_trial_features_df.to_parquet(outputs.wheel_trial_features_path, engine=PARQUET_ENGINE, compression=PARQUET_COMPRESSION, index=False)
    pose_trial_features_df.to_parquet(outputs.pose_trial_features_path, engine=PARQUET_ENGINE, compression=PARQUET_COMPRESSION, index=False)
    event_aligned_behavior_features_df.to_parquet(outputs.event_aligned_behavior_features_path, engine=PARQUET_ENGINE, compression=PARQUET_COMPRESSION, index=False)
    behavior_session_features_df.to_parquet(outputs.behavior_session_features_path, engine=PARQUET_ENGINE, compression=PARQUET_COMPRESSION, index=False)
    movement_state_epochs_df.to_parquet(outputs.movement_state_epochs_path, engine=PARQUET_ENGINE, compression=PARQUET_COMPRESSION, index=False)
    quiescence_state_epochs_df.to_parquet(outputs.quiescence_state_epochs_path, engine=PARQUET_ENGINE, compression=PARQUET_COMPRESSION, index=False)
    behavior_state_session_features_df.to_parquet(outputs.behavior_state_session_features_path, engine=PARQUET_ENGINE, compression=PARQUET_COMPRESSION, index=False)
    behavior_stats = _summarize_existing_behavior_store(outputs)
    _refresh_sidecars(outputs=outputs, config=reporter, sessions_df=sessions_df, trials_df=trials_df, events_df=events_df, wheel_availability_df=wheel_availability_df, pose_availability_df=pose_availability_df, trial_behavior_features_df=trial_behavior_features_df, wheel_trial_features_df=wheel_trial_features_df, pose_trial_features_df=pose_trial_features_df, event_aligned_behavior_features_df=event_aligned_behavior_features_df, behavior_session_features_df=behavior_session_features_df, movement_state_epochs_df=movement_state_epochs_df, quiescence_state_epochs_df=quiescence_state_epochs_df, behavior_state_session_features_df=behavior_state_session_features_df, behavior_stats=behavior_stats, prefetch_report=_load_behavior_prefetch_report(outputs.prefetch_report_path))
    (dataset_dir / 'feature_refresh_report.yaml').write_text(yaml.safe_dump({'dataset_dir': str(dataset_dir), 'generated_at': bwm_shared.now_iso(), 'operation': 'refresh_bwm_behavior_features', 'trial_behavior_feature_rows': int(len(trial_behavior_features_df)), 'wheel_trial_feature_rows': int(len(wheel_trial_features_df)), 'pose_trial_feature_rows': int(len(pose_trial_features_df)), 'event_aligned_behavior_feature_rows': int(len(event_aligned_behavior_features_df)), 'behavior_session_feature_rows': int(len(behavior_session_features_df)), 'movement_state_epoch_rows': int(len(movement_state_epochs_df)), 'quiescence_state_epoch_rows': int(len(quiescence_state_epochs_df)), 'behavior_state_session_feature_rows': int(len(behavior_state_session_features_df))}, sort_keys=False), encoding='utf-8')
    return outputs


def inspect_bwm_behavior_dataset(*, dataset_dir: Path) -> dict[str, Any]:
    outputs = _final_outputs(dataset_dir)
    dataset_exists = dataset_dir.exists()
    table_paths = {name: getattr(outputs, attr) for name, attr in EXPECTED_TABLE_OUTPUT_ATTRS.items()}
    present_tables = {name: path.exists() for name, path in table_paths.items()}
    missing_tables = sorted(name for name, present in present_tables.items() if not present)
    row_counts: dict[str, int | None] = {}
    for name, path in table_paths.items():
        if path.exists():
            try:
                row_counts[name] = int(len(pd.read_parquet(path)))
            except Exception:
                row_counts[name] = None
    schema = _load_yaml_file(outputs.schema_path)
    schema_tables: dict[str, Any] = dict(schema.get("tables", {}) or {})
    schema_version = schema.get("schema_version")
    dataset_version = schema.get("dataset_version")
    layout = _detect_dataset_layout(dataset_dir=dataset_dir, outputs=outputs, schema=schema)
    missing_schema_tables = sorted(name for name in table_paths if name not in schema_tables)
    schema_dataset_version_matches = dataset_version == layout.expected_dataset_version
    schema_version_matches = schema_version == layout.expected_schema_version
    compression_profile_matches = (
        True
        if layout.compression_profile is None
        else schema.get("compression_profile") == layout.compression_profile
    )
    sidecars = {
        "manifest": outputs.manifest_path.exists(),
        "schema": outputs.schema_path.exists(),
        "provenance": outputs.provenance_path.exists(),
        "build_report": outputs.build_report_path.exists(),
        "summary": outputs.summary_path.exists(),
    }
    missing_sidecars = sorted(name for name, present in sidecars.items() if not present)
    session_shards = sorted(outputs.wheel_store_path.glob(f"*{SESSION_SHARD_SUFFIX}")) if outputs.wheel_store_path.exists() else []
    missing_derived_tables = sorted(name for name in missing_tables if name in DERIVED_TABLE_NAMES)
    needs_sidecar_refresh = bool(
        missing_sidecars
        or missing_schema_tables
        or not schema_dataset_version_matches
        or not schema_version_matches
        or not compression_profile_matches
    )
    needs_derived_refresh = bool(missing_derived_tables)
    preferred_refresh_path = "local_shards" if session_shards else "cache"
    if needs_derived_refresh:
        recommended_action = "refresh_derived_from_local_shards" if session_shards else "refresh_derived_from_cache"
    elif needs_sidecar_refresh:
        recommended_action = "refresh_sidecars_only"
    else:
        recommended_action = "none"
    return {
        "dataset_dir": str(dataset_dir),
        "dataset_exists": bool(dataset_exists),
        "dataset_kind": layout.kind,
        "dataset_version": dataset_version,
        "schema_version": schema_version,
        "expected_dataset_version": layout.expected_dataset_version,
        "expected_schema_version": layout.expected_schema_version,
        "table_presence": present_tables,
        "row_counts": row_counts,
        "missing_tables": missing_tables,
        "missing_derived_tables": missing_derived_tables,
        "missing_schema_tables": missing_schema_tables,
        "schema_dataset_version_matches": schema_dataset_version_matches,
        "schema_version_matches": schema_version_matches,
        "compression_profile_matches": compression_profile_matches,
        "sidecars": sidecars,
        "missing_sidecars": missing_sidecars,
        "session_shards": {"present": bool(session_shards), "count": int(len(session_shards))},
        "preferred_refresh_path": preferred_refresh_path,
        "recommended_action": recommended_action,
    }


def refresh_bwm_behavior_features_from_shards(
    *,
    dataset_dir: Path,
    jobs: int = DEFAULT_BUILD_JOBS,
    verbose: bool = True,
    write_tables: set[str] | None = None,
) -> BuildOutputs:
    if not dataset_dir.exists():
        raise BuildError(f'Dataset directory does not exist: {dataset_dir}')
    outputs = _final_outputs(dataset_dir)
    if not outputs.wheel_store_path.exists():
        raise BuildError(f'Behavior shard directory does not exist: {outputs.wheel_store_path}')
    layout = _detect_dataset_layout(dataset_dir=dataset_dir, outputs=outputs, schema=_load_yaml_file(outputs.schema_path))
    if layout.kind != "base_v1_0":
        from ibl_ai_agent.datasets import bwm_behavior_upgrade

        return bwm_behavior_upgrade.refresh_upgraded_bwm_behavior_dataset_from_shards(
            dataset_dir=dataset_dir,
            jobs=jobs,
            verbose=verbose,
            write_tables=write_tables,
        )
    reporter = BuildConfig(output_root=dataset_dir.parent.parent, cache_root=Path("."), allow_remote_fetch=False, prefetch_missing=False, require_signals=False, verbose=verbose)
    sessions_df = pd.read_parquet(outputs.sessions_path)
    trials_df = pd.read_parquet(outputs.trials_path)
    events_df = pd.read_parquet(outputs.events_path)
    trial_behavior_features_df = _compute_trial_behavior_features(trials_df)
    if verbose:
        print(f"Refresh: rebuilding behavior-derived tables from local session shards with jobs={max(1, jobs)}.")
    from ibl_ai_agent.datasets import bwm_behavior_compression

    (
        wheel_availability_df,
        pose_availability_df,
        wheel_trial_features_df,
        pose_trial_features_df,
        event_aligned_behavior_features_df,
        movement_state_epochs_df,
        quiescence_state_epochs_df,
        behavior_state_session_features_df,
    ) = bwm_behavior_compression.build_behavior_feature_tables_from_shards(
        dataset_dir=dataset_dir,
        trials_df=trials_df,
        verbose=verbose,
        jobs=jobs,
    )

    sessions_df = sessions_df.drop(columns=[col for col in ("wheel_present", "pose_present", "present_cameras") if col in sessions_df.columns], errors="ignore")
    sessions_df = sessions_df.merge(wheel_availability_df[["eid", "wheel_present"]].drop_duplicates("eid"), on="eid", how="left")
    pose_session_presence = pose_availability_df.groupby("eid")["pose_present"].max().rename("pose_present") if not pose_availability_df.empty else pd.Series(dtype=bool)
    if not pose_session_presence.empty:
        sessions_df = sessions_df.merge(pose_session_presence, on="eid", how="left")
    camera_lists = (
        pose_availability_df.loc[pose_availability_df["pose_present"]]
        .groupby("eid")["camera"]
        .apply(lambda s: sorted({str(v) for v in s if str(v)}))
        .rename("present_cameras")
        if not pose_availability_df.empty
        else pd.Series(dtype=object)
    )
    if not camera_lists.empty:
        sessions_df = sessions_df.merge(camera_lists, on="eid", how="left")
    sessions_df["wheel_present"] = sessions_df.get("wheel_present", False).fillna(False).astype(bool)
    sessions_df["pose_present"] = sessions_df.get("pose_present", False).fillna(False).astype(bool)
    sessions_df["present_cameras"] = sessions_df.get("present_cameras", [[] for _ in range(len(sessions_df))]).apply(lambda x: x if isinstance(x, list) else [])
    behavior_session_features_df = _build_behavior_session_features(
        sessions_df=sessions_df,
        trial_behavior_features_df=trial_behavior_features_df,
        wheel_availability_df=wheel_availability_df,
        pose_availability_df=pose_availability_df,
    )

    for df, sort_cols in (
        (trial_behavior_features_df, ['eid', 'trial_id']),
        (wheel_trial_features_df, ['eid', 'trial_id']),
        (pose_trial_features_df, ['eid', 'trial_id', 'camera']),
        (event_aligned_behavior_features_df, ['eid', 'trial_id', 'signal_name', 'event_name']),
        (behavior_session_features_df, ['eid']),
        (movement_state_epochs_df, ['eid', 'movement_id']),
        (quiescence_state_epochs_df, ['eid', 'quiescence_id']),
        (behavior_state_session_features_df, ['eid']),
    ):
        if not df.empty:
            df.sort_values(sort_cols, inplace=True, kind='mergesort')

    table_frames = {
        "sessions": sessions_df,
        "wheel_availability": wheel_availability_df,
        "pose_availability": pose_availability_df,
        "trial_behavior_features": trial_behavior_features_df,
        "wheel_trial_features": wheel_trial_features_df,
        "pose_trial_features": pose_trial_features_df,
        "event_aligned_behavior_features": event_aligned_behavior_features_df,
        "behavior_session_features": behavior_session_features_df,
        "movement_state_epochs": movement_state_epochs_df,
        "quiescence_state_epochs": quiescence_state_epochs_df,
        "behavior_state_session_features": behavior_state_session_features_df,
    }
    if write_tables is None:
        write_tables = set(table_frames.keys())
    for table_name in write_tables:
        path = getattr(outputs, EXPECTED_TABLE_OUTPUT_ATTRS[table_name])
        table_frames[table_name].to_parquet(path, engine=PARQUET_ENGINE, compression=PARQUET_COMPRESSION, index=False)

    behavior_stats = _summarize_existing_behavior_store(outputs)
    _refresh_sidecars(
        outputs=outputs,
        config=reporter,
        sessions_df=sessions_df,
        trials_df=trials_df,
        events_df=events_df,
        wheel_availability_df=wheel_availability_df,
        pose_availability_df=pose_availability_df,
        trial_behavior_features_df=trial_behavior_features_df,
        wheel_trial_features_df=wheel_trial_features_df,
        pose_trial_features_df=pose_trial_features_df,
        event_aligned_behavior_features_df=event_aligned_behavior_features_df,
        behavior_session_features_df=behavior_session_features_df,
        movement_state_epochs_df=movement_state_epochs_df,
        quiescence_state_epochs_df=quiescence_state_epochs_df,
        behavior_state_session_features_df=behavior_state_session_features_df,
        behavior_stats=behavior_stats,
        prefetch_report=_load_behavior_prefetch_report(outputs.prefetch_report_path),
    )
    (dataset_dir / 'feature_refresh_report.yaml').write_text(yaml.safe_dump({'dataset_dir': str(dataset_dir), 'generated_at': bwm_shared.now_iso(), 'operation': 'refresh_bwm_behavior_features_from_shards', 'trial_behavior_feature_rows': int(len(trial_behavior_features_df)), 'wheel_trial_feature_rows': int(len(wheel_trial_features_df)), 'pose_trial_feature_rows': int(len(pose_trial_features_df)), 'event_aligned_behavior_feature_rows': int(len(event_aligned_behavior_features_df)), 'behavior_session_feature_rows': int(len(behavior_session_features_df)), 'movement_state_epoch_rows': int(len(movement_state_epochs_df)), 'quiescence_state_epoch_rows': int(len(quiescence_state_epochs_df)), 'behavior_state_session_feature_rows': int(len(behavior_state_session_features_df)), 'written_tables': sorted(write_tables)}, sort_keys=False), encoding='utf-8')
    return outputs


def ensure_bwm_behavior_dataset(
    *,
    dataset_dir: Path,
    cache_root: Path | None = None,
    jobs: int = DEFAULT_BUILD_JOBS,
    verbose: bool = True,
    force_refresh: bool = False,
    dry_run: bool = False,
) -> tuple[BuildOutputs, dict[str, Any]]:
    report = inspect_bwm_behavior_dataset(dataset_dir=dataset_dir)
    if not report["dataset_exists"]:
        raise BuildError(f'Dataset directory does not exist: {dataset_dir}')
    outputs = _final_outputs(dataset_dir)
    layout = _detect_dataset_layout(dataset_dir=dataset_dir, outputs=outputs, schema=_load_yaml_file(outputs.schema_path))
    if dry_run:
        return outputs, report
    missing_derived = set(report["missing_derived_tables"])
    if force_refresh:
        if layout.kind == "base_v1_0":
            refresh_bwm_behavior_features_from_shards(dataset_dir=dataset_dir, jobs=jobs, verbose=verbose)
        else:
            from ibl_ai_agent.datasets import bwm_behavior_upgrade

            bwm_behavior_upgrade.refresh_upgraded_bwm_behavior_dataset_from_shards(
                dataset_dir=dataset_dir,
                jobs=jobs,
                verbose=verbose,
            )
    elif missing_derived:
        if report["preferred_refresh_path"] == "local_shards":
            if layout.kind == "base_v1_0":
                refresh_bwm_behavior_features_from_shards(dataset_dir=dataset_dir, jobs=jobs, verbose=verbose, write_tables=missing_derived)
            else:
                from ibl_ai_agent.datasets import bwm_behavior_upgrade

                bwm_behavior_upgrade.refresh_upgraded_bwm_behavior_dataset_from_shards(
                    dataset_dir=dataset_dir,
                    jobs=jobs,
                    verbose=verbose,
                    write_tables=missing_derived,
                )
        elif cache_root is not None:
            refresh_bwm_behavior_features(dataset_dir=dataset_dir, cache_root=cache_root, jobs=jobs, verbose=verbose)
        else:
            raise BuildError("Derived tables are missing, but no local shard store or cache root was provided for refresh.")
    elif report["recommended_action"] == "refresh_sidecars_only":
        if layout.kind == "base_v1_0":
            sessions_df = pd.read_parquet(outputs.sessions_path)
            trials_df = pd.read_parquet(outputs.trials_path)
            events_df = pd.read_parquet(outputs.events_path)
            wheel_availability_df = pd.read_parquet(outputs.wheel_availability_path)
            pose_availability_df = pd.read_parquet(outputs.pose_availability_path)
            trial_behavior_features_df = pd.read_parquet(outputs.trial_behavior_features_path)
            wheel_trial_features_df = pd.read_parquet(outputs.wheel_trial_features_path)
            pose_trial_features_df = pd.read_parquet(outputs.pose_trial_features_path)
            event_aligned_behavior_features_df = pd.read_parquet(outputs.event_aligned_behavior_features_path)
            behavior_session_features_df = pd.read_parquet(outputs.behavior_session_features_path)
            movement_state_epochs_df = pd.read_parquet(outputs.movement_state_epochs_path)
            quiescence_state_epochs_df = pd.read_parquet(outputs.quiescence_state_epochs_path)
            behavior_state_session_features_df = pd.read_parquet(outputs.behavior_state_session_features_path)
            reporter = BuildConfig(output_root=dataset_dir.parent.parent, cache_root=cache_root or Path("."), allow_remote_fetch=False, prefetch_missing=False, require_signals=False, verbose=verbose)
            _refresh_sidecars(
                outputs=outputs,
                config=reporter,
                sessions_df=sessions_df,
                trials_df=trials_df,
                events_df=events_df,
                wheel_availability_df=wheel_availability_df,
                pose_availability_df=pose_availability_df,
                trial_behavior_features_df=trial_behavior_features_df,
                wheel_trial_features_df=wheel_trial_features_df,
                pose_trial_features_df=pose_trial_features_df,
                event_aligned_behavior_features_df=event_aligned_behavior_features_df,
                behavior_session_features_df=behavior_session_features_df,
                movement_state_epochs_df=movement_state_epochs_df,
                quiescence_state_epochs_df=quiescence_state_epochs_df,
                behavior_state_session_features_df=behavior_state_session_features_df,
                behavior_stats=_summarize_existing_behavior_store(outputs),
                prefetch_report=_load_behavior_prefetch_report(outputs.prefetch_report_path),
            )
        else:
            from ibl_ai_agent.datasets import bwm_behavior_upgrade

            bwm_behavior_upgrade.refresh_upgraded_bwm_behavior_sidecars(dataset_dir=dataset_dir)
    return outputs, inspect_bwm_behavior_dataset(dataset_dir=dataset_dir)


def _summarize_existing_behavior_store(outputs: BuildOutputs) -> dict[str, Any]:
    sessions_written = 0
    wheel_sessions_written = 0
    pose_sessions_written = 0
    camera_groups_written = 0
    missing_wheel: list[str] = []
    missing_pose: list[str] = []
    if outputs.wheel_store_path.exists():
        for shard_path in sorted(outputs.wheel_store_path.glob(f'*{SESSION_SHARD_SUFFIX}')):
            shard = _read_behavior_session_store_shard(shard_path)
            meta = shard['meta']
            sessions_written += 1
            if meta.get('wheel', {}).get('present'):
                wheel_sessions_written += 1
            else:
                missing_wheel.append(str(meta.get('eid', shard_path.stem)))
            cameras = meta.get('cameras', {})
            if cameras:
                pose_sessions_written += 1
                camera_groups_written += len(cameras)
            else:
                missing_pose.append(str(meta.get('eid', shard_path.stem)))
    return {'sessions_written': int(sessions_written), 'wheel_sessions_written': int(wheel_sessions_written), 'pose_sessions_written': int(pose_sessions_written), 'camera_groups_written': int(camera_groups_written), 'missing_wheel_sessions': missing_wheel, 'missing_pose_sessions': missing_pose, 'jobs': None, 'container_format': SIGNAL_CONTAINER_FORMAT, 'pose_float_dtype': 'float32'}


def _load_behavior_prefetch_report(path: Path) -> dict[str, Any]:
    if path.exists():
        return yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    empty_scan = {'aggregate_tables': {'trials': {'present': True}}, 'signals': {'wheel': {'missing': []}, 'pose': {'missing': []}}}
    return {'config': {'prefetch_missing': False}, 'initial': empty_scan, 'final': empty_scan, 'actions': []}


def _final_outputs(target_dir: Path) -> BuildOutputs:
    return BuildOutputs(dataset_dir=target_dir, sessions_path=target_dir / 'metadata' / 'sessions.parquet', trials_path=target_dir / 'metadata' / 'trials.parquet', events_path=target_dir / 'metadata' / 'events.parquet', wheel_availability_path=target_dir / 'metadata' / 'wheel_availability.parquet', pose_availability_path=target_dir / 'metadata' / 'pose_availability.parquet', trial_behavior_features_path=target_dir / 'features' / 'trial_behavior_features.parquet', wheel_trial_features_path=target_dir / 'features' / 'wheel_trial_features.parquet', pose_trial_features_path=target_dir / 'features' / 'pose_trial_features.parquet', event_aligned_behavior_features_path=target_dir / 'features' / 'event_aligned_behavior_features.parquet', behavior_session_features_path=target_dir / 'features' / 'behavior_session_features.parquet', movement_state_epochs_path=target_dir / 'features' / 'movement_state_epochs.parquet', quiescence_state_epochs_path=target_dir / 'features' / 'quiescence_state_epochs.parquet', behavior_state_session_features_path=target_dir / 'features' / 'behavior_state_session_features.parquet', manifest_path=target_dir / 'manifest.json', schema_path=target_dir / 'schema.yaml', provenance_path=target_dir / 'provenance.yaml', prefetch_report_path=target_dir / 'prefetch_report.yaml', build_report_path=target_dir / 'build_report.yaml', summary_path=target_dir / 'SUMMARY.md', wheel_store_path=target_dir / 'sessions', pose_store_path=target_dir / 'sessions')


def _format_scan_summary(scan: dict[str, Any], *, title: str) -> str:
    lines = [
        f"{title}:",
        f"- selected sessions: {scan['selection']['sessions']}",
        f"- aggregate trials table: {'present' if scan['aggregate_tables']['trials']['present'] else 'missing'}",
        f"- wheel present for {scan['signals']['wheel']['present_sessions']}/{scan['signals']['wheel']['required_sessions']} session(s)",
        f"- pose present for {scan['signals']['pose']['present_sessions']}/{scan['signals']['pose']['required_sessions']} session(s)",
    ]
    if scan['signals']['wheel']['missing']:
        lines.append(f"- missing wheel: {', '.join(item['eid'] for item in scan['signals']['wheel']['missing'][:5])}")
    if scan['signals']['pose']['missing']:
        lines.append(f"- missing pose: {', '.join(item['eid'] for item in scan['signals']['pose']['missing'][:5])}")
    return "\n".join(lines)


def _write_failure_prefetch_report(parent: Path, prefetch_report: dict[str, Any]) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / f"{DATASET_NAME}_prefetch_failure_{bwm_shared.now_tag()}.yaml"
    path.write_text(yaml.safe_dump(prefetch_report, sort_keys=False), encoding="utf-8")
    return path


def _resolve_behavior_build_dir(parent: Path, *, config: BuildConfig) -> tuple[Path, bool]:
    if config.resume:
        candidates = sorted(
            [path for path in parent.glob(f".{DATASET_NAME}-{DATASET_VERSION}-*") if path.is_dir()],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for candidate in candidates:
            return candidate, True
    return Path(mkdtemp(prefix=f".{DATASET_NAME}-{DATASET_VERSION}-", dir=parent)), False


def _write_build_state(tmp_dir: Path, state: dict[str, Any]) -> None:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    (tmp_dir / "build_state.yaml").write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")


def _should_emit_progress(index: int, total: int) -> bool:
    if total <= 0:
        return True
    if index in (1, total):
        return True
    if total <= 20:
        return True
    return index % max(1, total // 20) == 0


def _behavior_progress_line(stage: str, completed: int, total: int, started_at: float, *, written: int, skipped: int) -> str:
    base = bwm_ephys._progress_line(stage, completed, total, started_at)
    return f"{base} written={written} skipped={skipped}"


def _behavior_feature_progress_line(stage: str, completed: int, total: int, started_at: float, *, wheel_rows: int, pose_rows: int, event_rows: int, current: str | None = None) -> str:
    base = bwm_ephys._progress_line(stage, completed, total, started_at, current=current)
    return f"{base} wheel_rows={wheel_rows:,} pose_rows={pose_rows:,} event_rows={event_rows:,}"


def _emit(config: BuildConfig, message: str) -> None:
    if config.verbose:
        print(message)
