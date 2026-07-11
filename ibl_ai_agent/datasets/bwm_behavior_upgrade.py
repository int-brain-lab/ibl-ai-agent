from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
import json
from tempfile import mkdtemp
import os
from pathlib import Path
import shutil
from time import perf_counter
from typing import Any
import zipfile

import pandas as pd
import yaml

from ibl_ai_agent.datasets import bwm_behavior, bwm_behavior_compression, bwm_shared


DATASET_NAME = "bwm_behavior"
TARGET_DATASET_VERSION = "2.0.0"
COMPRESSION_PROFILE = "aggressive-pose-delta-wheel-native-left60-right60-body30"
TARGET_SIGNAL_CONTAINER_FORMAT = "zip_semantic_shards_v2"
DEFAULT_UPGRADE_JOBS = max(1, (os.cpu_count() or 2) // 2)
PROGRESS_UPDATE_EVERY = 10
FEATURE_CACHE_DIRNAME = ".feature-refresh-cache"


@dataclass(frozen=True)
class UpgradeOutputs:
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
    build_report_path: Path
    summary_path: Path
    archive_path: Path | None = None
    archive_checksum_path: Path | None = None


def upgrade_bwm_behavior_dataset_compression(
    *,
    source_dataset_dir: Path,
    output_root: Path,
    jobs: int = DEFAULT_UPGRADE_JOBS,
    resume: bool = True,
    verbose: bool = True,
    release_root: Path | None = None,
) -> UpgradeOutputs:
    if not source_dataset_dir.exists():
        raise FileNotFoundError(f"Missing source dataset: {source_dataset_dir}")
    target_dir = output_root / DATASET_NAME / TARGET_DATASET_VERSION
    def _maybe_release(outputs: bwm_behavior.BuildOutputs) -> bwm_behavior.BuildOutputs:
        if release_root is None:
            return outputs
        release_artifacts = bwm_shared.write_release_archive(
            target_dir,
            release_root=release_root,
            dataset_name=DATASET_NAME,
            dataset_version=TARGET_DATASET_VERSION,
            exclude_names={FEATURE_CACHE_DIRNAME},
        )
        return replace(
            outputs,
            archive_path=release_artifacts["archive_path"],
            archive_checksum_path=release_artifacts["checksum_path"],
        )

    if resume and target_dir.exists() and _upgrade_completed(target_dir):
        outputs = _maybe_release(bwm_behavior._final_outputs(target_dir))
        return UpgradeOutputs(
            dataset_dir=target_dir,
            sessions_path=outputs.sessions_path,
            trials_path=outputs.trials_path,
            events_path=outputs.events_path,
            wheel_availability_path=outputs.wheel_availability_path,
            pose_availability_path=outputs.pose_availability_path,
            trial_behavior_features_path=outputs.trial_behavior_features_path,
            wheel_trial_features_path=outputs.wheel_trial_features_path,
            pose_trial_features_path=outputs.pose_trial_features_path,
            event_aligned_behavior_features_path=outputs.event_aligned_behavior_features_path,
            behavior_session_features_path=outputs.behavior_session_features_path,
            movement_state_epochs_path=outputs.movement_state_epochs_path,
            quiescence_state_epochs_path=outputs.quiescence_state_epochs_path,
            behavior_state_session_features_path=outputs.behavior_state_session_features_path,
            manifest_path=outputs.manifest_path,
            schema_path=outputs.schema_path,
            provenance_path=outputs.provenance_path,
            build_report_path=outputs.build_report_path,
            summary_path=outputs.summary_path,
            archive_path=outputs.archive_path,
            archive_checksum_path=outputs.archive_checksum_path,
        )

    tmp_parent = target_dir.parent
    tmp_parent.mkdir(parents=True, exist_ok=True)
    work_dir, resumed = _resolve_upgrade_dir(tmp_parent, target_dir=target_dir, resume=resume)
    _write_upgrade_state(work_dir, {"status": "running", "resumed": resumed, "started_at": bwm_shared.now_iso(), "jobs": int(max(1, jobs))})

    started_at = perf_counter()
    try:
        if not (work_dir / "metadata" / "sessions.parquet").exists():
            _copy_tree_hardlink_preferred(source_dataset_dir, work_dir)
        outputs = bwm_behavior._final_outputs(work_dir)
        _write_upgrade_state(work_dir, {"status": "rewriting_sessions", "updated_at": bwm_shared.now_iso(), "jobs": int(max(1, jobs))})
        _rewrite_session_shards(dataset_dir=work_dir, jobs=jobs, verbose=verbose)
        _write_upgrade_state(work_dir, {"status": "sessions_rewritten", "updated_at": bwm_shared.now_iso(), "jobs": int(max(1, jobs))})
        _write_upgrade_state(work_dir, {"status": "refreshing_features", "updated_at": bwm_shared.now_iso(), "jobs": int(max(1, jobs))})
        _refresh_behavior_metadata_from_shards(dataset_dir=work_dir, outputs=outputs, jobs=jobs, verbose=verbose)
        _refresh_sidecars(dataset_dir=work_dir, outputs=outputs, source_dataset_dir=source_dataset_dir, elapsed_s=perf_counter() - started_at)
        if work_dir != target_dir:
            # Source and target may now share a directory name (e.g. the raw
            # DATASET_VERSION build and the compressed TARGET_DATASET_VERSION
            # release are both stamped "2.0.0" for the combined #18/#19
            # release). Everything needed from an existing target_dir has
            # already been hardlink-copied into work_dir above, so it is safe
            # to clear a stale/duplicate target_dir before the final rename.
            if target_dir.exists():
                shutil.rmtree(target_dir)
            work_dir.rename(target_dir)
        _write_upgrade_state(target_dir, {"status": "complete", "updated_at": bwm_shared.now_iso(), "jobs": int(max(1, jobs))})
    except Exception as exc:
        _write_upgrade_state(work_dir, {"status": "failed", "updated_at": bwm_shared.now_iso(), "error": str(exc)})
        raise

    outputs = _maybe_release(bwm_behavior._final_outputs(target_dir))
    return UpgradeOutputs(
        dataset_dir=target_dir,
        sessions_path=outputs.sessions_path,
        trials_path=outputs.trials_path,
        events_path=outputs.events_path,
        wheel_availability_path=outputs.wheel_availability_path,
        pose_availability_path=outputs.pose_availability_path,
        trial_behavior_features_path=outputs.trial_behavior_features_path,
        wheel_trial_features_path=outputs.wheel_trial_features_path,
        pose_trial_features_path=outputs.pose_trial_features_path,
        event_aligned_behavior_features_path=outputs.event_aligned_behavior_features_path,
        behavior_session_features_path=outputs.behavior_session_features_path,
        movement_state_epochs_path=outputs.movement_state_epochs_path,
        quiescence_state_epochs_path=outputs.quiescence_state_epochs_path,
        behavior_state_session_features_path=outputs.behavior_state_session_features_path,
        manifest_path=outputs.manifest_path,
        schema_path=outputs.schema_path,
        provenance_path=outputs.provenance_path,
        build_report_path=outputs.build_report_path,
        summary_path=outputs.summary_path,
        archive_path=outputs.archive_path,
        archive_checksum_path=outputs.archive_checksum_path,
    )


def _rewrite_session_shards(*, dataset_dir: Path, jobs: int, verbose: bool) -> None:
    sessions_dir = dataset_dir / "sessions"
    shard_paths = sorted(sessions_dir.glob("*.zip"))
    total = len(shard_paths)
    phase_started_at = perf_counter()
    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as executor:
        futures = {
            executor.submit(_rewrite_one_session_shard, shard_path=shard_path): shard_path
            for shard_path in shard_paths
        }
        for future in as_completed(futures):
            shard_path = futures[future]
            future.result()
            completed += 1
            _emit_progress(
                dataset_dir=dataset_dir,
                phase="rewriting_sessions",
                completed=completed,
                total=total,
                started_at=phase_started_at,
                shard_name=shard_path.name,
                verbose=verbose,
            )


def _rewrite_one_session_shard(*, shard_path: Path) -> None:
    if _shard_already_upgraded(shard_path):
        return
    shard = bwm_shared.read_array_shard(shard_path)
    metadata = dict(shard["meta"])
    metadata["dataset_version"] = TARGET_DATASET_VERSION
    temp_path = shard_path.with_suffix(".tmp")
    bwm_behavior_compression.write_behavior_session_shard(
        temp_path,
        metadata=metadata,
        arrays=shard["arrays"],
        strategy_name=COMPRESSION_PROFILE,
    )
    temp_path.replace(shard_path)


def _refresh_behavior_metadata_from_shards(*, dataset_dir: Path, outputs: bwm_behavior.BuildOutputs, jobs: int, verbose: bool) -> None:
    sessions_df = pd.read_parquet(outputs.sessions_path)
    trials_df = pd.read_parquet(outputs.trials_path)
    trial_behavior_features_df = bwm_behavior._compute_trial_behavior_features(trials_df)
    refresh_started_at = perf_counter()
    feature_cache_dir = dataset_dir / FEATURE_CACHE_DIRNAME
    feature_cache_dir.mkdir(parents=True, exist_ok=True)
    trial_groups = bwm_behavior._trial_groups_by_eid(
        trials_df,
        columns=[col for col in ["eid", "trial_id", "stimOn_times", "goCue_times", "firstMovement_times", "response_times", "feedback_times"] if col in trials_df.columns],
    )
    shard_paths = sorted((dataset_dir / "sessions").glob("*.zip"))
    total = len(shard_paths)
    pending_paths = [path for path in shard_paths if not _feature_cache_path(feature_cache_dir, path).exists()]
    completed = total - len(pending_paths)
    if completed:
        _emit_progress(
            dataset_dir=dataset_dir,
            phase="refreshing_features",
            completed=completed,
            total=total,
            started_at=refresh_started_at,
            shard_name="resume",
            verbose=verbose,
            force=True,
        )
    if pending_paths:
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as executor:
            futures = {
                executor.submit(_write_feature_cache_for_shard, shard_path=shard_path, cache_path=_feature_cache_path(feature_cache_dir, shard_path), trial_groups=trial_groups): shard_path
                for shard_path in pending_paths
            }
            for future in as_completed(futures):
                shard_path = futures[future]
                future.result()
                completed += 1
                _emit_progress(
                    dataset_dir=dataset_dir,
                    phase="refreshing_features",
                    completed=completed,
                    total=total,
                    started_at=refresh_started_at,
                    shard_name=shard_path.name,
                    verbose=verbose,
                )
    rows = bwm_behavior_compression._empty_feature_row_buckets()
    for shard_path in shard_paths:
        cache_rows = json.loads(_feature_cache_path(feature_cache_dir, shard_path).read_text(encoding="utf-8"))
        bwm_behavior_compression._extend_feature_row_buckets(rows, cache_rows)
    (
        wheel_availability_df,
        pose_availability_df,
        wheel_trial_features_df,
        pose_trial_features_df,
        event_aligned_behavior_features_df,
        movement_state_epochs_df,
        quiescence_state_epochs_df,
        behavior_state_session_features_df,
    ) = bwm_behavior_compression._coerce_feature_tables(bwm_behavior_compression._feature_tables_from_rows(rows))

    sessions_df = sessions_df.drop(columns=[col for col in ("wheel_present", "pose_present", "present_cameras") if col in sessions_df.columns])
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
    behavior_session_features_df = bwm_behavior._build_behavior_session_features(
        sessions_df=sessions_df,
        trial_behavior_features_df=trial_behavior_features_df,
        wheel_availability_df=wheel_availability_df,
        pose_availability_df=pose_availability_df,
    )

    _write_refreshed_metadata_tables(
        dataset_dir=dataset_dir,
        outputs=outputs,
        sessions_df=sessions_df,
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
        written_tables=set(bwm_behavior.EXPECTED_TABLE_OUTPUT_ATTRS.keys()) - {"trials", "events"},
        operation="upgrade_bwm_behavior_dataset_compression",
    )


def refresh_upgraded_bwm_behavior_dataset_from_shards(
    *,
    dataset_dir: Path,
    jobs: int = DEFAULT_UPGRADE_JOBS,
    verbose: bool = True,
    write_tables: set[str] | None = None,
) -> bwm_behavior.BuildOutputs:
    outputs = bwm_behavior._final_outputs(dataset_dir)
    sessions_df = pd.read_parquet(outputs.sessions_path)
    trials_df = pd.read_parquet(outputs.trials_path)
    trial_behavior_features_df = bwm_behavior._compute_trial_behavior_features(trials_df)
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
    behavior_session_features_df = bwm_behavior._build_behavior_session_features(
        sessions_df=sessions_df,
        trial_behavior_features_df=trial_behavior_features_df,
        wheel_availability_df=wheel_availability_df,
        pose_availability_df=pose_availability_df,
    )
    if write_tables is None:
        write_tables = set(bwm_behavior.EXPECTED_TABLE_OUTPUT_ATTRS.keys()) - {"trials", "events"}
    _write_refreshed_metadata_tables(
        dataset_dir=dataset_dir,
        outputs=outputs,
        sessions_df=sessions_df,
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
        written_tables=set(write_tables),
        operation="refresh_upgraded_bwm_behavior_features_from_shards",
    )
    refresh_upgraded_bwm_behavior_sidecars(dataset_dir=dataset_dir)
    return outputs


def refresh_upgraded_bwm_behavior_sidecars(*, dataset_dir: Path) -> bwm_behavior.BuildOutputs:
    outputs = bwm_behavior._final_outputs(dataset_dir)
    source_dataset_dir = _resolve_source_dataset_dir(dataset_dir)
    _refresh_sidecars(dataset_dir=dataset_dir, outputs=outputs, source_dataset_dir=source_dataset_dir, elapsed_s=0.0)
    return outputs


def _write_refreshed_metadata_tables(
    *,
    dataset_dir: Path,
    outputs: bwm_behavior.BuildOutputs,
    sessions_df: pd.DataFrame,
    wheel_availability_df: pd.DataFrame,
    pose_availability_df: pd.DataFrame,
    trial_behavior_features_df: pd.DataFrame,
    wheel_trial_features_df: pd.DataFrame,
    pose_trial_features_df: pd.DataFrame,
    event_aligned_behavior_features_df: pd.DataFrame,
    behavior_session_features_df: pd.DataFrame,
    movement_state_epochs_df: pd.DataFrame,
    quiescence_state_epochs_df: pd.DataFrame,
    behavior_state_session_features_df: pd.DataFrame,
    written_tables: set[str],
    operation: str,
) -> None:
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
    for table_name in written_tables:
        path = getattr(outputs, bwm_behavior.EXPECTED_TABLE_OUTPUT_ATTRS[table_name])
        table_frames[table_name].to_parquet(path, engine=bwm_behavior.PARQUET_ENGINE, compression=bwm_behavior.PARQUET_COMPRESSION, index=False)
    (dataset_dir / "feature_refresh_report.yaml").write_text(
        yaml.safe_dump(
            {
                "dataset_dir": str(dataset_dir),
                "generated_at": bwm_shared.now_iso(),
                "operation": operation,
                "compression_profile": COMPRESSION_PROFILE,
                "trial_behavior_feature_rows": int(len(trial_behavior_features_df)),
                "wheel_trial_feature_rows": int(len(wheel_trial_features_df)),
                "pose_trial_feature_rows": int(len(pose_trial_features_df)),
                "event_aligned_behavior_feature_rows": int(len(event_aligned_behavior_features_df)),
                "behavior_session_feature_rows": int(len(behavior_session_features_df)),
                "movement_state_epoch_rows": int(len(movement_state_epochs_df)),
                "quiescence_state_epoch_rows": int(len(quiescence_state_epochs_df)),
                "behavior_state_session_feature_rows": int(len(behavior_state_session_features_df)),
                "written_tables": sorted(written_tables),
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _resolve_source_dataset_dir(dataset_dir: Path) -> Path:
    provenance_path = dataset_dir / "provenance.yaml"
    provenance = yaml.safe_load(provenance_path.read_text(encoding="utf-8")) if provenance_path.exists() else {}
    source_value = (provenance or {}).get("source_dataset")
    if source_value:
        return Path(str(source_value))
    return dataset_dir.parent / bwm_behavior.RAW_BUILD_DIRNAME


def _refresh_sidecars(*, dataset_dir: Path, outputs: bwm_behavior.BuildOutputs, source_dataset_dir: Path, elapsed_s: float) -> None:
    stats = bwm_behavior._summarize_existing_behavior_store(outputs)
    schema = _build_schema(outputs)
    provenance = _build_provenance(source_dataset_dir=source_dataset_dir)
    build_report = _build_report(dataset_dir=dataset_dir, outputs=outputs, behavior_stats=stats, elapsed_s=elapsed_s)
    summary = _build_summary(outputs=outputs, behavior_stats=stats)
    outputs.schema_path.write_text(yaml.safe_dump(schema, sort_keys=False), encoding="utf-8")
    outputs.provenance_path.write_text(yaml.safe_dump(provenance, sort_keys=False), encoding="utf-8")
    outputs.build_report_path.write_text(yaml.safe_dump(build_report, sort_keys=False), encoding="utf-8")
    outputs.summary_path.write_text(summary, encoding="utf-8")
    manifest = bwm_shared.build_manifest(dataset_name=DATASET_NAME, dataset_version=TARGET_DATASET_VERSION, dataset_dir=dataset_dir)
    outputs.manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def _build_schema(outputs: bwm_behavior.BuildOutputs) -> dict[str, Any]:
    return {
        "dataset_name": DATASET_NAME,
        "dataset_version": TARGET_DATASET_VERSION,
        "schema_version": 3,
        "compression_profile": COMPRESSION_PROFILE,
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
                "container_format": TARGET_SIGNAL_CONTAINER_FORMAT,
                "compression_profile": COMPRESSION_PROFILE,
            }
        },
    }


def _build_provenance(*, source_dataset_dir: Path) -> dict[str, Any]:
    return {
        "dataset_name": DATASET_NAME,
        "dataset_version": TARGET_DATASET_VERSION,
        "created_at": bwm_shared.now_iso(),
        "source_dataset": str(source_dataset_dir),
        "compression_profile": COMPRESSION_PROFILE,
        "storage": {
            "signal_format": TARGET_SIGNAL_CONTAINER_FORMAT,
            "signal_codec": "behavior_semantic_v2",
            "metadata_compression": bwm_behavior.PARQUET_COMPRESSION,
        },
    }


def _build_report(*, dataset_dir: Path, outputs: bwm_behavior.BuildOutputs, behavior_stats: dict[str, Any], elapsed_s: float) -> dict[str, Any]:
    return {
        "dataset_name": DATASET_NAME,
        "dataset_version": TARGET_DATASET_VERSION,
        "source_dataset": str(dataset_dir.parent / bwm_behavior.RAW_BUILD_DIRNAME),
        "compression_profile": COMPRESSION_PROFILE,
        "build_timestamp": bwm_shared.now_iso(),
        "elapsed_seconds": float(elapsed_s),
        "stores": {"behavior_sessions": behavior_stats},
        "row_counts": {
            "sessions": int(len(pd.read_parquet(outputs.sessions_path))),
            "wheel_availability": int(len(pd.read_parquet(outputs.wheel_availability_path))),
            "pose_availability": int(len(pd.read_parquet(outputs.pose_availability_path))),
            "wheel_trial_features": int(len(pd.read_parquet(outputs.wheel_trial_features_path))),
            "pose_trial_features": int(len(pd.read_parquet(outputs.pose_trial_features_path))),
            "event_aligned_behavior_features": int(len(pd.read_parquet(outputs.event_aligned_behavior_features_path))),
            "behavior_session_features": int(len(pd.read_parquet(outputs.behavior_session_features_path))),
            "movement_state_epochs": int(len(pd.read_parquet(outputs.movement_state_epochs_path))),
            "quiescence_state_epochs": int(len(pd.read_parquet(outputs.quiescence_state_epochs_path))),
            "behavior_state_session_features": int(len(pd.read_parquet(outputs.behavior_state_session_features_path))),
        },
    }


def _build_summary(*, outputs: bwm_behavior.BuildOutputs, behavior_stats: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# BWM Behavior Compression Upgrade Summary",
            "",
            f"- Dataset version: `{TARGET_DATASET_VERSION}`",
            f"- Compression profile: `{COMPRESSION_PROFILE}`",
            f"- Session shards written: {behavior_stats['sessions_written']:,}",
            f"- Wheel sessions written: {behavior_stats['wheel_sessions_written']:,}",
            f"- Pose sessions written: {behavior_stats['pose_sessions_written']:,}",
            f"- Movement state epoch rows: {len(pd.read_parquet(outputs.movement_state_epochs_path)):,}",
            f"- Quiescence state epoch rows: {len(pd.read_parquet(outputs.quiescence_state_epochs_path)):,}",
            f"- Schema: `{outputs.schema_path}`",
            f"- Provenance: `{outputs.provenance_path}`",
            "",
        ]
    )


def _resolve_upgrade_dir(parent: Path, *, target_dir: Path, resume: bool) -> tuple[Path, bool]:
    if target_dir.exists() and resume:
        return target_dir, True
    if resume:
        candidates = sorted([path for path in parent.glob(f".upgrade-{DATASET_NAME}-{TARGET_DATASET_VERSION}-*") if path.is_dir()], key=lambda path: path.stat().st_mtime, reverse=True)
        if candidates:
            return candidates[0], True
    return Path(mkdtemp(prefix=f".upgrade-{DATASET_NAME}-{TARGET_DATASET_VERSION}-", dir=parent)), False


def _write_upgrade_state(work_dir: Path, state: dict[str, Any]) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "build_state.yaml").write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")


