from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import os
import shutil
from typing import Any

import typer
import yaml

from ibl_ai_agent.commands.common import fail
from ibl_ai_agent.notebook_open import notebook_url_from_log_file


def _iter_run_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)


def _dir_size_bytes(path: Path) -> int:
    total = 0
    for node in path.rglob("*"):
        if node.is_file():
            total += node.stat().st_size
    return total


def _format_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    unit = units[0]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            break
        value /= 1024.0
    return f"{value:.1f}{unit}"


def _should_delete(path: Path, *, index: int, keep_last: int, cutoff: datetime | None) -> bool:
    if index < keep_last:
        return False
    if cutoff is None:
        return True
    return datetime.fromtimestamp(path.stat().st_mtime) < cutoff


def _load_yaml_metadata(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _resolve_release_archive_identity(dataset_root: Path) -> tuple[str, str]:
    schema = _load_yaml_metadata(dataset_root / "schema.yaml")
    provenance = _load_yaml_metadata(dataset_root / "provenance.yaml")
    dataset_name = str(schema.get("dataset_name") or provenance.get("dataset_name") or dataset_root.parent.name)
    dataset_version = str(schema.get("dataset_version") or provenance.get("dataset_version") or dataset_root.name)
    if dataset_name not in {"bwm_behavior", "bwm_ephys"}:
        fail(
            "write-bwm-release-archive supports only built bwm_behavior and bwm_ephys datasets; "
            f"got dataset_name={dataset_name!r} from {dataset_root}."
        )
    return dataset_name, dataset_version


def _release_archive_excludes(dataset_name: str, dataset_version: str) -> set[str]:
    if dataset_name == "bwm_behavior":
        try:
            from ibl_ai_agent.datasets.bwm_behavior_upgrade import FEATURE_CACHE_DIRNAME, TARGET_DATASET_VERSION
        except Exception:
            return set()
        if dataset_version == TARGET_DATASET_VERSION:
            return {FEATURE_CACHE_DIRNAME}
    return set()


def register(app: typer.Typer) -> None:
    @app.command("build-bwm-simple-dataset")
    def build_bwm_simple_dataset_command(
        output_root: Path = typer.Option(
            Path("reports/datasets"),
            help="Root directory under which the versioned dataset directory will be created.",
        ),
        cache_root: Path = typer.Option(
            Path.home() / "Downloads" / "ONE",
            help="ONE cache root containing openalyx/alyx domain directories.",
        ),
        allow_remote_fetch: bool = typer.Option(
            False,
            help="Allow fetching missing inputs from OpenAlyx when they are not already in the local cache.",
        ),
        limit_insertions: int | None = typer.Option(
            None,
            min=1,
            help="Build only the first N insertions from the roster for a small smoke run.",
        ),
        jobs: int = typer.Option(
            max(1, (os.cpu_count() or 2) // 2),
            min=1,
            help="Number of parallel worker threads for build preprocessing.",
        ),
    ) -> None:
        """Build the versioned BWM simple dataset from cached BWM aggregates and ALF files."""
        try:
            from ibl_ai_agent.datasets.bwm_simple import BuildConfig, build_bwm_simple_dataset

            outputs = build_bwm_simple_dataset(
                BuildConfig(
                    output_root=output_root,
                    cache_root=cache_root,
                    allow_remote_fetch=allow_remote_fetch,
                    limit_insertions=limit_insertions,
                )
            )
        except Exception as exc:
            fail(str(exc))

        typer.echo(f"Dataset directory: {outputs.dataset_dir}")
        typer.echo(f"Insertions table: {outputs.insertions_path}")
        typer.echo(f"Units table: {outputs.units_path}")
        typer.echo(f"Trials table: {outputs.trials_path}")
        typer.echo(f"Channels table: {outputs.channels_path}")
        typer.echo(f"Metadata file: {outputs.metadata_path}")
        typer.echo(f"Build report: {outputs.build_report_path}")
        typer.echo(f"Summary report: {outputs.summary_path}")

    @app.command("write-bwm-release-archive")
    def write_bwm_release_archive_command(
        dataset_root: Path = typer.Option(
            ...,
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            resolve_path=True,
            help="Path to an existing built bwm_ephys or bwm_behavior dataset directory.",
        ),
        release_root: Path = typer.Option(
            Path("reports/releases"),
            help="Root directory under which the deterministic tar archive and checksum will be written.",
        ),
    ) -> None:
        """Write a tar/checksum release artifact for an existing dataset without rebuilding it."""
        try:
            from ibl_ai_agent.datasets.bwm_shared import write_release_archive

            dataset_name, dataset_version = _resolve_release_archive_identity(dataset_root)
            artifacts = write_release_archive(
                dataset_root,
                release_root=release_root,
                dataset_name=dataset_name,
                dataset_version=dataset_version,
                exclude_names=_release_archive_excludes(dataset_name, dataset_version),
            )
        except Exception as exc:
            fail(str(exc))

        typer.echo(f"Dataset directory: {dataset_root}")
        typer.echo(f"Dataset name: {dataset_name}")
        typer.echo(f"Dataset version: {dataset_version}")
        typer.echo(f"Release archive: {artifacts['archive_path']}")
        typer.echo(f"Release checksum: {artifacts['checksum_path']}")

    @app.command("prefetch-bwm-ephys-spikes")
    def prefetch_bwm_ephys_spikes_command(
        output_root: Path = typer.Option(
            Path("reports/datasets"),
            help="Root directory under which prefetch reports will be written.",
        ),
        cache_root: Path = typer.Option(
            Path.home() / "Downloads" / "ONE",
            help="ONE cache root containing openalyx/alyx domain directories.",
        ),
        limit_insertions: int | None = typer.Option(
            None,
            min=1,
            help="Prefetch only the first N insertions from the roster for a smaller run.",
        ),
        jobs: int = typer.Option(
            max(1, (os.cpu_count() or 2) // 2),
            min=1,
            help="Number of parallel spike-prefetch workers.",
        ),
    ) -> None:
        """Prefetch missing BWM spike arrays for the release roster into the local ONE cache."""
        try:
            from ibl_ai_agent.datasets.bwm_ephys import prefetch_bwm_ephys_spikes

            outputs = prefetch_bwm_ephys_spikes(
                cache_root=cache_root,
                output_root=output_root,
                limit_insertions=limit_insertions,
                jobs=jobs,
                verbose=True,
            )
        except Exception as exc:
            fail(str(exc))

        typer.echo(f"Prefetch report: {outputs.report_path}")
        typer.echo(f"Requested insertions: {outputs.requested_insertions}")
        typer.echo(f"Already present: {outputs.already_present_insertions}")
        typer.echo(f"Fetched now: {outputs.fetched_insertions}")
        typer.echo(f"Jobs: {outputs.jobs}")
        typer.echo(f"Failed: {outputs.failed_insertions}")
        typer.echo(f"Final present insertions: {outputs.final_present_insertions}")

    @app.command("prefetch-bwm-ephys-passive")
    def prefetch_bwm_ephys_passive_command(
        output_root: Path = typer.Option(
            Path("reports/datasets"),
            help="Root directory under which prefetch reports will be written.",
        ),
        cache_root: Path = typer.Option(
            Path.home() / "Downloads" / "ONE",
            help="ONE cache root containing openalyx/alyx domain directories.",
        ),
        limit_insertions: int | None = typer.Option(
            None,
            min=1,
            help="Prefetch only the first N insertions from the roster for a smaller run.",
        ),
        jobs: int = typer.Option(
            max(1, (os.cpu_count() or 2) // 2),
            min=1,
            help="Number of parallel passive-prefetch workers.",
        ),
    ) -> None:
        """Prefetch missing passive session datasets for the BWM roster into the local ONE cache."""
        try:
            from ibl_ai_agent.datasets.bwm_ephys import prefetch_bwm_ephys_passive

            outputs = prefetch_bwm_ephys_passive(
                cache_root=cache_root,
                output_root=output_root,
                limit_insertions=limit_insertions,
                jobs=jobs,
                verbose=True,
            )
        except Exception as exc:
            fail(str(exc))

        typer.echo(f"Prefetch report: {outputs.report_path}")
        typer.echo(f"Requested sessions: {outputs.requested_sessions}")
        typer.echo(f"Already present: {outputs.already_present_sessions}")
        typer.echo(f"Fetched now: {outputs.fetched_sessions}")
        typer.echo(f"Jobs: {outputs.jobs}")
        typer.echo(f"Failed: {outputs.failed_sessions}")
        typer.echo(f"Final present sessions: {outputs.final_present_sessions}")

    @app.command("inspect-bwm-ephys-cache")
    def inspect_bwm_ephys_cache_command(
        cache_root: Path = typer.Option(
            Path.home() / "Downloads" / "ONE",
            help="ONE cache root containing openalyx/alyx domain directories.",
        ),
        limit_insertions: int | None = typer.Option(
            None,
            min=1,
            help="Inspect only the first N insertions from the roster for a small smoke run.",
        ),
    ) -> None:
        """Inspect local cache coverage for the bwm_ephys builder without fetching or building."""
        try:
            from ibl_ai_agent.datasets.bwm_ephys import BuildConfig, inspect_bwm_ephys_cache

            report = inspect_bwm_ephys_cache(
                BuildConfig(
                    output_root=Path("reports/datasets"),
                    cache_root=cache_root,
                    allow_remote_fetch=False,
                    limit_insertions=limit_insertions,
                    prefetch_missing=False,
                    verbose=False,
                )
            )
        except Exception as exc:
            fail(str(exc))

        typer.echo(yaml.safe_dump(report, sort_keys=False).rstrip())

    @app.command("build-bwm-ephys-dataset")
    def build_bwm_ephys_dataset_command(
        output_root: Path = typer.Option(
            Path("reports/datasets"),
            help="Root directory under which the versioned dataset directory will be created.",
        ),
        cache_root: Path = typer.Option(
            Path.home() / "Downloads" / "ONE",
            help="ONE cache root containing openalyx/alyx domain directories.",
        ),
        allow_remote_fetch: bool = typer.Option(
            True,
            help="Fetch missing required inputs from OpenAlyx when they are not already in the local cache.",
        ),
        prefetch_missing: bool = typer.Option(
            True,
            help="Detect missing required assets and populate the local cache before building.",
        ),
        require_signals: bool = typer.Option(
            True,
            help="Fail if required spike assets remain missing after prefetch.",
        ),
        limit_insertions: int | None = typer.Option(
            None,
            min=1,
            help="Build only the first N insertions from the roster for a small smoke run.",
        ),
        spike_time_quantization_us: int = typer.Option(
            100,
            min=1,
            help="Spike time quantization in microseconds for integer/delta encodings.",
        ),
        jobs: int = typer.Option(
            max(1, (os.cpu_count() or 2) // 2),
            min=1,
            help="Number of parallel worker threads for build preprocessing.",
        ),
        spike_time_encoding: str = typer.Option(
            "delta_int_ticks",
            help="Spike time encoding; currently only delta_int_ticks is supported.",
        ),
    ) -> None:
        """Detect, fetch, verify, and build the versioned BWM ephys dataset."""
        try:
            from ibl_ai_agent.datasets.bwm_ephys import BuildConfig, build_bwm_ephys_dataset

            outputs = build_bwm_ephys_dataset(
                BuildConfig(
                    output_root=output_root,
                    cache_root=cache_root,
                    allow_remote_fetch=allow_remote_fetch,
                    limit_insertions=limit_insertions,
                    spike_time_quantization_us=spike_time_quantization_us,
                    spike_time_encoding=spike_time_encoding,
                    prefetch_missing=prefetch_missing,
                    require_signals=require_signals,
                    jobs=jobs,
                    verbose=True,
                )
            )
        except Exception as exc:
            fail(str(exc))

        typer.echo(f"Dataset directory: {outputs.dataset_dir}")
        typer.echo(f"Sessions table: {outputs.sessions_path}")
        typer.echo(f"Insertions table: {outputs.insertions_path}")
        typer.echo(f"Units table: {outputs.units_path}")
        typer.echo(f"Channels table: {outputs.channels_path}")
        typer.echo(f"Trials table: {outputs.trials_path}")
        typer.echo(f"Events table: {outputs.events_path}")
        typer.echo(f"Unit features table: {outputs.unit_features_path}")
        typer.echo(f"Event-response features table: {outputs.event_response_features_path}")
        typer.echo(f"Spikes shard directory: {outputs.spikes_store_path}")
        typer.echo(f"Spike metrics table: {outputs.spike_metrics_path}")
        typer.echo(f"Manifest: {outputs.manifest_path}")
        typer.echo(f"Schema: {outputs.schema_path}")
        typer.echo(f"Provenance: {outputs.provenance_path}")
        typer.echo(f"Prefetch report: {outputs.prefetch_report_path}")
        typer.echo(f"Build report: {outputs.build_report_path}")
        typer.echo(f"Summary report: {outputs.summary_path}")


    @app.command("inspect-bwm-behavior")
    def inspect_bwm_behavior_command(
        dataset_root: Path = typer.Option(
            Path("reports/datasets/bwm_behavior/2.0.0"),
            help="Path to an existing built bwm_behavior dataset directory to inspect.",
        ),
        cache_root: Path = typer.Option(
            Path.home() / "Downloads" / "ONE",
            help="ONE cache root containing openalyx/alyx domain directories.",
        ),
        include_cache: bool = typer.Option(
            True,
            help="Also inspect local cache coverage alongside the built dataset state.",
        ),
        limit_insertions: int | None = typer.Option(
            None,
            min=1,
            help="Inspect only the first N insertions from the roster for a small cache smoke run.",
        ),
    ) -> None:
        """Inspect the local bwm_behavior dataset state and optional cache coverage."""
        try:
            from ibl_ai_agent.datasets.bwm_behavior import BuildConfig, inspect_bwm_behavior_cache, inspect_bwm_behavior_dataset

            dataset_report = inspect_bwm_behavior_dataset(dataset_dir=dataset_root)
            payload: dict[str, object] = {"dataset": dataset_report}
            if include_cache:
                payload["cache"] = inspect_bwm_behavior_cache(
                    BuildConfig(
                        output_root=Path("reports/datasets"),
                        cache_root=cache_root,
                        allow_remote_fetch=False,
                        limit_insertions=limit_insertions,
                        prefetch_missing=False,
                        verbose=False,
                    )
                )
        except Exception as exc:
            fail(str(exc))

        typer.echo(yaml.safe_dump(payload, sort_keys=False).rstrip())

    @app.command("inspect-bwm-behavior-cache", hidden=True)
    def inspect_bwm_behavior_cache_command(
        cache_root: Path = typer.Option(
            Path.home() / "Downloads" / "ONE",
            help="ONE cache root containing openalyx/alyx domain directories.",
        ),
        limit_insertions: int | None = typer.Option(
            None,
            min=1,
            help="Inspect only the first N insertions from the roster for a small smoke run.",
        ),
    ) -> None:
        """Inspect local cache coverage for the bwm_behavior builder without fetching or building."""
        try:
            from ibl_ai_agent.datasets.bwm_behavior import BuildConfig, inspect_bwm_behavior_cache

            report = inspect_bwm_behavior_cache(
                BuildConfig(
                    output_root=Path("reports/datasets"),
                    cache_root=cache_root,
                    allow_remote_fetch=False,
                    limit_insertions=limit_insertions,
                    prefetch_missing=False,
                    verbose=False,
                )
            )
        except Exception as exc:
            fail(str(exc))

        typer.echo(yaml.safe_dump(report, sort_keys=False).rstrip())

    @app.command("refresh-bwm-ephys-features")
    def refresh_bwm_ephys_features_command(
        dataset_root: Path = typer.Option(
            Path("reports/datasets/bwm_ephys/1.0.0"),
            help="Path to an existing built bwm_ephys dataset directory.",
        ),
        cache_root: Path = typer.Option(
            Path.home() / "Downloads" / "ONE",
            help="ONE cache root containing aggregate tables and optional cached waveform files.",
        ),
        jobs: int = typer.Option(
            max(1, (os.cpu_count() or 2) // 2),
            min=1,
            help="Number of parallel workers for event-response feature refresh.",
        ),
    ) -> None:
        """Refresh BWM ephys unit features in place without rebuilding spike shards."""
        try:
            from ibl_ai_agent.datasets.bwm_ephys import refresh_bwm_ephys_features

            outputs = refresh_bwm_ephys_features(
                dataset_dir=dataset_root,
                cache_root=cache_root,
                jobs=jobs,
                verbose=True,
            )
        except Exception as exc:
            fail(str(exc))

        typer.echo(f"Dataset directory: {outputs.dataset_dir}")
        typer.echo(f"Unit features table: {outputs.unit_features_path}")
        typer.echo(f"Event-response features table: {outputs.event_response_features_path}")
        typer.echo(f"Spikes shard directory (reused): {outputs.spikes_store_path}")
        typer.echo(f"Manifest: {outputs.manifest_path}")
        typer.echo(f"Schema: {outputs.schema_path}")
        typer.echo(f"Provenance: {outputs.provenance_path}")
        typer.echo(f"Build report: {outputs.build_report_path}")
        typer.echo(f"Summary report: {outputs.summary_path}")

    @app.command("upgrade-bwm-ephys-passive")
    def upgrade_bwm_ephys_passive_command(
        source_dataset_root: Path = typer.Option(
            Path("reports/datasets/bwm_ephys/1.0.0"),
            help="Path to an existing built bwm_ephys/1.0.0 dataset directory.",
        ),
        output_root: Path = typer.Option(
            Path("reports/datasets"),
            help="Root directory under which the versioned 1.1.0 dataset directory will be created.",
        ),
        cache_root: Path = typer.Option(
            Path.home() / "Downloads" / "ONE",
            help="ONE cache root containing session ALF directories and optional passive extracted files.",
        ),
        jobs: int = typer.Option(
            max(1, (os.cpu_count() or 2) // 2),
            min=1,
            help="Number of workers for passive response feature computation.",
        ),
        resume: bool = typer.Option(
            True,
            help="Resume from the most recent partial passive-upgrade workdir when available.",
        ),
    ) -> None:
        """Upgrade bwm_ephys/1.0.0 into bwm_ephys/1.1.0 by adding passive-session tables/features."""
        try:
            from ibl_ai_agent.datasets.bwm_ephys_passive import upgrade_bwm_ephys_dataset_with_passive

            outputs = upgrade_bwm_ephys_dataset_with_passive(
                source_dataset_dir=source_dataset_root,
                output_root=output_root,
                cache_root=cache_root,
                jobs=jobs,
                resume=resume,
                verbose=True,
                release_root=Path("reports/releases"),
            )
        except Exception as exc:
            fail(str(exc))

        typer.echo(f"Dataset directory: {outputs.dataset_dir}")
        typer.echo(f"Passive session availability table: {outputs.passive_sessions_path}")
        typer.echo(f"Passive events table: {outputs.passive_events_path}")
        typer.echo(f"Passive response features table: {outputs.passive_response_features_path}")
        typer.echo(f"Schema: {outputs.schema_path}")
        typer.echo(f"Provenance: {outputs.provenance_path}")
        typer.echo(f"Build report: {outputs.build_report_path}")
        typer.echo(f"Summary report: {outputs.summary_path}")
        if outputs.archive_path is not None:
            typer.echo(f"Release archive: {outputs.archive_path}")
            typer.echo(f"Release checksum: {outputs.archive_checksum_path}")
        typer.echo(f"Manifest: {outputs.manifest_path}")

    @app.command("repair-bwm-ephys-spikes")
    def repair_bwm_ephys_spikes_command(
        dataset_root: Path = typer.Option(
            Path("reports/datasets/bwm_ephys/1.0.0"),
            help="Path to an existing built bwm_ephys dataset directory.",
        ),
        cache_root: Path = typer.Option(
            Path.home() / "Downloads" / "ONE",
            help="ONE cache root containing the cached spike arrays used for repair.",
        ),
        jobs: int = typer.Option(
            max(1, (os.cpu_count() or 2) // 2),
            min=1,
            help="Number of parallel repair workers.",
        ),
        limit_insertions: int | None = typer.Option(
            None,
            min=1,
            help="Repair only the first N failed/missing insertions.",
        ),
        pids: str | None = typer.Option(
            None,
            help="Comma-separated pid list to repair explicitly; defaults to failed/missing shards.",
        ),
    ) -> None:
        """Repair failed or missing BWM ephys spike shards in place using adaptive uint16/uint32 encoding."""
        try:
            from ibl_ai_agent.datasets.bwm_ephys import repair_bwm_ephys_spikes

            pid_list = [part.strip() for part in pids.split(",") if part.strip()] if pids else None
            outputs = repair_bwm_ephys_spikes(
                dataset_dir=dataset_root,
                cache_root=cache_root,
                jobs=jobs,
                limit_insertions=limit_insertions,
                pids=pid_list,
                verbose=True,
            )
        except Exception as exc:
            fail(str(exc))

        typer.echo(f"Dataset directory: {outputs.dataset_dir}")
        typer.echo(f"Spikes shard directory: {outputs.spikes_store_path}")
        typer.echo(f"Spike metrics table: {outputs.spike_metrics_path}")
        typer.echo(f"Manifest: {outputs.manifest_path}")
        typer.echo(f"Schema: {outputs.schema_path}")
        typer.echo(f"Provenance: {outputs.provenance_path}")
        typer.echo(f"Build report: {outputs.build_report_path}")
        typer.echo(f"Summary report: {outputs.summary_path}")

    @app.command("normalize-bwm-ephys-spikes")
    def normalize_bwm_ephys_spikes_command(
        dataset_root: Path = typer.Option(
            Path("reports/datasets/bwm_ephys/1.0.0"),
            help="Path to an existing built bwm_ephys dataset directory.",
        ),
        cache_root: Path = typer.Option(
            Path.home() / "Downloads" / "ONE",
            help="ONE cache root containing the cached spike arrays used for selective rebuilds.",
        ),
        jobs: int = typer.Option(
            max(1, (os.cpu_count() or 2) // 2),
            min=1,
            help="Number of parallel workers for any required shard rebuilds.",
        ),
        limit_insertions: int | None = typer.Option(
            None,
            min=1,
            help="Normalize only the first N selected insertions.",
        ),
        pids: str | None = typer.Option(
            None,
            help="Comma-separated pid list to normalize explicitly.",
        ),
        rewrite_all: bool = typer.Option(
            False,
            help="Force full shard rewrites instead of metadata-only normalization where possible.",
        ),
        no_validate_decode: bool = typer.Option(
            False,
            help="Skip decode validation when deciding whether old shards can be metadata-patched only.",
        ),
    ) -> None:
        """Normalize BWM ephys spike shards in place for a consistent on-disk format and metadata contract."""
        try:
            from ibl_ai_agent.datasets.bwm_ephys import normalize_bwm_ephys_spikes

            pid_list = [part.strip() for part in pids.split(",") if part.strip()] if pids else None
            outputs = normalize_bwm_ephys_spikes(
                dataset_dir=dataset_root,
                cache_root=cache_root,
                jobs=jobs,
                limit_insertions=limit_insertions,
                pids=pid_list,
                rewrite_all=rewrite_all,
                validate_decode=not no_validate_decode,
                verbose=True,
            )
        except Exception as exc:
            fail(str(exc))

        typer.echo(f"Dataset directory: {outputs.dataset_dir}")
        typer.echo(f"Spikes shard directory: {outputs.spikes_store_path}")
        typer.echo(f"Spike metrics table: {outputs.spike_metrics_path}")
        typer.echo(f"Manifest: {outputs.manifest_path}")
        typer.echo(f"Schema: {outputs.schema_path}")
        typer.echo(f"Provenance: {outputs.provenance_path}")
        typer.echo(f"Build report: {outputs.build_report_path}")
        typer.echo(f"Summary report: {outputs.summary_path}")

    @app.command("refresh-bwm-behavior")
    def refresh_bwm_behavior_command(
        dataset_root: Path = typer.Option(
            Path("reports/datasets/bwm_behavior/2.0.0"),
            help="Path to an existing built bwm_behavior dataset directory.",
        ),
        cache_root: Path = typer.Option(
            Path.home() / "Downloads" / "ONE",
            help="ONE cache root used only as a fallback when local shard refresh is unavailable.",
        ),
        jobs: int = typer.Option(
            max(1, (os.cpu_count() or 2) // 2),
            min=1,
            help="Number of parallel workers for shard-based or cache-based refresh.",
        ),
        force_refresh: bool = typer.Option(
            False,
            help="Force a full derived-table refresh even when the dataset already looks complete.",
        ),
        dry_run: bool = typer.Option(
            False,
            help="Report the planned action and diff without writing any files.",
        ),
    ) -> None:
        """Ensure an existing bwm_behavior dataset is complete and refresh only what is needed."""
        try:
            from ibl_ai_agent.datasets.bwm_behavior import ensure_bwm_behavior_dataset, inspect_bwm_behavior_dataset

            before = inspect_bwm_behavior_dataset(dataset_dir=dataset_root)
            outputs, after = ensure_bwm_behavior_dataset(
                dataset_dir=dataset_root,
                cache_root=cache_root,
                jobs=jobs,
                verbose=True,
                force_refresh=force_refresh,
                dry_run=dry_run,
            )
        except Exception as exc:
            fail(str(exc))

        if dry_run:
            typer.echo(yaml.safe_dump({"before": before, "after": after, "dry_run": True}, sort_keys=False).rstrip())
            return

        typer.echo(f"Dataset directory: {outputs.dataset_dir}")
        typer.echo(f"Wheel availability table: {outputs.wheel_availability_path}")
        typer.echo(f"Pose availability table: {outputs.pose_availability_path}")
        typer.echo(f"Trial behavior features: {outputs.trial_behavior_features_path}")
        typer.echo(f"Wheel trial features: {outputs.wheel_trial_features_path}")
        typer.echo(f"Pose trial features: {outputs.pose_trial_features_path}")
        typer.echo(f"Event-aligned behavior features: {outputs.event_aligned_behavior_features_path}")
        typer.echo(f"Behavior session features: {outputs.behavior_session_features_path}")
        typer.echo(f"Movement state epochs: {outputs.movement_state_epochs_path}")
        typer.echo(f"Quiescence state epochs: {outputs.quiescence_state_epochs_path}")
        typer.echo(f"Behavior state session features: {outputs.behavior_state_session_features_path}")
        typer.echo(f"Manifest: {outputs.manifest_path}")
        typer.echo(f"Schema: {outputs.schema_path}")
        typer.echo(f"Provenance: {outputs.provenance_path}")
        typer.echo(f"Build report: {outputs.build_report_path}")
        typer.echo(f"Summary report: {outputs.summary_path}")
        typer.echo(yaml.safe_dump({"before": before, "after": after}, sort_keys=False).rstrip())

    @app.command("refresh-bwm-behavior-features", hidden=True)
    def refresh_bwm_behavior_features_command(
        dataset_root: Path = typer.Option(
            Path("reports/datasets/bwm_behavior/1.0.0"),
            help="Path to an existing built bwm_behavior dataset directory.",
        ),
        cache_root: Path = typer.Option(
            Path.home() / "Downloads" / "ONE",
            help="ONE cache root containing behavior assets used to refresh derived features.",
        ),
        jobs: int = typer.Option(
            max(1, (os.cpu_count() or 2) // 2),
            min=1,
            help="Number of parallel workers for wheel/pose/event feature refresh.",
        ),
    ) -> None:
        """Refresh BWM behavior-derived feature tables in place without rebuilding session shards."""
        try:
            from ibl_ai_agent.datasets.bwm_behavior import refresh_bwm_behavior_features

            outputs = refresh_bwm_behavior_features(
                dataset_dir=dataset_root,
                cache_root=cache_root,
                jobs=jobs,
                verbose=True,
            )
        except Exception as exc:
            fail(str(exc))

        typer.echo(f"Dataset directory: {outputs.dataset_dir}")
        typer.echo(f"Wheel availability table: {outputs.wheel_availability_path}")
        typer.echo(f"Pose availability table: {outputs.pose_availability_path}")
        typer.echo(f"Trial behavior features: {outputs.trial_behavior_features_path}")
        typer.echo(f"Wheel trial features: {outputs.wheel_trial_features_path}")
        typer.echo(f"Pose trial features: {outputs.pose_trial_features_path}")
        typer.echo(f"Event-aligned behavior features: {outputs.event_aligned_behavior_features_path}")
        typer.echo(f"Behavior session features: {outputs.behavior_session_features_path}")
        typer.echo(f"Movement state epochs: {outputs.movement_state_epochs_path}")
        typer.echo(f"Quiescence state epochs: {outputs.quiescence_state_epochs_path}")
        typer.echo(f"Behavior state session features: {outputs.behavior_state_session_features_path}")
        typer.echo(f"Behavior shard directory (reused): {outputs.wheel_store_path}")
        typer.echo(f"Manifest: {outputs.manifest_path}")
        typer.echo(f"Schema: {outputs.schema_path}")
        typer.echo(f"Provenance: {outputs.provenance_path}")
        typer.echo(f"Build report: {outputs.build_report_path}")
        typer.echo(f"Summary report: {outputs.summary_path}")

    @app.command("build-bwm-behavior")
    def build_bwm_behavior_command(
        target_version: str = typer.Option(
            "2.0.0",
            help="Target dataset version to build. Supported values: 1.0.0, 2.0.0.",
        ),
        output_root: Path = typer.Option(
            Path("reports/datasets"),
            help="Root directory under which the versioned dataset directory will be created.",
        ),
        cache_root: Path = typer.Option(
            Path.home() / "Downloads" / "ONE",
            help="ONE cache root containing openalyx/alyx domain directories.",
        ),
        allow_remote_fetch: bool = typer.Option(
            True,
            help="Fetch missing required inputs from OpenAlyx when they are not already in the local cache.",
        ),
        prefetch_missing: bool = typer.Option(
            True,
            help="Detect missing required assets and populate the local cache before building.",
        ),
        require_signals: bool = typer.Option(
            False,
            help="Require full wheel+pose coverage before finalizing. When false, finalize a partial dataset and record gaps in the reports.",
        ),
        resume: bool = typer.Option(
            True,
            help="Resume from a partial build/upgrade directory when available.",
        ),
        limit_insertions: int | None = typer.Option(
            None,
            min=1,
            help="Build only the first N insertions from the roster for a small smoke run.",
        ),
        jobs: int = typer.Option(
            max(1, (os.cpu_count() or 2) // 2),
            min=1,
            help="Number of parallel worker threads for build preprocessing and upgrade refresh.",
        ),
    ) -> None:
        """Build the requested bwm_behavior dataset version, creating prerequisites when needed."""
        try:
            from ibl_ai_agent.datasets.bwm_behavior import BuildConfig, build_bwm_behavior_dataset
            from ibl_ai_agent.datasets.bwm_behavior_upgrade import upgrade_bwm_behavior_dataset_compression

            if target_version == "1.0.0":
                outputs = build_bwm_behavior_dataset(
                    BuildConfig(
                        output_root=output_root,
                        cache_root=cache_root,
                        allow_remote_fetch=allow_remote_fetch,
                        limit_insertions=limit_insertions,
                        prefetch_missing=prefetch_missing,
                        require_signals=require_signals,
                        resume=resume,
                        jobs=jobs,
                        verbose=True,
                    )
                )
            elif target_version == "2.0.0":
                source_dataset_root = output_root / "bwm_behavior" / "1.0.0"
                if not source_dataset_root.exists():
                    build_bwm_behavior_dataset(
                        BuildConfig(
                            output_root=output_root,
                            cache_root=cache_root,
                            allow_remote_fetch=allow_remote_fetch,
                            limit_insertions=limit_insertions,
                            prefetch_missing=prefetch_missing,
                            require_signals=require_signals,
                            resume=resume,
                            jobs=jobs,
                            verbose=True,
                        )
                    )
                outputs = upgrade_bwm_behavior_dataset_compression(
                    source_dataset_dir=source_dataset_root,
                    output_root=output_root,
                    jobs=jobs,
                    resume=resume,
                    verbose=True,
                    release_root=Path("reports/releases"),
                )
            else:
                fail(f"Unsupported target version for build-bwm-behavior: {target_version}")
                return
        except Exception as exc:
            fail(str(exc))

        typer.echo(f"Dataset directory: {outputs.dataset_dir}")
        typer.echo(f"Sessions table: {outputs.sessions_path}")
        typer.echo(f"Trials table: {outputs.trials_path}")
        typer.echo(f"Events table: {outputs.events_path}")
        typer.echo(f"Wheel availability table: {outputs.wheel_availability_path}")
        typer.echo(f"Pose availability table: {outputs.pose_availability_path}")
        typer.echo(f"Trial behavior features: {outputs.trial_behavior_features_path}")
        typer.echo(f"Wheel trial features: {outputs.wheel_trial_features_path}")
        typer.echo(f"Pose trial features: {outputs.pose_trial_features_path}")
        typer.echo(f"Event-aligned behavior features: {outputs.event_aligned_behavior_features_path}")
        typer.echo(f"Behavior session features: {outputs.behavior_session_features_path}")
        typer.echo(f"Movement state epochs: {outputs.movement_state_epochs_path}")
        typer.echo(f"Quiescence state epochs: {outputs.quiescence_state_epochs_path}")
        typer.echo(f"Behavior state session features: {outputs.behavior_state_session_features_path}")
        typer.echo(f"Manifest: {outputs.manifest_path}")
        typer.echo(f"Schema: {outputs.schema_path}")
        typer.echo(f"Provenance: {outputs.provenance_path}")
        typer.echo(f"Build report: {outputs.build_report_path}")
        typer.echo(f"Summary report: {outputs.summary_path}")
        if outputs.archive_path is not None:
            typer.echo(f"Release archive: {outputs.archive_path}")
            typer.echo(f"Release checksum: {outputs.archive_checksum_path}")

    @app.command("build-bwm-behavior-dataset", hidden=True)
    def build_bwm_behavior_dataset_command(
        output_root: Path = typer.Option(
            Path("reports/datasets"),
            help="Root directory under which the versioned dataset directory will be created.",
        ),
        cache_root: Path = typer.Option(
            Path.home() / "Downloads" / "ONE",
            help="ONE cache root containing openalyx/alyx domain directories.",
        ),
        allow_remote_fetch: bool = typer.Option(
            True,
            help="Fetch missing required inputs from OpenAlyx when they are not already in the local cache.",
        ),
        prefetch_missing: bool = typer.Option(
            True,
            help="Detect missing required assets and populate the local cache before building.",
        ),
        require_signals: bool = typer.Option(
            False,
            help="Require full wheel+pose coverage before finalizing. When false, finalize a partial dataset and record gaps in the reports.",
        ),
        resume: bool = typer.Option(
            True,
            help="Resume from the most recent partial build directory when available instead of starting from scratch.",
        ),
        limit_insertions: int | None = typer.Option(
            None,
            min=1,
            help="Build only the first N insertions from the roster for a small smoke run.",
        ),
        jobs: int = typer.Option(
            max(1, (os.cpu_count() or 2) // 2),
            min=1,
            help="Number of parallel worker threads for build preprocessing.",
        ),
    ) -> None:
        """Detect, fetch, verify, and build the versioned BWM behavior dataset."""
        try:
            from ibl_ai_agent.datasets.bwm_behavior import BuildConfig, build_bwm_behavior_dataset

            outputs = build_bwm_behavior_dataset(
                BuildConfig(
                    output_root=output_root,
                    cache_root=cache_root,
                    allow_remote_fetch=allow_remote_fetch,
                    limit_insertions=limit_insertions,
                    prefetch_missing=prefetch_missing,
                    require_signals=require_signals,
                    resume=resume,
                    jobs=jobs,
                    verbose=True,
                )
            )
        except Exception as exc:
            fail(str(exc))

        typer.echo(f"Dataset directory: {outputs.dataset_dir}")
        typer.echo(f"Sessions table: {outputs.sessions_path}")
        typer.echo(f"Trials table: {outputs.trials_path}")
        typer.echo(f"Events table: {outputs.events_path}")
        typer.echo(f"Wheel availability table: {outputs.wheel_availability_path}")
        typer.echo(f"Pose availability table: {outputs.pose_availability_path}")
        typer.echo(f"Trial behavior features: {outputs.trial_behavior_features_path}")
        typer.echo(f"Wheel trial features: {outputs.wheel_trial_features_path}")
        typer.echo(f"Pose trial features: {outputs.pose_trial_features_path}")
        typer.echo(f"Event-aligned behavior features: {outputs.event_aligned_behavior_features_path}")
        typer.echo(f"Behavior session features: {outputs.behavior_session_features_path}")
        typer.echo(f"Movement state epochs: {outputs.movement_state_epochs_path}")
        typer.echo(f"Quiescence state epochs: {outputs.quiescence_state_epochs_path}")
        typer.echo(f"Behavior state session features: {outputs.behavior_state_session_features_path}")
        typer.echo(f"Behavior shard directory: {outputs.wheel_store_path}")
        typer.echo(f"Behavior shard directory: {outputs.pose_store_path}")
        typer.echo(f"Manifest: {outputs.manifest_path}")
        typer.echo(f"Schema: {outputs.schema_path}")
        typer.echo(f"Provenance: {outputs.provenance_path}")
        typer.echo(f"Prefetch report: {outputs.prefetch_report_path}")
        typer.echo(f"Build report: {outputs.build_report_path}")
        typer.echo(f"Summary report: {outputs.summary_path}")

    @app.command("upgrade-bwm-behavior-compression", hidden=True)
    def upgrade_bwm_behavior_compression_command(
        source_dataset_root: Path = typer.Option(
            Path("reports/datasets/bwm_behavior/1.0.0"),
            help="Path to an existing built bwm_behavior dataset directory to upgrade.",
        ),
        output_root: Path = typer.Option(
            Path("reports/datasets"),
            help="Root directory under which the upgraded versioned dataset directory will be created.",
        ),
        resume: bool = typer.Option(
            True,
            help="Resume from an existing partial compression-upgrade directory when available.",
        ),
        jobs: int = typer.Option(
            max(1, (os.cpu_count() or 2) // 2),
            min=1,
            help="Number of parallel worker threads for shard rewrite and shard-based feature refresh.",
        ),
    ) -> None:
        """Upgrade bwm_behavior/1.0.0 into bwm_behavior/2.0.0 with compressed session shards."""
        try:
            from ibl_ai_agent.datasets.bwm_behavior_upgrade import upgrade_bwm_behavior_dataset_compression

            outputs = upgrade_bwm_behavior_dataset_compression(
                source_dataset_dir=source_dataset_root,
                output_root=output_root,
                jobs=jobs,
                resume=resume,
                verbose=True,
                release_root=Path("reports/releases"),
            )
        except Exception as exc:
            fail(str(exc))

        typer.echo(f"Dataset directory: {outputs.dataset_dir}")
        typer.echo(f"Sessions table: {outputs.sessions_path}")
        typer.echo(f"Trials table: {outputs.trials_path}")
        typer.echo(f"Events table: {outputs.events_path}")
        typer.echo(f"Wheel availability table: {outputs.wheel_availability_path}")
        typer.echo(f"Pose availability table: {outputs.pose_availability_path}")
        typer.echo(f"Wheel trial features: {outputs.wheel_trial_features_path}")
        typer.echo(f"Pose trial features: {outputs.pose_trial_features_path}")
        typer.echo(f"Event-aligned behavior features: {outputs.event_aligned_behavior_features_path}")
        typer.echo(f"Behavior session features: {outputs.behavior_session_features_path}")
        typer.echo(f"Movement state epochs: {outputs.movement_state_epochs_path}")
        typer.echo(f"Quiescence state epochs: {outputs.quiescence_state_epochs_path}")
        typer.echo(f"Behavior state session features: {outputs.behavior_state_session_features_path}")
        typer.echo(f"Manifest: {outputs.manifest_path}")
        typer.echo(f"Schema: {outputs.schema_path}")
        typer.echo(f"Provenance: {outputs.provenance_path}")
        typer.echo(f"Build report: {outputs.build_report_path}")
        typer.echo(f"Summary report: {outputs.summary_path}")
        if outputs.archive_path is not None:
            typer.echo(f"Release archive: {outputs.archive_path}")
            typer.echo(f"Release checksum: {outputs.archive_checksum_path}")

    @app.command("profile-bwm-behavior-compression")
    def profile_bwm_behavior_compression_command(
        dataset_root: Path = typer.Option(
            Path("reports/datasets/bwm_behavior/1.0.0"),
            help="Path to an existing built bwm_behavior dataset directory.",
        ),
        output_root: Path = typer.Option(
            Path("reports/profiles"),
            help="Root directory under which the timestamped profile report directory will be created.",
        ),
        max_shards: int = typer.Option(
            12,
            min=1,
            help="Number of behavior session shards to sample.",
        ),
        selection: str = typer.Option(
            "largest",
            help="Shard selection mode: largest, smallest, or spread.",
        ),
        strategies: str = typer.Option(
            "lossless-baseline,conservative,balanced,aggressive,balanced-pose-delta,aggressive-pose-delta,aggressive-pose-delta-wheel-native-left60-right60-body30,aggressive-pose-delta-wheel100-pose50,aggressive-pose-delta-30hz",
            help="Comma-separated strategy names to profile.",
        ),
        target_min_factor: float = typer.Option(
            5.0,
            min=1.0,
            help="Target compression factor used in the summary report.",
        ),
    ) -> None:
        """Profile lossy quantization strategies for bwm_behavior continuous signals."""
        try:
            from ibl_ai_agent.datasets.bwm_behavior_compression import ProfileConfig, profile_bwm_behavior_compression

            strategy_names = tuple(part.strip() for part in strategies.split(",") if part.strip())
            outputs = profile_bwm_behavior_compression(
                ProfileConfig(
                    dataset_dir=dataset_root,
                    output_root=output_root,
                    max_shards=max_shards,
                    selection=selection,
                    strategy_names=strategy_names,
                    target_min_factor=target_min_factor,
                    verbose=True,
                )
            )
        except Exception as exc:
            fail(str(exc))

        typer.echo(f"Profile report dir: {outputs.report_dir}")
        typer.echo(f"Summary: {outputs.summary_path}")
        typer.echo(f"Strategy summary: {outputs.strategy_summary_path}")
        typer.echo(f"Array metrics: {outputs.array_metrics_path}")
        typer.echo(f"Config: {outputs.config_path}")

    @app.command("validate-bwm-behavior-compression")
    def validate_bwm_behavior_compression_command(
        dataset_root: Path = typer.Option(
            Path("reports/datasets/bwm_behavior/1.0.0"),
            help="Path to an existing built bwm_behavior dataset directory.",
        ),
        output_root: Path = typer.Option(
            Path("reports/profiles"),
            help="Root directory under which the timestamped validation report directory will be created.",
        ),
        max_shards: int = typer.Option(
            12,
            min=1,
            help="Number of behavior session shards to sample.",
        ),
        selection: str = typer.Option(
            "spread",
            help="Shard selection mode: largest, smallest, or spread.",
        ),
        strategy: str = typer.Option(
            "aggressive-pose-delta-30hz",
            help="Compression strategy name to validate.",
        ),
    ) -> None:
        """Validate reconstruction errors for one bwm_behavior compression strategy."""
        try:
            from ibl_ai_agent.datasets.bwm_behavior_compression import ValidationConfig, validate_bwm_behavior_compression

            outputs = validate_bwm_behavior_compression(
                ValidationConfig(
                    dataset_dir=dataset_root,
                    output_root=output_root,
                    max_shards=max_shards,
                    selection=selection,
                    strategy_name=strategy,
                    verbose=True,
                )
            )
        except Exception as exc:
            fail(str(exc))

        typer.echo(f"Validation report dir: {outputs.report_dir}")
        typer.echo(f"Summary: {outputs.summary_path}")
        typer.echo(f"Array validation: {outputs.array_validation_path}")
        typer.echo(f"Config: {outputs.config_path}")

    @app.command("validate-bwm-behavior-compression-features")
    def validate_bwm_behavior_compression_features_command(
        dataset_root: Path = typer.Option(
            Path("reports/datasets/bwm_behavior/1.0.0"),
            help="Path to an existing built bwm_behavior dataset directory.",
        ),
        output_root: Path = typer.Option(
            Path("reports/profiles"),
            help="Root directory under which the timestamped validation report directory will be created.",
        ),
        max_shards: int = typer.Option(
            25,
            min=1,
            help="Number of behavior session shards to sample.",
        ),
        selection: str = typer.Option(
            "spread",
            help="Shard selection mode: largest, smallest, or spread.",
        ),
        strategy: str = typer.Option(
            "aggressive-pose-delta-30hz",
            help="Compression strategy name to validate.",
        ),
    ) -> None:
        """Validate derived behavior feature stability for one compression strategy."""
        try:
            from ibl_ai_agent.datasets.bwm_behavior_compression import (
                FeatureValidationConfig,
                validate_bwm_behavior_compression_features,
            )

            outputs = validate_bwm_behavior_compression_features(
                FeatureValidationConfig(
                    dataset_dir=dataset_root,
                    output_root=output_root,
                    max_shards=max_shards,
                    selection=selection,
                    strategy_name=strategy,
                    verbose=True,
                )
            )
        except Exception as exc:
            fail(str(exc))

        typer.echo(f"Feature validation report dir: {outputs.report_dir}")
        typer.echo(f"Summary: {outputs.summary_path}")
        typer.echo(f"Feature validation: {outputs.feature_validation_path}")
        typer.echo(f"Row validation: {outputs.row_validation_path}")
        typer.echo(f"Config: {outputs.config_path}")

    @app.command("open-notebook-url")
    def open_notebook_url(
        ask_log: Path = typer.Option(..., exists=True, readable=True, help="ask run log file"),
        token: str | None = typer.Option(None, help="Optional Jupyter token to append if missing"),
    ) -> None:
        """Extract notebook_edit_url from ask logs and print a URL that can be opened."""
        url = notebook_url_from_log_file(ask_log, token=(token or ""))
        if not url:
            fail("Could not extract notebook URL from ask log.")
        typer.echo(url)

    @app.command("clean-runs")
    def clean_runs(
        scope: str = typer.Option(
            "ask",
            help="Which run families to clean. Public builds currently support ask.",
        ),
        ask_dir: Path = typer.Option(
            Path("reports/ask_runs"),
            help="Ask run root directory.",
        ),
        keep_last: int = typer.Option(
            20,
            min=0,
            help="Keep at least this many newest runs per selected scope.",
        ),
        older_than_days: int | None = typer.Option(
            None,
            min=1,
            help="Delete only runs older than this many days.",
        ),
        apply: bool = typer.Option(
            False,
            help="Actually delete selected runs. Default is dry-run.",
        ),
    ) -> None:
        """Prune reports run directories with safe retention defaults."""
        allowed_scopes = {"ask", "all"}
        if scope not in allowed_scopes:
            fail(f"Invalid --scope '{scope}'. Use one of: ask, all.")

        selected_roots: list[Path] = []
        if scope in {"ask", "all"}:
            selected_roots.append(ask_dir)

        cutoff = None
        if older_than_days is not None:
            cutoff = datetime.now() - timedelta(days=older_than_days)

        selected: list[Path] = []
        for root in selected_roots:
            for i, run_dir in enumerate(_iter_run_dirs(root)):
                if _should_delete(run_dir, index=i, keep_last=keep_last, cutoff=cutoff):
                    selected.append(run_dir)

        bytes_to_free = sum(_dir_size_bytes(path) for path in selected)
        mode = "APPLY" if apply else "DRY-RUN"
        typer.echo(
            " ".join(
                [
                    f"mode={mode}",
                    f"scope={scope}",
                    f"selected={len(selected)}",
                    f"reclaim={_format_bytes(bytes_to_free)}",
                ]
            )
        )
        for path in selected:
            typer.echo(str(path))

        if not apply:
            typer.echo("No files deleted. Re-run with --apply to delete selected runs.")
            return

        for path in selected:
            shutil.rmtree(path)
        typer.echo(f"Deleted {len(selected)} run directories.")
