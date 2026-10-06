from __future__ import annotations

from dataclasses import dataclass
import os
from datetime import datetime, timezone
from pathlib import Path
import shutil
from tempfile import gettempdir, mkdtemp
from typing import Any

_MPLCONFIGDIR = Path(gettempdir()) / "ibl-ai-agent-matplotlib"
_MPLCONFIGDIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_MPLCONFIGDIR))

from brainwidemap import bwm_query, download_aggregate_tables  # noqa: E402
from iblatlas.regions import BrainRegions  # noqa: E402
from ibl_ai_agent.core.access import require_online  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402


DATASET_NAME = "bwm_simple"
DATASET_VERSION = "1.0.0"
FREEZE = "2023_12_bwm_release"
SORTER_REVISION = "2024-05-06"
GOOD_UNIT_THRESHOLD = 1.0
PARQUET_ENGINE = "pyarrow"
PARQUET_COMPRESSION = "zstd"
INVALID_ACRONYMS = {"void", "root"}
INVALID_BERYL_ACRONYMS = {"void", "root"}
OPENALYX_BASE_URL = "https://openalyx.internationalbrainlab.org"
DOMAIN_DIRS = ("openalyx.internationalbrainlab.org", "alyx.internationalbrainlab.org")


class BuildError(RuntimeError):
    """Raised when the dataset build cannot complete successfully."""


@dataclass(frozen=True)
class BuildConfig:
    output_root: Path
    cache_root: Path
    allow_remote_fetch: bool = False
    limit_insertions: int | None = None


@dataclass(frozen=True)
class BuildOutputs:
    dataset_dir: Path
    insertions_path: Path
    units_path: Path
    trials_path: Path
    channels_path: Path
    metadata_path: Path
    build_report_path: Path
    summary_path: Path


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    message: str