def _emit_progress(
    *,
    dataset_dir: Path,
    phase: str,
    completed: int,
    total: int,
    started_at: float,
    shard_name: str,
    verbose: bool,
    force: bool = False,
) -> None:
    if total <= 0:
        return
    elapsed_s = max(perf_counter() - started_at, 1e-9)
    rate = completed / elapsed_s
    remaining = max(total - completed, 0)
    eta_s = remaining / rate if rate > 0 else None
    should_emit = force or completed == 1 or completed == total or completed % PROGRESS_UPDATE_EVERY == 0
    if not should_emit:
        return
    state = {
        "status": phase,
        "updated_at": bwm_shared.now_iso(),
        "completed_shards": int(completed),
        "total_shards": int(total),
        "progress_fraction": float(completed / total),
        "elapsed_seconds": float(elapsed_s),
        "eta_seconds": (float(eta_s) if eta_s is not None else None),
        "latest_shard": shard_name,
    }
    _write_upgrade_state(dataset_dir, state)
    if verbose:
        eta_text = f", eta={eta_s/60:.1f}m" if eta_s is not None else ""
        print(f"{phase}: {completed}/{total} ({completed/total:.1%}) latest={shard_name}{eta_text}")


def _feature_cache_path(feature_cache_dir: Path, shard_path: Path) -> Path:
    return feature_cache_dir / f"{shard_path.stem}.json"


def _write_feature_cache_for_shard(*, shard_path: Path, cache_path: Path, trial_groups: dict[str, pd.DataFrame]) -> None:
    rows = bwm_behavior_compression._feature_rows_for_shard(shard_path=shard_path, trial_groups=trial_groups)
    cache_path.write_text(json.dumps(rows, sort_keys=True), encoding="utf-8")


def _shard_already_upgraded(shard_path: Path) -> bool:
    try:
        with zipfile.ZipFile(shard_path, mode="r") as zf:
            meta = json.loads(zf.read("meta.json").decode("utf-8"))
    except Exception:
        return False
    compression = meta.get("compression", {})
    return (
        meta.get("format") in bwm_behavior.BEHAVIOR_SESSION_SHARD_FORMATS_V2
        and meta.get("dataset_version") == TARGET_DATASET_VERSION
        and compression.get("profile") == COMPRESSION_PROFILE
    )


def _upgrade_completed(target_dir: Path) -> bool:
    state_path = target_dir / "build_state.yaml"
    if not state_path.exists():
        return False
    state = yaml.safe_load(state_path.read_text(encoding="utf-8")) or {}
    return state.get("status") == "complete"


def _copy_tree_hardlink_preferred(src: Path, dst: Path) -> None:
    def _copy_file(link_src: str, link_dst: str) -> str:
        try:
            os.link(link_src, link_dst)
        except OSError:
            shutil.copy2(link_src, link_dst)
        return link_dst

    dst.mkdir(parents=True, exist_ok=True)
    for child in src.iterdir():
        target = dst / child.name
        if child.is_dir():
            shutil.copytree(child, target, copy_function=_copy_file)
        else:
            _copy_file(str(child), str(target))