def build_bwm_simple_dataset(config: BuildConfig) -> BuildOutputs:
    target_dir = config.output_root / DATASET_NAME / DATASET_VERSION
    if target_dir.exists():
        raise BuildError(f"Output directory already exists: {target_dir}")

    tmp_parent = target_dir.parent
    tmp_parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(mkdtemp(prefix=f".{DATASET_NAME}-{DATASET_VERSION}-", dir=tmp_parent))

    try:
        roster = _load_roster(limit_insertions=config.limit_insertions)
        one_remote = _make_one(config.cache_root, mode="remote") if config.allow_remote_fetch else None
        clusters_path = _resolve_aggregate_table(
            config.cache_root,
            "clusters",
            allow_remote_fetch=config.allow_remote_fetch,
            one_remote=one_remote,
        )
        trials_path = _resolve_aggregate_table(
            config.cache_root,
            "trials",
            allow_remote_fetch=config.allow_remote_fetch,
            one_remote=one_remote,
        )

        units_df = _build_units(roster, clusters_path)
        trials_df = _build_trials(roster, trials_path)
        channels_df = _build_channels(roster, config.cache_root, allow_remote_fetch=config.allow_remote_fetch, one_remote=one_remote)
        insertions_df = _build_insertions(roster, units_df, trials_df)

        _sort_frames(insertions_df, units_df, trials_df, channels_df)

        paths = _write_tables(tmp_dir, insertions_df, units_df, trials_df, channels_df)
        metadata = _build_metadata(clusters_path, trials_path, paths)
        _write_yaml(paths.metadata_path, metadata)

        issues = _validate(insertions_df, units_df, trials_df, channels_df)
        errors = [issue.message for issue in issues if issue.severity == "error"]
        warnings = [issue.message for issue in issues if issue.severity == "warning"]
        if errors:
            raise BuildError("Validation failed:\n" + "\n".join(errors))

        build_report = _build_report(
            config=config,
            clusters_path=clusters_path,
            trials_path=trials_path,
            insertions_df=insertions_df,
            units_df=units_df,
            trials_df=trials_df,
            channels_df=channels_df,
            warnings=warnings,
        )
        _write_yaml(paths.build_report_path, build_report)
        _write_summary(
            paths.summary_path,
            insertions_df=insertions_df,
            units_df=units_df,
            trials_df=trials_df,
            channels_df=channels_df,
        )

        target_dir.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir.rename(target_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    return BuildOutputs(
        dataset_dir=target_dir,
        insertions_path=target_dir / "insertions.pqt",
        units_path=target_dir / "units.pqt",
        trials_path=target_dir / "trials.pqt",
        channels_path=target_dir / "channels.pqt",
        metadata_path=target_dir / "metadata.yaml",
        build_report_path=target_dir / "build_report.yaml",
        summary_path=target_dir / "SUMMARY.md",
    )


def _load_roster(*, limit_insertions: int | None = None) -> pd.DataFrame:
    roster = (
        bwm_query(freeze=FREEZE)
        .drop_duplicates(subset="pid")
        .reset_index(drop=True)
        .copy()
    )
    if limit_insertions is not None:
        roster = roster.head(limit_insertions).reset_index(drop=True)
    roster["date"] = roster["date"].astype(str)
    roster["session_number"] = roster["session_number"].astype(np.int16)
    return roster


def _make_one(cache_root: Path, *, mode: str) -> Any:
    require_online()
    from one.api import ONE

    return ONE(
        base_url=OPENALYX_BASE_URL,
        mode=mode,
        silent=True,
        cache_dir=str(cache_root),
    )


def _resolve_aggregate_table(
    cache_root: Path,
    table_type: str,
    *,
    allow_remote_fetch: bool,
    one_remote: Any | None,
) -> Path:
    for domain in DOMAIN_DIRS:
        path = cache_root / domain / "bwm_tables" / f"{table_type}.pqt"
        if path.exists():
            return path

    if not allow_remote_fetch or one_remote is None:
        raise BuildError(
            f"Missing required aggregate table {table_type}.pqt under {cache_root}; "
            "remote fetch is disabled."
        )

    target_path = cache_root / DOMAIN_DIRS[0] / "bwm_tables"
    target_path.mkdir(parents=True, exist_ok=True)
    return download_aggregate_tables(one_remote, target_path=target_path, type=table_type)


def _build_units(roster: pd.DataFrame, clusters_path: Path) -> pd.DataFrame:
    units = pd.read_parquet(clusters_path).copy()
    units = units.loc[units["pid"].isin(roster["pid"])].copy()
    units = units.loc[units["label"] >= GOOD_UNIT_THRESHOLD].copy()

    br = BrainRegions()
    atlas_ids = units["atlas_id"].to_numpy(dtype=np.int64, copy=False)
    units["acronym"] = br.id2acronym(atlas_ids).astype(object)
    units = units.loc[~units["acronym"].isin(INVALID_ACRONYMS)].copy()
    raw_acronyms = np.asarray(units["acronym"], dtype=object)
    units["beryl_acronym"] = br.acronym2acronym(raw_acronyms, mapping="Beryl").astype(object)
    invalid_beryl = pd.Series(units["beryl_acronym"]).isin(INVALID_BERYL_ACRONYMS)
    units.loc[invalid_beryl, "beryl_acronym"] = pd.NA
    units["beryl_id"] = pd.Series(pd.NA, index=units.index, dtype="Int32")
    valid_beryl = units["beryl_acronym"].notna()
    units.loc[valid_beryl, "beryl_id"] = br.acronym2id(
        np.asarray(units.loc[valid_beryl, "beryl_acronym"], dtype=object)
    ).astype(np.int32)

    units["label"] = units["label"].astype(np.float32)
    units["depths"] = units["depths"].astype(np.float32)
    units["firing_rate"] = units["firing_rate"].astype(np.float32)
    units["spike_count"] = units["spike_count"].round().astype(np.int32)
    units["cluster_id"] = units["cluster_id"].astype(np.int32)
    units["channels"] = units["channels"].astype(np.int32)
    units["atlas_id"] = units["atlas_id"].astype(np.int32)
    units["beryl_id"] = units["beryl_id"].astype("Int32")
    for name in ("x", "y", "z", "axial_um", "lateral_um"):
        if name in units.columns:
            units[name] = units[name].astype(np.float32)

    keep = [
        "pid",
        "eid",
        "cluster_id",
        "channels",
        "label",
        "atlas_id",
        "acronym",
        "beryl_id",
        "beryl_acronym",
        "x",
        "y",
        "z",
        "axial_um",
        "lateral_um",
        "depths",
        "spike_count",
        "firing_rate",
    ]
    units = units[keep]
    units = units.merge(
        roster[["pid", "subject", "date", "session_number", "lab", "probe_name"]],
        on="pid",
        how="left",
        validate="many_to_one",
    )
    ordered = [
        "pid",
        "eid",
        "cluster_id",
        "channels",
        "subject",
        "date",
        "session_number",
        "lab",
        "probe_name",
        "label",
        "atlas_id",
        "acronym",
        "beryl_id",
        "beryl_acronym",
        "x",
        "y",
        "z",
        "axial_um",
        "lateral_um",
        "depths",
        "spike_count",
        "firing_rate",
    ]
    return units[ordered]


def _build_trials(roster: pd.DataFrame, trials_path: Path) -> pd.DataFrame:
    trials = pd.read_parquet(trials_path).copy()
    sessions = roster[["eid", "subject", "date", "session_number", "lab"]].drop_duplicates("eid")
    trials = trials.loc[trials["eid"].isin(sessions["eid"])].copy()
    trials = trials.merge(sessions, on="eid", how="left", validate="many_to_one")
    trials["trial_id"] = trials.groupby("eid").cumcount().astype(np.int32)
    if "bwm_include" in trials.columns:
        trials["bwm_include"] = trials["bwm_include"].astype(bool)

    ordered = [
        "eid",
        "trial_id",
        "subject",
        "date",
        "session_number",
        "lab",
        "choice",
        "feedbackType",
        "probabilityLeft",
        "contrastLeft",
        "contrastRight",
        "intervals_0",
        "stimOn_times",
        "goCue_times",
        "firstMovement_times",
        "response_times",
        "feedback_times",
        "intervals_1",
        "bwm_include",
    ]
    optional = [
        "goCueTrigger_times",
        "stimOff_times",
        "rewardVolume",
        "reaction_time",
    ]
    ordered.extend([name for name in optional if name in trials.columns])
    return trials[ordered]


def _build_channels(
    roster: pd.DataFrame,
    cache_root: Path,
    *,
    allow_remote_fetch: bool,
    one_remote: Any | None,
) -> pd.DataFrame:
    br = BrainRegions()
    rows: list[pd.DataFrame] = []
    missing: list[str] = []

    for item in roster.itertuples(index=False):
        revision_dir = _resolve_revision_dir(
            cache_root,
            lab=str(item.lab),
            subject=str(item.subject),
            date=str(item.date),
            session_number=int(item.session_number),
            probe_name=str(item.probe_name),
            allow_remote_fetch=allow_remote_fetch,
            one_remote=one_remote,
            eid=str(item.eid),
        )
        if revision_dir is None:
            missing.append(str(item.pid))
            continue

        raw_ind = _load_npy_if_present(revision_dir / "channels.rawInd.npy")
        if raw_ind is None:
            missing.append(str(item.pid))
            continue

        n_channels = int(len(raw_ind))
        channel_df = pd.DataFrame(
            {
                "pid": str(item.pid),
                "eid": str(item.eid),
                "channel_id": np.arange(n_channels, dtype=np.int32),
                "subject": str(item.subject),
                "date": str(item.date),
                "session_number": np.full(n_channels, int(item.session_number), dtype=np.int16),
                "lab": str(item.lab),
                "probe_name": str(item.probe_name),
                "rawInd": raw_ind.astype(np.int32),
            }
        )

        labels = _load_npy_if_present(revision_dir / "channels.labels.npy")
        if labels is not None:
            channel_df["labels"] = labels.astype(np.int16)

        atlas_ids = _load_npy_if_present(revision_dir / "channels.brainLocationIds_ccf_2017.npy")
        if atlas_ids is not None:
            atlas_ids = atlas_ids.astype(np.int32)
            channel_df["brainLocationIds_ccf_2017"] = atlas_ids
            raw_acronyms = br.id2acronym(atlas_ids).astype(object)
            channel_df["acronym"] = raw_acronyms
            channel_df["beryl_acronym"] = br.acronym2acronym(raw_acronyms, mapping="Beryl").astype(object)
            invalid_beryl = pd.Series(channel_df["beryl_acronym"]).isin(INVALID_BERYL_ACRONYMS)
            channel_df.loc[invalid_beryl, "beryl_acronym"] = pd.NA
            channel_df["beryl_id"] = pd.Series(pd.NA, index=channel_df.index, dtype="Int32")
            valid_beryl = channel_df["beryl_acronym"].notna()
            channel_df.loc[valid_beryl, "beryl_id"] = br.acronym2id(
                np.asarray(channel_df.loc[valid_beryl, "beryl_acronym"], dtype=object)
            ).astype(np.int32)

        local_coords = _load_npy_if_present(revision_dir / "channels.localCoordinates.npy")
        if local_coords is not None:
            channel_df["localCoordinates_x"] = local_coords[:, 0].astype(np.float32)
            channel_df["localCoordinates_y"] = local_coords[:, 1].astype(np.float32)

        mlapdv = _load_npy_if_present(revision_dir / "channels.mlapdv.npy")
        if mlapdv is not None:
            channel_df["mlapdv_x"] = mlapdv[:, 0].astype(np.float32)
            channel_df["mlapdv_y"] = mlapdv[:, 1].astype(np.float32)
            channel_df["mlapdv_z"] = mlapdv[:, 2].astype(np.float32)

        rows.append(channel_df)

    if missing:
        raise BuildError(
            f"Missing required channel-source files for {len(missing)} insertions; "
            "partial builds are disallowed."
        )
    if not rows:
        raise BuildError("No channels were built from the local cache.")
    frames = pd.concat(rows, ignore_index=True)
    ordered = [
        "pid",
        "eid",
        "channel_id",
        "subject",
        "date",
        "session_number",
        "lab",
        "probe_name",
        "rawInd",
        "labels",
        "brainLocationIds_ccf_2017",
        "acronym",
        "beryl_id",
        "beryl_acronym",
        "localCoordinates_x",
        "localCoordinates_y",
        "mlapdv_x",
        "mlapdv_y",
        "mlapdv_z",
    ]
    ordered = [name for name in ordered if name in frames.columns]
    return frames[ordered]


def _build_insertions(roster: pd.DataFrame, units_df: pd.DataFrame, trials_df: pd.DataFrame) -> pd.DataFrame:
    unit_counts = (
        units_df.groupby("pid", as_index=True)
        .size()
        .rename("n_good_units")
        .astype(np.int32)
    )
    trial_counts = (
        trials_df.groupby("eid", as_index=True)
        .size()
        .rename("n_trials")
        .astype(np.int32)
    )
    included_counts = (
        trials_df.loc[trials_df["bwm_include"]]
        .groupby("eid", as_index=True)
        .size()
        .rename("n_included_trials")
        .astype(np.int32)
    )

    insertions = roster.copy()
    insertions = insertions.merge(unit_counts, on="pid", how="left")
    insertions = insertions.merge(trial_counts, on="eid", how="left")
    insertions = insertions.merge(included_counts, on="eid", how="left")
    for name in ("n_good_units", "n_trials", "n_included_trials"):
        insertions[name] = insertions[name].fillna(0).astype(np.int32)

    ordered = [
        "pid",
        "eid",
        "subject",
        "date",
        "session_number",
        "lab",
        "probe_name",
        "n_good_units",
        "n_trials",
        "n_included_trials",
    ]
    return insertions[ordered]


def _sort_frames(
    insertions_df: pd.DataFrame,
    units_df: pd.DataFrame,
    trials_df: pd.DataFrame,
    channels_df: pd.DataFrame,
) -> None:
    insertions_df.sort_values(
        ["lab", "subject", "date", "session_number", "probe_name"],
        inplace=True,
        kind="mergesort",
    )
    units_df.sort_values(
        ["lab", "subject", "date", "session_number", "probe_name", "cluster_id"],
        inplace=True,
        kind="mergesort",
    )
    trials_df.sort_values(
        ["lab", "subject", "date", "session_number", "trial_id"],
        inplace=True,
        kind="mergesort",
    )
    channels_df.sort_values(
        ["lab", "subject", "date", "session_number", "probe_name", "channel_id"],
        inplace=True,
        kind="mergesort",
    )


def _write_tables(
    tmp_dir: Path,
    insertions_df: pd.DataFrame,
    units_df: pd.DataFrame,
    trials_df: pd.DataFrame,
    channels_df: pd.DataFrame,
) -> BuildOutputs:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    insertions_path = tmp_dir / "insertions.pqt"
    units_path = tmp_dir / "units.pqt"
    trials_path = tmp_dir / "trials.pqt"
    channels_path = tmp_dir / "channels.pqt"
    metadata_path = tmp_dir / "metadata.yaml"
    build_report_path = tmp_dir / "build_report.yaml"
    summary_path = tmp_dir / "SUMMARY.md"

    for frame, path in (
        (insertions_df, insertions_path),
        (units_df, units_path),
        (trials_df, trials_path),
        (channels_df, channels_path),
    ):
        frame.to_parquet(
            path,
            engine=PARQUET_ENGINE,
            compression=PARQUET_COMPRESSION,
            index=False,
        )

    return BuildOutputs(
        dataset_dir=tmp_dir,
        insertions_path=insertions_path,
        units_path=units_path,
        trials_path=trials_path,
        channels_path=channels_path,
        metadata_path=metadata_path,
        build_report_path=build_report_path,
        summary_path=summary_path,
    )


def _build_metadata(clusters_path: Path, trials_path: Path, outputs: BuildOutputs) -> dict[str, Any]:
    return {
        "dataset_name": DATASET_NAME,
        "dataset_version": DATASET_VERSION,
        "schema_version": 1,
        "created_at": _now_iso(),
        "source": {
            "freeze": FREEZE,
            "sorter_revision": SORTER_REVISION,
            "good_unit_rule": f"label >= {GOOD_UNIT_THRESHOLD}",
            "clusters_table": _artifact_id(clusters_path),
            "trials_table": _artifact_id(trials_path),
        },
        "storage": {
            "format": "parquet",
            "parquet_engine": PARQUET_ENGINE,
            "compression": PARQUET_COMPRESSION,
            "index_written": False,
        },
        "outputs": {
            "insertions": outputs.insertions_path.name,
            "units": outputs.units_path.name,
            "trials": outputs.trials_path.name,
            "channels": outputs.channels_path.name,
            "build_report": outputs.build_report_path.name,
            "summary": outputs.summary_path.name,
        },
        "tables": {
            "insertions": _metadata_columns(
                [
                    ("pid", "source", True, False),
                    ("eid", "source", True, False),
                    ("subject", "source", True, False),
                    ("date", "source", True, False),
                    ("session_number", "source", True, False),
                    ("lab", "source", True, False),
                    ("probe_name", "source", True, False),
                    ("n_good_units", "derived", True, False),
                    ("n_trials", "derived", True, False),
                    ("n_included_trials", "derived", True, False),
                ],
                primary_key=["pid"],
            ),
            "units": _metadata_columns(
                [
                    ("pid", "source", True, False),
                    ("eid", "source", True, False),
                    ("cluster_id", "source", True, False),
                    ("channels", "source", True, False),
                    ("subject", "copied_from_join", True, False),
                    ("date", "copied_from_join", True, False),
                    ("session_number", "copied_from_join", True, False),
                    ("lab", "copied_from_join", True, False),
                    ("probe_name", "copied_from_join", True, False),
                    ("label", "filtered_source", True, False),
                    ("atlas_id", "source", True, False),
                    (
                        "acronym",
                        "derived",
                        True,
                        False,
                        {
                            "derived_from": ["atlas_id"],
                            "derivation": "BrainRegions.id2acronym(atlas_id)",
                            "note": "Allen acronym is derived from atlas_id rather than copied from a potentially coarser aggregate acronym field.",
                        },
                    ),
                    ("beryl_id", "derived", True, True),
                    (
                        "beryl_acronym",
                        "derived",
                        True,
                        True,
                        {
                            "derived_from": ["atlas_id", "acronym"],
                            "derivation": "BrainRegions.acronym2acronym(acronym, mapping='Beryl')",
                            "note": "Stored as null when the Allen-level mapping resolves only to Beryl root or void.",
                        },
                    ),
                    ("x", "source", False, True),
                    ("y", "source", False, True),
                    ("z", "source", False, True),
                    ("axial_um", "source", False, True),
                    ("lateral_um", "source", False, True),
                    ("depths", "source", True, False),
                    ("spike_count", "source", True, False),
                    ("firing_rate", "source", True, False),
                ],
                primary_key=["pid", "cluster_id"],
            ),
            "trials": _metadata_columns(
                [
                    ("eid", "source", True, False),
                    ("trial_id", "derived", True, False),
                    ("subject", "copied_from_join", True, False),
                    ("date", "copied_from_join", True, False),
                    ("session_number", "copied_from_join", True, False),
                    ("lab", "copied_from_join", True, False),
                    ("choice", "source", True, True),
                    ("feedbackType", "source", True, True),
                    ("probabilityLeft", "source", True, True),
                    ("contrastLeft", "source", True, True),
                    ("contrastRight", "source", True, True),
                    ("intervals_0", "source", True, True),
                    ("stimOn_times", "source", True, True),
                    ("goCue_times", "source", True, True),
                    ("firstMovement_times", "source", True, True),
                    ("response_times", "source", True, True),
                    ("feedback_times", "source", True, True),
                    ("intervals_1", "source", True, True),
                    ("bwm_include", "source", True, False),
                ],
                primary_key=["eid", "trial_id"],
            ),
            "channels": _metadata_columns(
                [
                    ("pid", "source", True, False),
                    ("eid", "source", True, False),
                    ("channel_id", "derived", True, False),
                    ("subject", "copied_from_join", True, False),
                    ("date", "copied_from_join", True, False),
                    ("session_number", "copied_from_join", True, False),
                    ("lab", "copied_from_join", True, False),
                    ("probe_name", "copied_from_join", True, False),
                    ("rawInd", "source", True, False),
                    ("labels", "source", False, True),
                    ("brainLocationIds_ccf_2017", "source", False, True),
                    (
                        "acronym",
                        "derived",
                        False,
                        True,
                        {
                            "derived_from": ["brainLocationIds_ccf_2017"],
                            "derivation": "BrainRegions.id2acronym(brainLocationIds_ccf_2017)",
                        },
                    ),
                    ("beryl_id", "derived", False, True),
                    (
                        "beryl_acronym",
                        "derived",
                        False,
                        True,
                        {
                            "derived_from": ["brainLocationIds_ccf_2017", "acronym"],
                            "derivation": "BrainRegions.acronym2acronym(acronym, mapping='Beryl')",
                            "note": "Stored as null when the Allen-level mapping resolves only to Beryl root or void.",
                        },
                    ),
                    ("localCoordinates_x", "source", False, True),
                    ("localCoordinates_y", "source", False, True),
                    ("mlapdv_x", "source", False, True),
                    ("mlapdv_y", "source", False, True),
                    ("mlapdv_z", "source", False, True),
                ],
                primary_key=["pid", "channel_id"],
            ),
        },
    }


def _metadata_columns(
    columns: list[tuple[str, str, bool, bool] | tuple[str, str, bool, bool, dict[str, Any]]],
    *,
    primary_key: list[str],
) -> dict[str, Any]:
    return {
        "primary_key": primary_key,
        "columns": [
            _metadata_column_entry(column) for column in columns
        ],
    }


def _metadata_column_entry(
    column: tuple[str, str, bool, bool] | tuple[str, str, bool, bool, dict[str, Any]]
) -> dict[str, Any]:
    if len(column) == 4:
        name, provenance, required, nullable = column
        extra: dict[str, Any] = {}
    else:
        name, provenance, required, nullable, extra = column
    entry = {
        "name": name,
        "provenance": provenance,
        "required": required,
        "nullable": nullable,
    }
    entry.update(extra)
    return entry


def _build_report(
    *,
    config: BuildConfig,
    clusters_path: Path,
    trials_path: Path,
    insertions_df: pd.DataFrame,
    units_df: pd.DataFrame,
    trials_df: pd.DataFrame,
    channels_df: pd.DataFrame,
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "dataset_name": DATASET_NAME,
        "dataset_version": DATASET_VERSION,
        "build_timestamp": _now_iso(),
        "build_mode": "remote-fetch-used" if config.allow_remote_fetch else "local-cache-only",
        "build_completeness_status": "complete",
        "source_artifacts": {
            "freeze": FREEZE,
            "sorter_revision": SORTER_REVISION,
            "clusters_table": _artifact_id(clusters_path),
            "trials_table": _artifact_id(trials_path),
        },
        "package_versions": _package_versions(),
        "row_counts": {
            "insertions": int(len(insertions_df)),
            "units": int(len(units_df)),
            "trials": int(len(trials_df)),
            "channels": int(len(channels_df)),
        },
        "null_counts": {
            "units": _null_counts(units_df, ["x", "y", "z", "axial_um", "lateral_um", "beryl_id", "beryl_acronym"]),
            "trials": _null_counts(trials_df, ["choice", "feedbackType", "bwm_include"]),
            "channels": _null_counts(
                channels_df,
                [
                    "brainLocationIds_ccf_2017",
                    "acronym",
                    "beryl_id",
                    "beryl_acronym",
                    "localCoordinates_x",
                    "localCoordinates_y",
                    "mlapdv_x",
                    "mlapdv_y",
                    "mlapdv_z",
                ],
            ),
        },
        "validation_results": {
            "status": "passed",
            "warnings": warnings,
            "errors": [],
        },
    }


def _validate(
    insertions_df: pd.DataFrame,
    units_df: pd.DataFrame,
    trials_df: pd.DataFrame,
    channels_df: pd.DataFrame,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    _assert_unique(insertions_df, ["pid"], issues, "insertions")
    _assert_unique(insertions_df, ["eid", "probe_name"], issues, "insertions")
    _assert_unique(units_df, ["pid", "cluster_id"], issues, "units")
    _assert_unique(trials_df, ["eid", "trial_id"], issues, "trials")
    _assert_unique(channels_df, ["pid", "channel_id"], issues, "channels")

    _assert_membership(units_df["pid"], insertions_df["pid"], issues, "units.pid missing from insertions.pid")
    _assert_membership(channels_df["pid"], insertions_df["pid"], issues, "channels.pid missing from insertions.pid")
    _assert_membership(units_df["eid"], insertions_df["eid"], issues, "units.eid missing from insertions.eid")
    _assert_membership(trials_df["eid"], insertions_df["eid"], issues, "trials.eid missing from insertions.eid")

    unit_channel = units_df[["pid", "channels"]].dropna().copy()
    unit_channel["channels"] = unit_channel["channels"].astype(np.int32)
    merged = unit_channel.merge(
        channels_df[["pid", "channel_id"]],
        left_on=["pid", "channels"],
        right_on=["pid", "channel_id"],
        how="left",
    )
    if merged["channel_id"].isna().any():
        issues.append(
            ValidationIssue(
                "error",
                "Some (pid, channels) pairs in units do not match channels.pqt.",
            )
        )

    expected_good = units_df.groupby("pid").size().rename("expected")
    actual_good = insertions_df.set_index("pid")["n_good_units"].rename("actual")
    if not np.array_equal(
        expected_good.to_numpy(dtype=np.int64),
        actual_good.loc[expected_good.index].to_numpy(dtype=np.int64),
    ):
        issues.append(ValidationIssue("error", "insertions.n_good_units does not match grouped units count."))

    expected_trials = trials_df.groupby("eid").size().rename("expected")
    actual_trials = insertions_df.drop_duplicates("eid").set_index("eid")["n_trials"].rename("actual")
    if not np.array_equal(
        expected_trials.to_numpy(dtype=np.int64),
        actual_trials.loc[expected_trials.index].to_numpy(dtype=np.int64),
    ):
        issues.append(ValidationIssue("error", "insertions.n_trials does not match grouped trials count."))

    expected_included = trials_df.loc[trials_df["bwm_include"]].groupby("eid").size().rename("expected")
    actual_included = insertions_df.drop_duplicates("eid").set_index("eid")["n_included_trials"].rename("actual")
    compare = actual_included.copy()
    compare.loc[:] = 0
    compare.update(expected_included.astype(np.int32))
    if not np.array_equal(
        compare.to_numpy(dtype=np.int64),
        actual_included.to_numpy(dtype=np.int64),
    ):
        issues.append(
            ValidationIssue(
                "error",
                "insertions.n_included_trials does not match grouped included-trial count.",
            )
        )

    for table_name, frame, cols in (
        ("units", units_df, ["x", "y", "z", "axial_um", "lateral_um"]),
        ("channels", channels_df, ["brainLocationIds_ccf_2017", "localCoordinates_x", "localCoordinates_y", "mlapdv_x", "mlapdv_y", "mlapdv_z"]),
    ):
        for col in cols:
            if col not in frame.columns:
                issues.append(ValidationIssue("warning", f"Optional column {table_name}.{col} is missing."))
                continue
            null_rate = float(frame[col].isna().mean())
            if null_rate > 0.25:
                issues.append(
                    ValidationIssue(
                        "warning",
                        f"High null rate for optional column {table_name}.{col}: {null_rate:.1%}",
                    )
                )

    return issues


def _assert_unique(df: pd.DataFrame, columns: list[str], issues: list[ValidationIssue], table: str) -> None:
    if df.duplicated(columns).any():
        issues.append(
            ValidationIssue("error", f"Duplicate key rows found in {table} for columns {columns}.")
        )


def _assert_membership(values: pd.Series, allowed: pd.Series, issues: list[ValidationIssue], message: str) -> None:
    if not values.isin(allowed).all():
        issues.append(ValidationIssue("error", message))


def _package_versions() -> dict[str, str]:
    import importlib.metadata

    versions: dict[str, str] = {}
    for package in ("brainwidemap", "ONE-api", "ibllib", "brainbox", "iblatlas", "pandas", "pyarrow"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            continue
    return versions


def _null_counts(df: pd.DataFrame, columns: list[str]) -> dict[str, int]:
    return {col: int(df[col].isna().sum()) for col in columns if col in df.columns}


def _resolve_revision_dir(
    cache_root: Path,
    *,
    lab: str,
    subject: str,
    date: str,
    session_number: int,
    probe_name: str,
    allow_remote_fetch: bool,
    one_remote: Any | None,
    eid: str,
) -> Path | None:
    candidates = [
        cache_root
        / domain
        / lab
        / "Subjects"
        / subject
        / date
        / f"{session_number:03d}"
        / "alf"
        / probe_name
        / "pykilosort"
        / f"#{SORTER_REVISION}#"
        for domain in DOMAIN_DIRS
    ]
    for path in candidates:
        if path.exists() and (path / "channels.rawInd.npy").exists():
            return path
    for path in candidates:
        if path.exists():
            return path

    if allow_remote_fetch and one_remote is not None:
        from brainbox.io.one import SpikeSortingLoader

        ssl = SpikeSortingLoader(one=one_remote, eid=eid, pname=probe_name)
        ssl.load_spike_sorting(revision=SORTER_REVISION, good_units=True)
        for path in candidates:
            if path.exists():
                return path
    return None


def _load_npy_if_present(path: Path) -> np.ndarray | None:
    if not path.exists():
        return None
    return np.load(path)


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _write_summary(
    path: Path,
    *,
    insertions_df: pd.DataFrame,
    units_df: pd.DataFrame,
    trials_df: pd.DataFrame,
    channels_df: pd.DataFrame,
) -> None:
    units_beryl_null = int(units_df["beryl_acronym"].isna().sum()) if "beryl_acronym" in units_df.columns else 0
    channels_beryl_null = int(channels_df["beryl_acronym"].isna().sum()) if "beryl_acronym" in channels_df.columns else 0
    coarse_top = (
        units_df.loc[units_df["beryl_acronym"].isna(), "acronym"]
        .value_counts(dropna=False)
        .head(15)
    )
    lines = [
        "# BWM Simple Dataset Build Summary",
        "",
        f"- Insertions: {len(insertions_df):,}",
        f"- Units: {len(units_df):,}",
        f"- Trials: {len(trials_df):,}",
        f"- Channels: {len(channels_df):,}",
        f"- Units with null Beryl: {units_beryl_null:,}",
        f"- Channels with null Beryl: {channels_beryl_null:,}",
        "",
        "## Top Remaining Coarse Allen Acronyms In Units",
        "",
    ]
    if coarse_top.empty:
        lines.append("None")
    else:
        lines.extend(f"- `{acronym}`: {count:,}" for acronym, count in coarse_top.items())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _artifact_id(path: Path) -> str:
    parts = list(path.parts)
    if "bwm_tables" in parts:
        idx = parts.index("bwm_tables")
        return "/".join(parts[idx:])
    return path.name
