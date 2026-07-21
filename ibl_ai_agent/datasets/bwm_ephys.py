from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import shutil
from tempfile import mkdtemp
from threading import Lock
from time import perf_counter
from typing import Any

import numpy as np
import pandas as pd
import yaml
from brainbox.singlecell import calculate_peths

from ibl_ai_agent.datasets import bwm_session_assets, bwm_shared, bwm_simple


DATASET_NAME = "bwm_ephys"
DATASET_VERSION = "1.0.0"
SCHEMA_VERSION = 1
DEFAULT_SPIKE_TIME_QUANTIZATION_US = 100
DEFAULT_SPIKE_TIME_ENCODING = "delta_int_ticks"
PARQUET_ENGINE = "pyarrow"
PARQUET_COMPRESSION = "zstd"
SIGNAL_CONTAINER_FORMAT = "blosc_file_shards"
SIGNAL_COMPRESSION_VARIANT = "blosc_zstd_shuffle"
DEFAULT_SPIKE_TIME_STORAGE_DTYPE = np.uint16
FALLBACK_SPIKE_TIME_STORAGE_DTYPE = np.uint32
SPIKE_PROGRESS_INTERVAL_S = 3.0
SPIKE_SLOW_TASK_S = 30.0
EVENT_RESPONSE_PROGRESS_INTERVAL_S = 5.0
EVENT_RESPONSE_SLOW_TASK_S = 30.0
SPIKE_METRICS_FILENAME = "spike_build_metrics.parquet"
EVENT_COLUMNS = (
    ("interval_start", "intervals_0"),
    ("stimOn", "stimOn_times"),
    ("goCue", "goCue_times"),
    ("firstMovement", "firstMovement_times"),
    ("response", "response_times"),
    ("feedback", "feedback_times"),
    ("interval_end", "intervals_1"),
)
EVENT_RESPONSE_EVENT_COLUMNS = (
    ("stimOn", "stimOn_times"),
    ("goCue", "goCue_times"),
    ("firstMovement", "firstMovement_times"),
    ("response", "response_times"),
    ("feedback", "feedback_times"),
)
EVENT_RESPONSE_PRE_TIME_S = 0.2
EVENT_RESPONSE_POST_TIME_S = 0.3
EVENT_RESPONSE_BIN_SIZE_S = 0.005
EVENT_RESPONSE_SMOOTHING_S = 0.02
EVENT_RESPONSE_PEAK_WINDOW_S = (0.0, 0.2)
EVENT_RESPONSE_WINDOW_SPEC = (
    f"pre={EVENT_RESPONSE_PRE_TIME_S:.3f}|post={EVENT_RESPONSE_POST_TIME_S:.3f}|"
    f"bin={EVENT_RESPONSE_BIN_SIZE_S:.3f}|smooth={EVENT_RESPONSE_SMOOTHING_S:.3f}|"
    f"peak={EVENT_RESPONSE_PEAK_WINDOW_S[0]:.3f}:{EVENT_RESPONSE_PEAK_WINDOW_S[1]:.3f}"
)
UNIT_FEATURE_CANDIDATES = (
    "amp_max",
    "amp_median",
    "amplitude",
    "amplitude_cutoff",
    "contamination",
    "drift",
    "firing_rate",
    "isi_viol",
    "missed_spikes_est",
    "noise_cutoff",
    "peakToTrough",
    "presence_ratio",
    "pt_ratio",
    "rp_viol",
    "slidingRP_viol",
    "spike_count",
    "spike_width",
    "waveform_duration",
)
WAVEFORM_REQUIRED_FILES = ("waveforms.templates.npy", "waveforms.table.pqt")
AP_SAMPLE_RATE_HZ = 30_000.0
WAVEFORM_UPSAMPLE_FACTOR = 10
DEFAULT_BUILD_JOBS = max(1, (os.cpu_count() or 2) // 2)


class BuildError(RuntimeError):
    """Raised when the dataset build cannot complete successfully."""


@dataclass(frozen=True)
class PrefetchOutputs:
    report_path: Path
    requested_insertions: int
    already_present_insertions: int
    fetched_insertions: int
    failed_insertions: int
    final_present_insertions: int
    jobs: int


@dataclass(frozen=True)
class PassivePrefetchOutputs:
    report_path: Path
    requested_sessions: int
    already_present_sessions: int
    fetched_sessions: int
    failed_sessions: int
    final_present_sessions: int
    jobs: int


@dataclass(frozen=True)
class BuildConfig:
    output_root: Path
    cache_root: Path
    allow_remote_fetch: bool = False
    limit_insertions: int | None = None
    spike_time_quantization_us: int = DEFAULT_SPIKE_TIME_QUANTIZATION_US
    spike_time_encoding: str = DEFAULT_SPIKE_TIME_ENCODING
    prefetch_missing: bool = True
    require_signals: bool = True
    jobs: int = DEFAULT_BUILD_JOBS
    verbose: bool = True
    release_root: Path | None = None


@dataclass(frozen=True)
class BuildOutputs:
    dataset_dir: Path
    sessions_path: Path
    insertions_path: Path
    units_path: Path
    channels_path: Path
    trials_path: Path
    events_path: Path
    unit_features_path: Path
    event_response_features_path: Path
    manifest_path: Path
    schema_path: Path
    provenance_path: Path
    prefetch_report_path: Path
    build_report_path: Path
    summary_path: Path
    spikes_store_path: Path
    spike_metrics_path: Path
    wheel_store_path: Path
    pose_store_path: Path
    archive_path: Path | None = None
    archive_checksum_path: Path | None = None


@dataclass(frozen=True)
class StageMetric:
    name: str
    started_at: str
    elapsed_s: float
    details: dict[str, Any]


@dataclass(frozen=True)
class PreflightResult:
    roster: pd.DataFrame
    initial_scan: dict[str, Any]
    final_scan: dict[str, Any]
    prefetch_report: dict[str, Any]
    stages: list[StageMetric]


@dataclass(frozen=True)
class ResolvedInputs:
    clusters_path: Path
    trials_path: Path


@dataclass(frozen=True)
class MetadataBundle:
    outputs: BuildOutputs
    sessions_df: pd.DataFrame
    insertions_df: pd.DataFrame
    units_df: pd.DataFrame
    channels_df: pd.DataFrame
    trials_df: pd.DataFrame
    events_df: pd.DataFrame
    unit_features_df: pd.DataFrame
    event_response_features_df: pd.DataFrame
    stages: list[StageMetric]


class BuildProgressReporter:
    def __init__(self, *, verbose: bool) -> None:
        self.verbose = verbose

    def emit(self, message: str) -> None:
        if self.verbose:
            print(message)

    def stage_start(self, name: str, detail: str | None = None) -> tuple[float, str]:
        started_at = bwm_shared.now_iso()
        self.emit(f"[{name}] start" + (f": {detail}" if detail else ""))
        return perf_counter(), started_at

    def stage_done(self, name: str, started_perf: float, started_at: str, **details: Any) -> StageMetric:
        metric = StageMetric(name=name, started_at=started_at, elapsed_s=round(perf_counter() - started_perf, 3), details=details)
        suffix = f" ({', '.join(f'{k}={v}' for k, v in details.items())})" if details else ""
        self.emit(f"[{name}] done in {metric.elapsed_s:.2f}s{suffix}")
        return metric


class SpikeWorkerMonitor:
    def __init__(self, *, reporter: BuildProgressReporter, total: int, jobs: int, started_at: float, report_interval_s: float = SPIKE_PROGRESS_INTERVAL_S) -> None:
        self.reporter = reporter
        self.total = total
        self.jobs = jobs
        self.started_at = started_at
        self.report_interval_s = report_interval_s
        self.lock = Lock()
        self.states: dict[str, dict[str, Any]] = {}
        self.last_report_at = started_at
        self.last_completion_at = started_at
        self.completed = 0
        self.failed = 0
        self.ok = 0
        self.total_spikes = 0
        self.bytes_written = 0
        self.running_peak = 0
        self.slowest: list[dict[str, Any]] = []

    def update(self, pid: str, stage: str, **details: Any) -> None:
        now = perf_counter()
        with self.lock:
            state = self.states.setdefault(pid, {"started_at": now, "stage_started_at": now})
            if state.get("stage") != stage:
                state["stage_started_at"] = now
            state["stage"] = stage
            state["updated_at"] = now
            state.update(details)

    def record_completion(self, result: dict[str, Any]) -> None:
        now = perf_counter()
        with self.lock:
            self.completed += 1
            self.last_completion_at = now
            status = result["status"]
            if status == "ok":
                self.ok += 1
                self.total_spikes += int(result.get("n_spikes", 0))
                self.bytes_written += int(result.get("shard_bytes", 0))
            elif status == "failed":
                self.failed += 1
            pid = result["pid"]
            self.states.pop(pid, None)
            if result.get("total_s") is not None:
                entry = {
                    "pid": pid,
                    "total_s": float(result["total_s"]),
                    "n_spikes": int(result.get("n_spikes", 0)),
                    "status": status,
                }
                self.slowest.append(entry)
                self.slowest.sort(key=lambda x: x["total_s"], reverse=True)
                del self.slowest[10:]

    def maybe_report(self, pending_count: int, force: bool = False) -> None:
        now = perf_counter()
        with self.lock:
            if not force and now - self.last_report_at < self.report_interval_s:
                return
            running = len(self.states)
            self.running_peak = max(self.running_peak, running)
            queued = max(self.total - self.completed - running, 0)
            elapsed = max(now - self.started_at, 1e-9)
            completion_rate = self.completed / elapsed
            spike_rate = self.total_spikes / elapsed if self.total_spikes else 0.0
            byte_rate = self.bytes_written / elapsed if self.bytes_written else 0.0
            idle_for = now - self.last_completion_at
            active_lines = []
            for pid, state in sorted(self.states.items(), key=lambda kv: kv[1].get("stage_started_at", now))[: min(8, max(1, self.jobs))]:
                stage_elapsed = now - float(state.get("stage_started_at", now))
                stage = state.get("stage", "?")
                est = state.get("estimated_input_bytes")
                est_txt = f" { _format_bytes(int(est)) }" if est else ""
                active_lines.append(f"{pid[:8]}:{stage}:{stage_elapsed:0.1f}s{est_txt}")
            weighted_done = self.bytes_written
            weighted_total = self.bytes_written + sum(int(state.get("estimated_output_bytes", state.get("estimated_input_bytes", 0))) for state in self.states.values())
            eta = "?"
            if byte_rate > 0 and weighted_total > weighted_done:
                eta = _format_seconds((weighted_total - weighted_done) / byte_rate)
            elif completion_rate > 0:
                eta = _format_seconds((self.total - self.completed) / completion_rate)
            self.reporter.emit(
                "spikes live: "
                f"done={self.completed}/{self.total} ok={self.ok} failed={self.failed} running={running} queued={queued} pending={pending_count} "
                f"| write={_format_bytes(self.bytes_written)} @ {_format_bytes(int(byte_rate))}/s "
                f"| spikes={self.total_spikes:,} @ {spike_rate:,.0f}/s | last_done={idle_for:0.1f}s | ETA {eta}"
            )
            if active_lines:
                self.reporter.emit("active: " + " | ".join(active_lines))
            if idle_for >= SPIKE_SLOW_TASK_S and self.states:
                slow = sorted(self.states.items(), key=lambda kv: kv[1].get("stage_started_at", now))[0]
                self.reporter.emit(
                    f"slow-task warning: pid={slow[0]} stage={slow[1].get('stage','?')} running_for={now - float(slow[1].get('started_at', now)):.1f}s"
                )
            self.last_report_at = now


class EventResponseWorkerMonitor:
    def __init__(self, *, reporter: BuildProgressReporter, total: int, jobs: int, started_at: float, report_interval_s: float = EVENT_RESPONSE_PROGRESS_INTERVAL_S) -> None:
        self.reporter = reporter
        self.total = total
        self.jobs = jobs
        self.started_at = started_at
        self.report_interval_s = report_interval_s
        self.lock = Lock()
        self.states: dict[str, dict[str, Any]] = {}
        self.last_report_at = started_at
        self.last_completion_at = started_at
        self.completed = 0
        self.ok = 0
        self.failed = 0
        self.skipped = 0
        self.total_rows = 0
        self.total_events = 0
        self.running_peak = 0
        self.slowest: list[dict[str, Any]] = []

    def update(self, pid: str, stage: str, **details: Any) -> None:
        now = perf_counter()
        with self.lock:
            state = self.states.setdefault(pid, {"started_at": now, "stage_started_at": now})
            if state.get("stage") != stage:
                state["stage_started_at"] = now
            state["stage"] = stage
            state["updated_at"] = now
            state.update(details)

    def record_completion(self, result: dict[str, Any]) -> None:
        now = perf_counter()
        with self.lock:
            self.completed += 1
            self.last_completion_at = now
            status = str(result.get('status', 'unknown'))
            if status == 'ok':
                self.ok += 1
                self.total_rows += int(result.get('rows_written', 0) or 0)
                self.total_events += int(result.get('events_processed', 0) or 0)
            elif status == 'failed':
                self.failed += 1
            else:
                self.skipped += 1
            pid = str(result.get('pid', 'unknown'))
            self.states.pop(pid, None)
            total_s = result.get('total_s')
            if total_s is not None:
                entry = {
                    'pid': pid,
                    'total_s': float(total_s),
                    'rows_written': int(result.get('rows_written', 0) or 0),
                    'events_processed': int(result.get('events_processed', 0) or 0),
                    'status': status,
                }
                self.slowest.append(entry)
                self.slowest.sort(key=lambda x: x['total_s'], reverse=True)
                del self.slowest[10:]

    def maybe_report(self, pending_count: int, force: bool = False) -> None:
        now = perf_counter()
        with self.lock:
            if not force and now - self.last_report_at < self.report_interval_s:
                return
            running = len(self.states)
            self.running_peak = max(self.running_peak, running)
            queued = max(self.total - self.completed - running, 0)
            elapsed = max(now - self.started_at, 1e-9)
            completion_rate = self.completed / elapsed
            row_rate = self.total_rows / elapsed if self.total_rows else 0.0
            event_rate = self.total_events / elapsed if self.total_events else 0.0
            idle_for = now - self.last_completion_at
            eta = '?'
            if completion_rate > 0:
                eta = _format_seconds((self.total - self.completed) / completion_rate)
            active_lines = []
            for pid, state in sorted(self.states.items(), key=lambda kv: kv[1].get('stage_started_at', now))[: min(8, max(1, self.jobs))]:
                stage_elapsed = now - float(state.get('stage_started_at', now))
                stage = state.get('stage', '?')
                units = state.get('n_units')
                units_txt = f" units={int(units)}" if units is not None else ''
                active_lines.append(f"{pid[:8]}:{stage}:{stage_elapsed:0.1f}s{units_txt}")
            self.reporter.emit(
                'event-response live: '
                f"done={self.completed}/{self.total} ok={self.ok} skipped={self.skipped} failed={self.failed} running={running} queued={queued} pending={pending_count} "
                f"| rows={self.total_rows:,} @ {row_rate:,.0f}/s | events={self.total_events:,} @ {event_rate:,.1f}/s "
                f"| last_done={idle_for:0.1f}s | ETA {eta}"
            )
            if active_lines:
                self.reporter.emit('event-response active: ' + ' | '.join(active_lines))
            if idle_for >= EVENT_RESPONSE_SLOW_TASK_S and self.states:
                slow = sorted(self.states.items(), key=lambda kv: kv[1].get('stage_started_at', now))[0]
                self.reporter.emit(
                    f"event-response slow-task warning: pid={slow[0]} stage={slow[1].get('stage','?')} running_for={now - float(slow[1].get('started_at', now)):.1f}s"
                )
            self.last_report_at = now


class SpikeShardWriter:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir

    def shard_path(self, pid: str) -> Path:
        return self.output_dir / pid

    def write(self, pid: str, *, metadata: dict[str, Any], arrays: dict[str, np.ndarray], progress: callable | None = None) -> int:
        shard_dir = self.shard_path(pid)
        bwm_shared.write_array_directory(shard_dir, metadata=metadata, arrays=arrays, progress=progress)
        return sum(path.stat().st_size for path in shard_dir.iterdir() if path.is_file())

    def read(self, path: Path) -> dict[str, Any]:
        return bwm_shared.read_array_directory(path)


SPIKE_SHARD_WRITER = SpikeShardWriter(Path("."))


def build_bwm_ephys_dataset(config: BuildConfig) -> BuildOutputs:
    _validate_build_config(config)
    target_dir = config.output_root / DATASET_NAME / DATASET_VERSION
    if target_dir.exists():
        raise BuildError(f"Output directory already exists: {target_dir}. Remove it or build to a different output root before rerunning.")
    reporter = BuildProgressReporter(verbose=config.verbose)
    tmp_parent = target_dir.parent
    tmp_parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(mkdtemp(prefix=f".{DATASET_NAME}-{DATASET_VERSION}-", dir=tmp_parent))
    try:
        preflight = _run_preflight(config, reporter=reporter)
        resolved_inputs, resolve_stage = _resolve_required_inputs(config, reporter=reporter)
        metadata = _build_metadata_bundle(config, tmp_dir=tmp_dir, roster=preflight.roster, inputs=resolved_inputs, reporter=reporter)
        spike_stats, spike_stage = _build_spike_store(config, path=tmp_dir / "spikes", roster=preflight.roster, units_df=metadata.units_df, reporter=reporter)
        event_response_features_df, event_response_stage = _materialize_event_response_features(tmp_dir=tmp_dir, units_df=metadata.units_df, trials_df=metadata.trials_df, outputs=metadata.outputs, reporter=reporter, jobs=config.jobs)
        metadata = MetadataBundle(outputs=metadata.outputs, sessions_df=metadata.sessions_df, insertions_df=metadata.insertions_df, units_df=metadata.units_df, channels_df=metadata.channels_df, trials_df=metadata.trials_df, events_df=metadata.events_df, unit_features_df=metadata.unit_features_df, event_response_features_df=event_response_features_df, stages=metadata.stages)
        stages = [*preflight.stages, resolve_stage, *metadata.stages, spike_stage, event_response_stage]
        _write_build_sidecars(config, dataset_dir=tmp_dir, outputs=metadata.outputs, metadata=metadata, spike_stats=spike_stats, preflight=preflight, stages=stages, reporter=reporter, inputs=resolved_inputs)
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir.rename(target_dir)
        reporter.emit(f"Done: dataset built at {target_dir}")
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    outputs = _final_outputs(target_dir)
    if config.release_root is not None:
        release_artifacts = bwm_shared.write_release_archive(
            target_dir,
            release_root=config.release_root,
            dataset_name=DATASET_NAME,
            dataset_version=DATASET_VERSION,
        )
        outputs = replace(
            outputs,
            archive_path=release_artifacts["archive_path"],
            archive_checksum_path=release_artifacts["checksum_path"],
        )
    return outputs


def prefetch_bwm_ephys_spikes(*, cache_root: Path, output_root: Path, limit_insertions: int | None = None, jobs: int = DEFAULT_BUILD_JOBS, verbose: bool = True) -> PrefetchOutputs:
    if jobs <= 0:
        raise BuildError("jobs must be a positive integer.")
    roster = bwm_simple._load_roster(limit_insertions=limit_insertions)
    scan = inspect_bwm_ephys_cache(BuildConfig(output_root=output_root, cache_root=cache_root, allow_remote_fetch=False, limit_insertions=limit_insertions, prefetch_missing=False, require_signals=False, jobs=jobs, verbose=False), roster=roster)
    items = list(scan["signals"]["spikes"]["missing"])
    reporter = BuildProgressReporter(verbose=verbose)
    reporter.emit(_format_scan_summary(scan, title="Standalone prefetch initial scan"))
    spike_actions, spike_summary, _ = _run_spike_prefetch_jobs(cache_root=cache_root, items=items, jobs=jobs, reporter=reporter, label="prefetch spikes")
    already_present = int(len(roster) - len(items))
    actions = [
        {"kind": "spikes", **_row_identity(row, key_name="pid"), "status": "already_present", "revision_dir": str(revision_dir)}
        for row in roster.itertuples(index=False)
        for revision_dir in [bwm_simple._resolve_revision_dir(cache_root, lab=str(row.lab), subject=str(row.subject), date=str(row.date), session_number=int(row.session_number), probe_name=str(row.probe_name), allow_remote_fetch=False, one_remote=None, eid=str(row.eid))]
        if revision_dir is not None and _spike_assets_present(revision_dir)
    ]
    actions.extend(spike_actions)
    final_scan = inspect_bwm_ephys_cache(BuildConfig(output_root=output_root, cache_root=cache_root, allow_remote_fetch=False, limit_insertions=limit_insertions, prefetch_missing=False, require_signals=False, jobs=jobs, verbose=False), roster=roster)
    report = {
        "dataset_name": DATASET_NAME,
        "operation": "prefetch_bwm_ephys_spikes",
        "generated_at": bwm_shared.now_iso(),
        "selection": {"requested_insertions": int(len(roster)), "requested_sessions": int(roster['eid'].nunique())},
        "summary": {
            "already_present_insertions": already_present,
            "fetched_insertions": spike_summary["fetched"],
            "failed_insertions": spike_summary["failed"],
            "final_present_insertions": int(final_scan['signals']['spikes']['present_insertions']),
            "final_missing_insertions": len(final_scan['signals']['spikes']['missing']),
        },
        "actions": actions,
        "final_scan": final_scan,
    }
    reports_dir = output_root / DATASET_NAME / "prefetch_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"spikes_prefetch_{bwm_shared.now_tag()}.yaml"
    report_path.write_text(yaml.safe_dump(report, sort_keys=False), encoding="utf-8")
    return PrefetchOutputs(report_path=report_path, requested_insertions=int(len(roster)), already_present_insertions=already_present, fetched_insertions=spike_summary["fetched"], failed_insertions=spike_summary["failed"], final_present_insertions=int(final_scan['signals']['spikes']['present_insertions']), jobs=int(jobs))


def prefetch_bwm_ephys_passive(*, cache_root: Path, output_root: Path, limit_insertions: int | None = None, jobs: int = DEFAULT_BUILD_JOBS, verbose: bool = True) -> PassivePrefetchOutputs:
    if jobs <= 0:
        raise BuildError("jobs must be a positive integer.")
    roster = bwm_simple._load_roster(limit_insertions=limit_insertions)
    scan = inspect_bwm_ephys_cache(BuildConfig(output_root=output_root, cache_root=cache_root, allow_remote_fetch=False, limit_insertions=limit_insertions, prefetch_missing=False, require_signals=False, jobs=jobs, verbose=False), roster=roster)
    items = list(scan["signals"]["passive"]["missing"])
    reporter = BuildProgressReporter(verbose=verbose)
    reporter.emit(_format_scan_summary(scan, title="Standalone passive prefetch initial scan"))
    passive_actions, passive_summary, _ = _run_passive_prefetch_jobs(cache_root=cache_root, items=items, jobs=jobs, reporter=reporter, label="prefetch passive")
    already_present = int(scan['signals']['passive']['present_sessions'])
    actions = list(passive_actions)
    final_scan = inspect_bwm_ephys_cache(BuildConfig(output_root=output_root, cache_root=cache_root, allow_remote_fetch=False, limit_insertions=limit_insertions, prefetch_missing=False, require_signals=False, jobs=jobs, verbose=False), roster=roster)
    report = {
        "dataset_name": DATASET_NAME,
        "operation": "prefetch_bwm_ephys_passive",
        "generated_at": bwm_shared.now_iso(),
        "selection": {"requested_insertions": int(len(roster)), "requested_sessions": int(roster['eid'].nunique())},
        "summary": {
            "already_present_sessions": already_present,
            "fetched_sessions": passive_summary["fetched"],
            "failed_sessions": passive_summary["failed"],
            "final_present_sessions": int(final_scan['signals']['passive']['present_sessions']),
            "final_missing_sessions": len(final_scan['signals']['passive']['missing']),
        },
        "actions": actions,
        "final_scan": final_scan,
    }
    reports_dir = output_root / DATASET_NAME / "prefetch_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"passive_prefetch_{bwm_shared.now_tag()}.yaml"
    report_path.write_text(yaml.safe_dump(report, sort_keys=False), encoding="utf-8")
    return PassivePrefetchOutputs(report_path=report_path, requested_sessions=int(roster['eid'].nunique()), already_present_sessions=already_present, fetched_sessions=passive_summary["fetched"], failed_sessions=passive_summary["failed"], final_present_sessions=int(final_scan['signals']['passive']['present_sessions']), jobs=int(jobs))


def inspect_bwm_ephys_cache(config: BuildConfig, *, roster: pd.DataFrame | None = None) -> dict[str, Any]:
    roster_df = roster.copy() if roster is not None else bwm_simple._load_roster(limit_insertions=config.limit_insertions)
    aggregate_tables = {name: bwm_shared.scan_aggregate_table(config.cache_root, name) for name in ("clusters", "trials")}
    spikes_missing: list[dict[str, Any]] = []
    for row in roster_df.itertuples(index=False):
        revision_dir = bwm_simple._resolve_revision_dir(config.cache_root, lab=str(row.lab), subject=str(row.subject), date=str(row.date), session_number=int(row.session_number), probe_name=str(row.probe_name), allow_remote_fetch=False, one_remote=None, eid=str(row.eid))
        if revision_dir is None or not _spike_assets_present(revision_dir):
            spikes_missing.append(_row_identity(row, key_name="pid"))
    passive_missing: list[dict[str, Any]] = []
    session_rows = roster_df[['eid', 'subject', 'date', 'session_number', 'lab']].drop_duplicates('eid')
    for row in session_rows.itertuples(index=False):
        session_dir = bwm_session_assets.resolve_session_dir(
            config.cache_root,
            lab=str(row.lab),
            subject=str(row.subject),
            date=str(row.date),
            session_number=int(row.session_number),
        )
        missing_files = bwm_session_assets.passive_missing_filenames(session_dir)
        if missing_files:
            passive_missing.append(
                {
                    "eid": str(row.eid),
                    "subject": str(row.subject),
                    "date": str(row.date),
                    "session_number": int(row.session_number),
                    "lab": str(row.lab),
                    "session_dir": str(session_dir) if session_dir is not None else "",
                    "missing_files": missing_files,
                }
            )
    return {
        "generated_at": bwm_shared.now_iso(),
        "selection": {"insertions": int(len(roster_df)), "sessions": int(roster_df['eid'].nunique())},
        "aggregate_tables": aggregate_tables,
        "signals": {
            "spikes": {
                "required_insertions": int(len(roster_df)),
                "present_insertions": int(len(roster_df) - len(spikes_missing)),
                "missing": spikes_missing,
            },
            "passive": {
                "required_sessions": int(len(session_rows)),
                "present_sessions": int(len(session_rows) - len(passive_missing)),
                "missing": passive_missing,
            },
        },
    }


def _validate_build_config(config: BuildConfig) -> None:
    if config.spike_time_quantization_us <= 0:
        raise BuildError("spike_time_quantization_us must be a positive integer.")
    if config.spike_time_encoding != DEFAULT_SPIKE_TIME_ENCODING:
        raise BuildError("bwm_ephys currently supports only spike_time_encoding='delta_int_ticks'.")
    if config.jobs <= 0:
        raise BuildError("jobs must be a positive integer.")


def _run_preflight(config: BuildConfig, *, reporter: BuildProgressReporter) -> PreflightResult:
    stages: list[StageMetric] = []
    perf, started_at = reporter.stage_start("preflight", "load roster and inspect cache")
    roster = bwm_simple._load_roster(limit_insertions=config.limit_insertions)
    reporter.emit(f"Preflight: scanning cache for {len(roster)} insertion(s) / {roster['eid'].nunique()} session(s).")
    initial_scan = inspect_bwm_ephys_cache(config, roster=roster)
    reporter.emit(_format_scan_summary(initial_scan, title="Initial cache scan"))
    final_scan = initial_scan
    prefetch_report = {
        "dataset_name": DATASET_NAME,
        "dataset_version": DATASET_VERSION,
        "generated_at": bwm_shared.now_iso(),
        "config": {
            "allow_remote_fetch": bool(config.allow_remote_fetch),
            "prefetch_missing": bool(config.prefetch_missing),
            "require_signals": bool(config.require_signals),
            "limit_insertions": config.limit_insertions,
            "spike_time_encoding": config.spike_time_encoding,
            "spike_time_quantization_us": int(config.spike_time_quantization_us),
            "jobs": int(config.jobs),
        },
        "initial": initial_scan,
        "actions": [],
    }
    stages.append(reporter.stage_done("preflight", perf, started_at, insertions=int(len(roster)), sessions=int(roster['eid'].nunique())))
    if config.prefetch_missing and _preflight_has_missing_required_inputs(initial_scan):
        if not config.allow_remote_fetch:
            reporter.emit("Missing required assets detected, but remote fetch is disabled. Re-run with --allow-remote-fetch to populate the local ONE cache automatically.")
        else:
            prefetch_report, prefetch_stage = _prefetch_required_inputs(config, roster=roster, initial_scan=initial_scan, reporter=reporter, prefetch_report=prefetch_report)
            stages.append(prefetch_stage)
            final_scan = prefetch_report["final"]
            reporter.emit(_format_scan_summary(final_scan, title="Post-prefetch cache scan"))
    else:
        prefetch_report["final"] = final_scan
        if not _preflight_has_missing_required_inputs(initial_scan):
            reporter.emit("Preflight: all required assets are already present in the local cache.")
    if config.require_signals and _preflight_has_missing_required_inputs(final_scan):
        failure_report_path = _write_failure_prefetch_report(config.output_root / DATASET_NAME, prefetch_report)
        raise BuildError(f"Required signal assets are still missing after preflight/prefetch. See {failure_report_path} for details.")
    return PreflightResult(roster=roster, initial_scan=initial_scan, final_scan=final_scan, prefetch_report=prefetch_report, stages=stages)


def _prefetch_required_inputs(config: BuildConfig, *, roster: pd.DataFrame, initial_scan: dict[str, Any], reporter: BuildProgressReporter, prefetch_report: dict[str, Any]) -> tuple[dict[str, Any], StageMetric]:
    perf, started_at = reporter.stage_start("prefetch", "fetch missing aggregate tables, spike payloads, and passive assets")
    actions: list[dict[str, Any]] = []
    one_remote = bwm_shared.make_remote_one(config.cache_root)
    for table_type in ("clusters", "trials"):
        state = initial_scan["aggregate_tables"][table_type]
        if state["present"]:
            actions.append({"kind": "aggregate_table", "name": table_type, "status": "already_present"})
            continue
        try:
            path = bwm_simple._resolve_aggregate_table(config.cache_root, table_type, allow_remote_fetch=True, one_remote=one_remote)
            actions.append({"kind": "aggregate_table", "name": table_type, "status": "fetched", "path": str(path)})
            reporter.emit(f"Prefetch: fetched aggregate table '{table_type}'.")
        except Exception as exc:
            actions.append({"kind": "aggregate_table", "name": table_type, "status": "failed", "error": str(exc)})
            reporter.emit(f"Prefetch: failed to fetch aggregate table '{table_type}': {exc}")
    spike_actions, spike_summary, progress_metric = _run_spike_prefetch_jobs(cache_root=config.cache_root, items=list(initial_scan['signals']['spikes']['missing']), jobs=config.jobs, reporter=reporter, label="prefetch spikes")
    actions.extend(spike_actions)
    passive_actions, passive_summary, passive_progress_metric = _run_passive_prefetch_jobs(cache_root=config.cache_root, items=list(initial_scan['signals']['passive']['missing']), jobs=config.jobs, reporter=reporter, label="prefetch passive")
    actions.extend(passive_actions)
    final_scan = inspect_bwm_ephys_cache(config, roster=roster)
    prefetch_report["actions"] = actions
    prefetch_report["final"] = final_scan
    stage = reporter.stage_done("prefetch", perf, started_at, spikes_requested=len(initial_scan['signals']['spikes']['missing']), spikes_fetched=spike_summary['fetched'], spikes_failed=spike_summary['failed'], passive_requested=len(initial_scan['signals']['passive']['missing']), passive_fetched=passive_summary['fetched'], passive_failed=passive_summary['failed'], progress_reports=progress_metric + passive_progress_metric)
    return prefetch_report, stage


def _resolve_required_inputs(config: BuildConfig, *, reporter: BuildProgressReporter) -> tuple[ResolvedInputs, StageMetric]:
    perf, started_at = reporter.stage_start("resolve-inputs", "aggregate tables")
    one_remote = bwm_simple._make_one(config.cache_root, mode="remote") if config.allow_remote_fetch else None
    clusters_path = bwm_simple._resolve_aggregate_table(config.cache_root, "clusters", allow_remote_fetch=config.allow_remote_fetch, one_remote=one_remote)
    trials_path = bwm_simple._resolve_aggregate_table(config.cache_root, "trials", allow_remote_fetch=config.allow_remote_fetch, one_remote=one_remote)
    return ResolvedInputs(clusters_path=clusters_path, trials_path=trials_path), reporter.stage_done("resolve-inputs", perf, started_at, clusters=str(clusters_path), trials=str(trials_path))


def _build_metadata_bundle(config: BuildConfig, *, tmp_dir: Path, roster: pd.DataFrame, inputs: ResolvedInputs, reporter: BuildProgressReporter) -> MetadataBundle:
    stages: list[StageMetric] = []
    metadata_dir = tmp_dir / "metadata"
    features_dir = tmp_dir / "features"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    features_dir.mkdir(parents=True, exist_ok=True)
    perf, started_at = reporter.stage_start("metadata", "prepare metadata tables from cached aggregate data")
    units_df = bwm_simple._build_units(roster, inputs.clusters_path)
    trials_df = bwm_simple._build_trials(roster, inputs.trials_path)
    channels_df = bwm_simple._build_channels(roster, config.cache_root, allow_remote_fetch=config.allow_remote_fetch, one_remote=None)
    insertions_df = _build_insertions(roster, units_df, trials_df, channels_df)
    sessions_df = _build_sessions(roster, units_df, trials_df, insertions_df)
    events_df = _build_events(trials_df)
    unit_features_df = _build_unit_features(inputs.clusters_path, units_df, roster=roster, cache_root=config.cache_root)
    event_response_features_df = _empty_event_response_features_df()
    _sort_frames(sessions_df=sessions_df, insertions_df=insertions_df, units_df=units_df, channels_df=channels_df, trials_df=trials_df, events_df=events_df, unit_features_df=unit_features_df, event_response_features_df=event_response_features_df)
    outputs = _write_metadata_tables(metadata_dir=metadata_dir, features_dir=features_dir, sessions_df=sessions_df, insertions_df=insertions_df, units_df=units_df, channels_df=channels_df, trials_df=trials_df, events_df=events_df, unit_features_df=unit_features_df, event_response_features_df=event_response_features_df, dataset_dir=tmp_dir)
    stages.append(reporter.stage_done("metadata", perf, started_at, sessions=len(sessions_df), insertions=len(insertions_df), units=len(units_df), channels=len(channels_df), trials=len(trials_df), event_response_features=0))
    return MetadataBundle(outputs=outputs, sessions_df=sessions_df, insertions_df=insertions_df, units_df=units_df, channels_df=channels_df, trials_df=trials_df, events_df=events_df, unit_features_df=unit_features_df, event_response_features_df=event_response_features_df, stages=stages)


def _build_spike_store(config: BuildConfig, *, path: Path, roster: pd.DataFrame, units_df: pd.DataFrame, reporter: BuildProgressReporter) -> tuple[dict[str, Any], StageMetric]:
    perf, started_at = reporter.stage_start("spikes", f"build spike shards with {config.jobs} worker(s)")
    stats = _write_spike_store(path, roster=roster, units_df=units_df, cache_root=config.cache_root, spike_time_quantization_us=config.spike_time_quantization_us, jobs=config.jobs, reporter=reporter)
    stage = reporter.stage_done("spikes", perf, started_at, insertions_written=stats['insertions_written'], missing=len(stats['missing_insertions']), total_spikes=stats['total_spikes_written'], bytes_written=stats['bytes_written'])
    return stats, stage


def _materialize_event_response_features(*, tmp_dir: Path, units_df: pd.DataFrame, trials_df: pd.DataFrame, outputs: BuildOutputs, reporter: BuildProgressReporter, jobs: int) -> tuple[pd.DataFrame, StageMetric]:
    perf, started_at = reporter.stage_start('event-response-features', 'summarize event-aligned responses from local spike shards')
    event_response_features_df = _build_event_response_features(dataset_dir=tmp_dir, units_df=units_df, trials_df=trials_df, jobs=jobs, reporter=reporter)
    if not event_response_features_df.empty:
        event_response_features_df.sort_values(['pid', 'cluster_id', 'event_name'], inplace=True, kind='mergesort')
    event_response_features_df.to_parquet(outputs.event_response_features_path, engine=PARQUET_ENGINE, compression=PARQUET_COMPRESSION, index=False)
    stage = reporter.stage_done('event-response-features', perf, started_at, rows=len(event_response_features_df), events=event_response_features_df['event_name'].nunique() if not event_response_features_df.empty else 0)
    return event_response_features_df, stage


def _write_build_sidecars(config: BuildConfig, *, dataset_dir: Path, outputs: BuildOutputs, metadata: MetadataBundle, spike_stats: dict[str, Any], preflight: PreflightResult, stages: list[StageMetric], reporter: BuildProgressReporter, inputs: ResolvedInputs) -> None:
    perf, started_at = reporter.stage_start("reports", "schema, provenance, reports, manifest")
    schema = _build_schema(outputs)
    provenance = _build_provenance(config=config, clusters_path=inputs.clusters_path, trials_path=inputs.trials_path)
    build_report = _build_report(config=config, sessions_df=metadata.sessions_df, insertions_df=metadata.insertions_df, units_df=metadata.units_df, channels_df=metadata.channels_df, trials_df=metadata.trials_df, events_df=metadata.events_df, unit_features_df=metadata.unit_features_df, event_response_features_df=metadata.event_response_features_df, spike_stats=spike_stats, prefetch_report=preflight.prefetch_report, stages=stages)
    summary = _build_summary(sessions_df=metadata.sessions_df, insertions_df=metadata.insertions_df, units_df=metadata.units_df, channels_df=metadata.channels_df, trials_df=metadata.trials_df, events_df=metadata.events_df, unit_features_df=metadata.unit_features_df, event_response_features_df=metadata.event_response_features_df, spike_stats=spike_stats, prefetch_report=preflight.prefetch_report, stages=stages)
    outputs.schema_path.write_text(yaml.safe_dump(schema, sort_keys=False), encoding="utf-8")
    outputs.provenance_path.write_text(yaml.safe_dump(provenance, sort_keys=False), encoding="utf-8")
    outputs.prefetch_report_path.write_text(yaml.safe_dump(preflight.prefetch_report, sort_keys=False), encoding="utf-8")
    outputs.build_report_path.write_text(yaml.safe_dump(build_report, sort_keys=False), encoding="utf-8")
    outputs.summary_path.write_text(summary, encoding="utf-8")
    manifest = bwm_shared.build_manifest(dataset_name=DATASET_NAME, dataset_version=DATASET_VERSION, dataset_dir=dataset_dir)
    outputs.manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    reporter.stage_done("reports", perf, started_at, files=len(manifest['files']))


def _run_spike_prefetch_jobs(*, cache_root: Path, items: list[dict[str, Any]], jobs: int, reporter: BuildProgressReporter, label: str) -> tuple[list[dict[str, Any]], dict[str, int], int]:
    actions: list[dict[str, Any]] = []
    fetched = 0
    failed = 0
    if not items:
        reporter.emit(f"{label}: no missing spike payloads.")
        return actions, {"fetched": 0, "failed": 0}, 0
    started_at = perf_counter()
    progress_reports = 0
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as executor:
        futures = {executor.submit(_prefetch_spikes_task, cache_root=cache_root, item=item): item for item in items}
        pending = set(futures)
        while pending:
            done, pending = wait(pending, timeout=SPIKE_PROGRESS_INTERVAL_S, return_when=FIRST_COMPLETED)
            if not done:
                progress_reports += 1
                reporter.emit(_progress_line(label, len(items) - len(pending), len(items), started_at))
                continue
            for future in done:
                result = future.result()
                actions.append(result)
                if result['status'] == 'fetched':
                    fetched += 1
                else:
                    failed += 1
            progress_reports += 1
            reporter.emit(_progress_line(label, len(items) - len(pending), len(items), started_at, current=result.get('pid'), state=result['status']))
    return actions, {"fetched": fetched, "failed": failed}, progress_reports


def _prefetch_spikes_task(*, cache_root: Path, item: dict[str, Any]) -> dict[str, Any]:
    one_remote = bwm_shared.make_remote_one(cache_root)
    try:
        _prefetch_spikes(one_remote, eid=item['eid'], probe_name=item['probe_name'])
        revision_dir = bwm_simple._resolve_revision_dir(cache_root, lab=item['lab'], subject=item['subject'], date=item['date'], session_number=int(item['session_number']), probe_name=item['probe_name'], allow_remote_fetch=False, one_remote=None, eid=item['eid'])
        if revision_dir is not None and _spike_assets_present(revision_dir):
            return {"kind": "spikes", **item, "status": "fetched", "revision_dir": str(revision_dir)}
        return {"kind": "spikes", **item, "status": "failed", "error": "fetch completed but spike files still missing"}
    except Exception as exc:
        return {"kind": "spikes", **item, "status": "failed", "error": str(exc)}


def _prefetch_spikes(one_remote: Any, *, eid: str, probe_name: str) -> None:
    from brainbox.io.one import SpikeSortingLoader
    loader = SpikeSortingLoader(one=one_remote, eid=eid, pname=probe_name)
    loader.load_spike_sorting(revision=bwm_simple.SORTER_REVISION)


def _run_passive_prefetch_jobs(*, cache_root: Path, items: list[dict[str, Any]], jobs: int, reporter: BuildProgressReporter, label: str) -> tuple[list[dict[str, Any]], dict[str, int], int]:
    actions: list[dict[str, Any]] = []
    fetched = 0
    failed = 0
    if not items:
        reporter.emit(f"{label}: no missing passive datasets.")
        return actions, {"fetched": 0, "failed": 0}, 0
    started_at = perf_counter()
    progress_reports = 0
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as executor:
        futures = {executor.submit(_prefetch_passive_task, cache_root=cache_root, item=item): item for item in items}
        pending = set(futures)
        while pending:
            done, pending = wait(pending, timeout=SPIKE_PROGRESS_INTERVAL_S, return_when=FIRST_COMPLETED)
            if not done:
                progress_reports += 1
                reporter.emit(_progress_line(label, len(items) - len(pending), len(items), started_at))
                continue
            for future in done:
                result = future.result()
                actions.append(result)
                if result['status'] == 'fetched':
                    fetched += 1
                else:
                    failed += 1
            progress_reports += 1
            reporter.emit(_progress_line(label, len(items) - len(pending), len(items), started_at, current=result.get('eid'), state=result['status']))
    return actions, {"fetched": fetched, "failed": failed}, progress_reports


def _prefetch_passive_task(*, cache_root: Path, item: dict[str, Any]) -> dict[str, Any]:
    one_remote = bwm_shared.make_remote_one(cache_root)
    try:
        statuses = bwm_shared.prefetch_passive(one_remote, eid=item['eid'], dataset_names=item.get('missing_files') or bwm_session_assets.PASSIVE_DATASET_FILENAMES)
        session_dir = bwm_session_assets.resolve_session_dir(
            cache_root,
            lab=item['lab'],
            subject=item['subject'],
            date=item['date'],
            session_number=int(item['session_number']),
        )
        still_missing = bwm_session_assets.passive_missing_filenames(session_dir)
        if not still_missing:
            return {"kind": "passive", **item, "status": "fetched", "dataset_statuses": statuses, "session_dir": str(session_dir) if session_dir else ""}
        return {"kind": "passive", **item, "status": "failed", "dataset_statuses": statuses, "still_missing_files": still_missing}
    except Exception as exc:
        return {"kind": "passive", **item, "status": "failed", "error": str(exc)}


def _spike_assets_present(revision_dir: Path) -> bool:
    return (revision_dir / "spikes.times.npy").exists() and (revision_dir / "spikes.clusters.npy").exists()


def _preflight_has_missing_required_inputs(scan: dict[str, Any]) -> bool:
    return (not scan['aggregate_tables']['clusters']['present']) or (not scan['aggregate_tables']['trials']['present']) or bool(scan['signals']['spikes']['missing'])


def _format_scan_summary(scan: dict[str, Any], *, title: str) -> str:
    lines = [
        f"{title}:",
        f"- selected insertions: {scan['selection']['insertions']}",
        f"- selected sessions: {scan['selection']['sessions']}",
        f"- aggregate clusters table: {'present' if scan['aggregate_tables']['clusters']['present'] else 'missing'}",
        f"- aggregate trials table: {'present' if scan['aggregate_tables']['trials']['present'] else 'missing'}",
        f"- spikes present for {scan['signals']['spikes']['present_insertions']}/{scan['signals']['spikes']['required_insertions']} insertion(s)",
        f"- passive present for {scan['signals']['passive']['present_sessions']}/{scan['signals']['passive']['required_sessions']} session(s)",
    ]
    if scan['signals']['spikes']['missing']:
        lines.append(f"- missing spikes: {', '.join(item['pid'] for item in scan['signals']['spikes']['missing'][:5])}")
    if scan['signals']['passive']['missing']:
        lines.append(f"- missing passive: {', '.join(item['eid'] for item in scan['signals']['passive']['missing'][:5])}")
    return "\n".join(lines)


def _row_identity(row: Any, *, key_name: str) -> dict[str, Any]:
    payload = {key_name: str(getattr(row, key_name))}
    for name in ('eid', 'pid', 'subject', 'date', 'session_number', 'lab', 'probe_name'):
        if hasattr(row, name):
            value = getattr(row, name)
            payload[name] = int(value) if name == 'session_number' else str(value)
    return payload


def _write_failure_prefetch_report(parent: Path, prefetch_report: dict[str, Any]) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / f"{DATASET_NAME}_prefetch_failure_{bwm_shared.now_tag()}.yaml"
    path.write_text(yaml.safe_dump(prefetch_report, sort_keys=False), encoding='utf-8')
    return path


def _build_insertions(roster: pd.DataFrame, units_df: pd.DataFrame, trials_df: pd.DataFrame, channels_df: pd.DataFrame) -> pd.DataFrame:
    insertions = bwm_simple._build_insertions(roster, units_df, trials_df)
    channel_counts = channels_df.groupby('pid').size().rename('n_channels').astype(np.int32)
    insertions = insertions.merge(channel_counts, on='pid', how='left')
    insertions['n_channels'] = insertions['n_channels'].fillna(0).astype(np.int32)
    return insertions


def _build_sessions(roster: pd.DataFrame, units_df: pd.DataFrame, trials_df: pd.DataFrame, insertions_df: pd.DataFrame) -> pd.DataFrame:
    sessions = roster[['eid', 'subject', 'date', 'session_number', 'lab']].drop_duplicates('eid').copy()
    n_trials = trials_df.groupby('eid').size().rename('n_trials').astype(np.int32)
    n_included = trials_df.loc[trials_df['bwm_include']].groupby('eid').size().rename('n_included_trials').astype(np.int32)
    n_insertions = insertions_df.groupby('eid').size().rename('n_insertions').astype(np.int16)
    n_good_units = units_df.groupby('eid').size().rename('n_good_units').astype(np.int32)
    sessions = sessions.merge(n_trials, on='eid', how='left').merge(n_included, on='eid', how='left').merge(n_insertions, on='eid', how='left').merge(n_good_units, on='eid', how='left')
    for col in ('n_trials', 'n_included_trials', 'n_good_units'):
        sessions[col] = sessions[col].fillna(0).astype(np.int32)
    sessions['n_insertions'] = sessions['n_insertions'].fillna(0).astype(np.int16)
    return sessions


def _build_events(trials_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for event_name, source_column in EVENT_COLUMNS:
        if source_column not in trials_df.columns:
            continue
        event_df = trials_df[['eid', 'trial_id', 'subject', 'date', 'session_number', 'lab', source_column]].copy()
        event_df.rename(columns={source_column: 'event_time'}, inplace=True)
        event_df['event_name'] = event_name
        event_df = event_df.loc[event_df['event_time'].notna()].copy()
        rows.append(event_df)
    if not rows:
        return pd.DataFrame(columns=['eid', 'trial_id', 'event_id', 'subject', 'date', 'session_number', 'lab', 'event_name', 'event_time'])
    events = pd.concat(rows, ignore_index=True)
    events['event_time'] = events['event_time'].astype(np.float32)
    events.sort_values(['eid', 'trial_id', 'event_time', 'event_name'], inplace=True, kind='mergesort')
    events['event_id'] = events.groupby('eid').cumcount().astype(np.int32)
    return events[['eid', 'trial_id', 'event_id', 'subject', 'date', 'session_number', 'lab', 'event_name', 'event_time']]


def _resolve_waveforms_dir(revision_dir: Path | None) -> Path | None:
    if revision_dir is None:
        return None
    candidates = [revision_dir]
    if revision_dir.name.startswith('#'):
        candidates.append(revision_dir.parent)
    for candidate in candidates:
        if all((candidate / filename).exists() for filename in WAVEFORM_REQUIRED_FILES):
            return candidate
    return None


def _load_template_cluster_ids(waveforms_dir: Path) -> np.ndarray:
    df_wav = pd.read_parquet(waveforms_dir / "waveforms.table.pqt").reset_index(drop=True)
    if "sample" not in df_wav.columns or "cluster" not in df_wav.columns:
        raise BuildError(f"Waveform table at {waveforms_dir} is missing required columns.")
    valid = df_wav.loc[df_wav["sample"] >= 0, :]
    return valid.groupby("cluster", sort=True).size().index.to_numpy(dtype=np.int32)


def _template_waveform_metrics(template_2d: np.ndarray) -> dict[str, float]:
    if template_2d.ndim != 2 or template_2d.size == 0:
        return {
            'spike_width_ms': float('nan'),
            'peak_to_trough_ms': float('nan'),
            'waveform_amplitude_uv': float('nan'),
            'pt_ratio': float('nan'),
        }
    finite_mask = np.isfinite(template_2d)
    if not finite_mask.any():
        return {
            'spike_width_ms': float('nan'),
            'peak_to_trough_ms': float('nan'),
            'waveform_amplitude_uv': float('nan'),
            'pt_ratio': float('nan'),
        }
    valid_rows = finite_mask.any(axis=1)
    if not valid_rows.any():
        return {
            'spike_width_ms': float('nan'),
            'peak_to_trough_ms': float('nan'),
            'waveform_amplitude_uv': float('nan'),
            'pt_ratio': float('nan'),
        }
    channel_ptp = np.full(template_2d.shape[0], np.nan, dtype=float)
    valid_template = np.asarray(template_2d[valid_rows], dtype=float)
    channel_ptp[valid_rows] = np.nanmax(valid_template, axis=1) - np.nanmin(valid_template, axis=1)
    if np.isnan(channel_ptp).all():
        return {
            'spike_width_ms': float('nan'),
            'peak_to_trough_ms': float('nan'),
            'waveform_amplitude_uv': float('nan'),
            'pt_ratio': float('nan'),
        }
    dominant_channel = int(np.nanargmax(channel_ptp))
    waveform = np.asarray(template_2d[dominant_channel], dtype=float)
    if np.isnan(waveform).all():
        return {
            'spike_width_ms': float('nan'),
            'peak_to_trough_ms': float('nan'),
            'waveform_amplitude_uv': float('nan'),
            'pt_ratio': float('nan'),
        }
    amplitude_uv = float(np.nanmax(waveform) - np.nanmin(waveform))
    oriented = waveform.copy()
    if abs(np.nanmax(oriented)) > abs(np.nanmin(oriented)):
        oriented = -oriented
    sample_index = np.arange(oriented.size, dtype=float)
    upsampled_index = np.linspace(sample_index[0], sample_index[-1], oriented.size * WAVEFORM_UPSAMPLE_FACTOR)
    upsampled_waveform = np.interp(upsampled_index, sample_index, oriented)
    trough_idx = int(np.nanargmin(upsampled_waveform))
    trough_value = float(upsampled_waveform[trough_idx])
    spike_width_ms = float('nan')
    peak_to_trough_ms = float('nan')
    pt_ratio = float('nan')
    if np.isfinite(trough_value) and trough_value < 0.0:
        half_level = trough_value / 2.0
        left = np.flatnonzero(upsampled_waveform[:trough_idx] > half_level)
        right = np.flatnonzero(upsampled_waveform[trough_idx:] > half_level)
        if left.size and right.size:
            left_idx = int(left[-1])
            right_idx = int(trough_idx + right[0])
            width_samples = right_idx - left_idx
            spike_width_ms = width_samples / (AP_SAMPLE_RATE_HZ * WAVEFORM_UPSAMPLE_FACTOR) * 1000.0
        if trough_idx < upsampled_waveform.size - 1:
            post_trough = upsampled_waveform[trough_idx + 1:]
            if post_trough.size and not np.isnan(post_trough).all():
                rebound_idx = int(np.nanargmax(post_trough)) + trough_idx + 1
                rebound_value = float(upsampled_waveform[rebound_idx])
                if rebound_value > 0.0:
                    peak_to_trough_ms = ((rebound_idx - trough_idx) / (AP_SAMPLE_RATE_HZ * WAVEFORM_UPSAMPLE_FACTOR) * 1000.0)
                    pt_ratio = rebound_value / abs(trough_value) if trough_value != 0.0 else float('nan')
    return {
        'spike_width_ms': float(spike_width_ms),
        'peak_to_trough_ms': float(peak_to_trough_ms),
        'waveform_amplitude_uv': float(amplitude_uv),
        'pt_ratio': float(pt_ratio),
    }


def _build_waveform_unit_features(*, roster: pd.DataFrame, cache_root: Path, units_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    probe_rows = roster[['pid', 'eid', 'probe_name', 'subject', 'date', 'session_number', 'lab']].drop_duplicates('pid')
    for probe in probe_rows.itertuples(index=False):
        revision_dir = bwm_simple._resolve_revision_dir(
            cache_root,
            lab=str(probe.lab),
            subject=str(probe.subject),
            date=str(probe.date),
            session_number=int(probe.session_number),
            probe_name=str(probe.probe_name),
            allow_remote_fetch=False,
            one_remote=None,
            eid=str(probe.eid),
        )
        waveforms_dir = _resolve_waveforms_dir(revision_dir)
        if waveforms_dir is None:
            continue
        cluster_ids = _load_template_cluster_ids(waveforms_dir)
        templates = np.lib.format.open_memmap(waveforms_dir / 'waveforms.templates.npy', mode='r')
        if int(templates.shape[0]) != int(cluster_ids.size):
            raise BuildError(
                f"Mismatch between waveform templates and cluster ids for {probe.eid}/{probe.probe_name}: "
                f"{templates.shape[0]} templates vs {cluster_ids.size} clusters."
            )
        for cluster_id, template in zip(cluster_ids, templates, strict=False):
            metrics = _template_waveform_metrics(np.asarray(template))
            rows.append({
                'pid': str(probe.pid),
                'cluster_id': int(cluster_id),
                **metrics,
            })
    if not rows:
        return pd.DataFrame(columns=['pid', 'cluster_id', 'spike_width_ms', 'peak_to_trough_ms', 'waveform_amplitude_uv', 'pt_ratio'])
    feature_df = pd.DataFrame(rows)
    feature_df['cluster_id'] = feature_df['cluster_id'].astype(np.int32)
    for col in ('spike_width_ms', 'peak_to_trough_ms', 'waveform_amplitude_uv', 'pt_ratio'):
        feature_df[col] = feature_df[col].astype(np.float32)
    feature_df = feature_df.merge(units_df[['pid', 'cluster_id']], on=['pid', 'cluster_id'], how='inner', validate='one_to_one')
    return feature_df


def _build_unit_features(clusters_path: Path, units_df: pd.DataFrame, *, roster: pd.DataFrame | None = None, cache_root: Path | None = None) -> pd.DataFrame:
    key_cols = ['pid', 'cluster_id']
    base = units_df[key_cols].drop_duplicates().copy()
    base['cluster_id'] = base['cluster_id'].astype(np.int32)
    clusters = pd.read_parquet(clusters_path)
    available = [col for col in UNIT_FEATURE_CANDIDATES if col in clusters.columns]
    if available:
        aggregate_df = clusters[key_cols + available].copy()
        aggregate_df['cluster_id'] = aggregate_df['cluster_id'].astype(np.int32)
        base = base.merge(aggregate_df, on=key_cols, how='left', validate='one_to_one')
    base.rename(columns={'peakToTrough': 'peak_to_trough_ms', 'spike_width': 'spike_width_ms'}, inplace=True)
    for col in ('spike_width_ms', 'peak_to_trough_ms', 'waveform_amplitude_uv', 'pt_ratio'):
        if col not in base.columns:
            base[col] = np.nan
    if roster is not None and cache_root is not None:
        waveform_df = _build_waveform_unit_features(roster=roster, cache_root=cache_root, units_df=units_df)
        if not waveform_df.empty:
            base = base.merge(waveform_df, on=key_cols, how='left', suffixes=('', '__waveform'), validate='one_to_one')
            for col in ('spike_width_ms', 'peak_to_trough_ms', 'waveform_amplitude_uv', 'pt_ratio'):
                wf_col = f'{col}__waveform'
                if wf_col in base.columns:
                    base[col] = base[wf_col].where(base[wf_col].notna(), base[col])
                    base.drop(columns=[wf_col], inplace=True)
    cols = ['pid', 'cluster_id'] + [c for c in base.columns if c not in {'pid', 'cluster_id'}]
    return base[cols]


def _summarize_peth_row(firing_rate: np.ndarray, time_axis: np.ndarray) -> dict[str, float]:
    firing_rate = np.asarray(firing_rate, dtype=float)
    time_axis = np.asarray(time_axis, dtype=float)
    if firing_rate.size == 0 or time_axis.size == 0:
        return {
            'baseline_fr': float('nan'),
            'peak_fr': float('nan'),
            'peak_latency_ms': float('nan'),
            'modulation_index': float('nan'),
        }
    baseline_mask = (time_axis >= -EVENT_RESPONSE_PRE_TIME_S) & (time_axis < 0.0)
    peak_mask = (time_axis >= EVENT_RESPONSE_PEAK_WINDOW_S[0]) & (time_axis <= EVENT_RESPONSE_PEAK_WINDOW_S[1])
    if baseline_mask.sum() == 0 or peak_mask.sum() == 0:
        return {
            'baseline_fr': float('nan'),
            'peak_fr': float('nan'),
            'peak_latency_ms': float('nan'),
            'modulation_index': float('nan'),
        }
    baseline_fr = float(np.nanmean(firing_rate[baseline_mask]))
    peak_values = np.asarray(firing_rate[peak_mask], dtype=float)
    peak_times = np.asarray(time_axis[peak_mask], dtype=float)
    if peak_values.size == 0 or np.isnan(peak_values).all():
        return {
            'baseline_fr': baseline_fr,
            'peak_fr': float('nan'),
            'peak_latency_ms': float('nan'),
            'modulation_index': float('nan'),
        }
    peak_index = int(np.nanargmax(peak_values))
    peak_fr = float(peak_values[peak_index])
    peak_latency_ms = float(peak_times[peak_index] * 1000.0)
    denom = peak_fr + baseline_fr
    modulation_index = float((peak_fr - baseline_fr) / denom) if np.isfinite(denom) and abs(denom) > 1e-12 else float('nan')
    return {
        'baseline_fr': baseline_fr,
        'peak_fr': peak_fr,
        'peak_latency_ms': peak_latency_ms,
        'modulation_index': modulation_index,
    }


def _empty_event_response_features_df() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            'pid',
            'cluster_id',
            'event_name',
            'window_spec',
            'n_events',
            'baseline_fr',
            'peak_fr',
            'peak_latency_ms',
            'modulation_index',
        ]
    )


def _build_event_response_features_for_pid(*, dataset_dir: Path, pid: str, unit_group: pd.DataFrame, eid_trials: pd.DataFrame, monitor: EventResponseWorkerMonitor | None = None) -> dict[str, Any]:
    total_start = perf_counter()
    result: dict[str, Any] = {
        'pid': str(pid),
        'status': 'skipped',
        'rows_written': 0,
        'events_processed': 0,
        'n_units': int(len(unit_group)),
        'frame': _empty_event_response_features_df(),
    }
    try:
        if unit_group.empty or eid_trials.empty:
            result['total_s'] = perf_counter() - total_start
            return result
        shard_path = dataset_dir / 'spikes' / str(pid)
        if not shard_path.exists():
            result['total_s'] = perf_counter() - total_start
            return result
        if monitor is not None:
            monitor.update(str(pid), 'load-shard', n_units=int(len(unit_group)))
        shard = load_spike_shard(shard_path)
        spike_times = np.asarray(shard['spike_times_seconds'], dtype=float)
        dense_clusters = np.asarray(shard['spike_clusters'], dtype=int)
        cluster_ids = np.asarray(shard['cluster_ids'], dtype=int)
        raw_cluster_ids = cluster_ids[dense_clusters]
        unit_ids = unit_group['cluster_id'].astype(int).to_numpy()
        if unit_ids.size == 0:
            result['total_s'] = perf_counter() - total_start
            return result
        rows: list[dict[str, Any]] = []
        events_processed = 0
        for event_name, source_column in EVENT_RESPONSE_EVENT_COLUMNS:
            if source_column not in eid_trials.columns:
                continue
            event_times = pd.to_numeric(eid_trials[source_column], errors='coerce').to_numpy(dtype=float)
            event_times = event_times[np.isfinite(event_times)]
            if event_times.size == 0:
                continue
            events_processed += int(event_times.size)
            if monitor is not None:
                monitor.update(str(pid), f'event:{event_name}', n_units=int(unit_ids.size), n_events=int(event_times.size))
            peths, _ = calculate_peths(
                spike_times,
                raw_cluster_ids,
                unit_ids,
                event_times,
                pre_time=EVENT_RESPONSE_PRE_TIME_S,
                post_time=EVENT_RESPONSE_POST_TIME_S,
                bin_size=EVENT_RESPONSE_BIN_SIZE_S,
                smoothing=EVENT_RESPONSE_SMOOTHING_S,
                return_fr=True,
            )
            means = np.asarray(peths.means, dtype=float)
            time_axis = np.asarray(peths.tscale, dtype=float)
            for unit_id, firing_rate in zip(unit_ids, means, strict=False):
                summary = _summarize_peth_row(firing_rate, time_axis)
                rows.append({
                    'pid': str(pid),
                    'cluster_id': int(unit_id),
                    'event_name': str(event_name),
                    'window_spec': EVENT_RESPONSE_WINDOW_SPEC,
                    'n_events': int(event_times.size),
                    **summary,
                })
        if not rows:
            result.update(status='skipped', events_processed=events_processed, total_s=perf_counter() - total_start)
            return result
        frame = pd.DataFrame(rows)
        frame['cluster_id'] = frame['cluster_id'].astype(np.int32)
        frame['n_events'] = frame['n_events'].astype(np.int32)
        for col in ('baseline_fr', 'peak_fr', 'peak_latency_ms', 'modulation_index'):
            frame[col] = frame[col].astype(np.float32)
        result.update(status='ok', rows_written=int(len(frame)), events_processed=events_processed, frame=frame, total_s=perf_counter() - total_start)
        return result
    except Exception as exc:
        result.update(status='failed', error=str(exc), total_s=perf_counter() - total_start)
        return result


def _build_event_response_features(*, dataset_dir: Path, units_df: pd.DataFrame, trials_df: pd.DataFrame, jobs: int = DEFAULT_BUILD_JOBS, reporter: BuildProgressReporter | None = None) -> pd.DataFrame:
    if units_df.empty or trials_df.empty:
        return _empty_event_response_features_df()
    if jobs <= 0:
        raise BuildError('jobs must be a positive integer.')
    local_reporter = reporter or BuildProgressReporter(verbose=False)
    trial_cols = ['eid', 'bwm_include', *[source for _, source in EVENT_RESPONSE_EVENT_COLUMNS]]
    available_trial_cols = [col for col in trial_cols if col in trials_df.columns]
    trial_view = trials_df[available_trial_cols].copy()
    if 'bwm_include' in trial_view.columns:
        trial_view = trial_view.loc[trial_view['bwm_include'].astype(bool)].copy()
    trials_by_eid = {str(eid): frame.copy() for eid, frame in trial_view.groupby('eid', sort=False)}
    items = []
    for pid, unit_group in units_df.groupby('pid', sort=False):
        eid = str(unit_group['eid'].iloc[0]) if not unit_group.empty and 'eid' in unit_group.columns else ''
        items.append((str(pid), unit_group.copy(), trials_by_eid.get(eid, pd.DataFrame(columns=available_trial_cols))))
    if not items:
        return _empty_event_response_features_df()
    started_at = perf_counter()
    monitor = EventResponseWorkerMonitor(reporter=local_reporter, total=len(items), jobs=max(1, jobs), started_at=started_at)
    completed_frames: list[pd.DataFrame] = []
    failures: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as executor:
        pending: set[Future] = set()
        for pid, unit_group, eid_trials in items:
            monitor.update(pid, 'queued', n_units=int(len(unit_group)))
            future = executor.submit(_build_event_response_features_for_pid, dataset_dir=dataset_dir, pid=pid, unit_group=unit_group, eid_trials=eid_trials, monitor=monitor)
            pending.add(future)
        while pending:
            done, pending = wait(pending, timeout=EVENT_RESPONSE_PROGRESS_INTERVAL_S, return_when=FIRST_COMPLETED)
            if not done:
                monitor.maybe_report(len(pending))
                continue
            for future in done:
                result = future.result()
                monitor.record_completion(result)
                if result.get('status') == 'ok':
                    completed_frames.append(result['frame'])
                elif result.get('status') == 'failed':
                    failures.append({'pid': result.get('pid'), 'error': result.get('error', 'unknown error')})
            monitor.maybe_report(len(pending), force=True)
    monitor.maybe_report(0, force=True)
    if failures:
        sample = '; '.join(f"{item['pid']}: {item['error']}" for item in failures[:5])
        raise BuildError(f"Event-response feature generation failed for {len(failures)} insertion(s): {sample}")
    if not completed_frames:
        return _empty_event_response_features_df()
    df = pd.concat(completed_frames, ignore_index=True)
    return df


def _sort_frames(*, sessions_df: pd.DataFrame, insertions_df: pd.DataFrame, units_df: pd.DataFrame, channels_df: pd.DataFrame, trials_df: pd.DataFrame, events_df: pd.DataFrame, unit_features_df: pd.DataFrame, event_response_features_df: pd.DataFrame) -> None:
    sessions_df.sort_values(['lab', 'subject', 'date', 'session_number'], inplace=True, kind='mergesort')
    insertions_df.sort_values(['lab', 'subject', 'date', 'session_number', 'probe_name'], inplace=True, kind='mergesort')
    units_df.sort_values(['lab', 'subject', 'date', 'session_number', 'probe_name', 'cluster_id'], inplace=True, kind='mergesort')
    channels_df.sort_values(['lab', 'subject', 'date', 'session_number', 'probe_name', 'channel_id'], inplace=True, kind='mergesort')
    trials_df.sort_values(['lab', 'subject', 'date', 'session_number', 'trial_id'], inplace=True, kind='mergesort')
    if not events_df.empty:
        events_df.sort_values(['lab', 'subject', 'date', 'session_number', 'trial_id', 'event_id'], inplace=True, kind='mergesort')
    if not unit_features_df.empty:
        unit_features_df.sort_values(['pid', 'cluster_id'], inplace=True, kind='mergesort')
    if not event_response_features_df.empty:
        event_response_features_df.sort_values(['pid', 'cluster_id', 'event_name'], inplace=True, kind='mergesort')


def _write_metadata_tables(*, metadata_dir: Path, features_dir: Path, sessions_df: pd.DataFrame, insertions_df: pd.DataFrame, units_df: pd.DataFrame, channels_df: pd.DataFrame, trials_df: pd.DataFrame, events_df: pd.DataFrame, unit_features_df: pd.DataFrame, event_response_features_df: pd.DataFrame, dataset_dir: Path) -> BuildOutputs:
    sessions_path = metadata_dir / 'sessions.parquet'
    insertions_path = metadata_dir / 'insertions.parquet'
    units_path = metadata_dir / 'units.parquet'
    channels_path = metadata_dir / 'channels.parquet'
    trials_path = metadata_dir / 'trials.parquet'
    events_path = metadata_dir / 'events.parquet'
    unit_features_path = features_dir / 'unit_features.parquet'
    event_response_features_path = features_dir / 'event_response_features.parquet'
    for frame, path in ((sessions_df, sessions_path), (insertions_df, insertions_path), (units_df, units_path), (channels_df, channels_path), (trials_df, trials_path), (events_df, events_path), (unit_features_df, unit_features_path), (event_response_features_df, event_response_features_path)):
        frame.to_parquet(path, engine=PARQUET_ENGINE, compression=PARQUET_COMPRESSION, index=False)
    return BuildOutputs(dataset_dir=dataset_dir, sessions_path=sessions_path, insertions_path=insertions_path, units_path=units_path, channels_path=channels_path, trials_path=trials_path, events_path=events_path, unit_features_path=unit_features_path, event_response_features_path=event_response_features_path, manifest_path=dataset_dir / 'manifest.json', schema_path=dataset_dir / 'schema.yaml', provenance_path=dataset_dir / 'provenance.yaml', prefetch_report_path=dataset_dir / 'prefetch_report.yaml', build_report_path=dataset_dir / 'build_report.yaml', summary_path=dataset_dir / 'SUMMARY.md', spikes_store_path=dataset_dir / 'spikes', spike_metrics_path=dataset_dir / SPIKE_METRICS_FILENAME, wheel_store_path=dataset_dir / 'wheel', pose_store_path=dataset_dir / 'pose')


def _write_spike_store(path: Path, *, roster: pd.DataFrame, units_df: pd.DataFrame, cache_root: Path, spike_time_quantization_us: int, jobs: int, reporter: BuildProgressReporter) -> dict[str, Any]:
    path.mkdir(parents=True, exist_ok=True)
    writer = SpikeShardWriter(path)
    grouped_units = units_df.groupby('pid')['cluster_id'].apply(lambda s: np.sort(s.to_numpy(dtype=np.int32)))
    items = _order_spike_items(roster, cache_root=cache_root, grouped_units=grouped_units)
    total_items = len(items)
    started_at = perf_counter()
    monitor = SpikeWorkerMonitor(reporter=reporter, total=total_items, jobs=jobs, started_at=started_at)
    completed = 0
    total_spikes = 0
    insertions_written = 0
    missing_pids: list[str] = []
    empty_pids: list[str] = []
    failed: list[dict[str, Any]] = []
    written_bytes = 0
    metrics: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as executor:
        pending: set[Future] = set()
        future_to_pid: dict[Future, str] = {}
        for item in items:
            monitor.update(str(item.pid), 'queued', estimated_input_bytes=int(getattr(item, '_estimated_input_bytes', 0)), estimated_output_bytes=int(getattr(item, '_estimated_input_bytes', 0) // 2))
            future = executor.submit(_write_spike_shard_task, writer=writer, item=item, good_cluster_ids=grouped_units.get(str(item.pid)), cache_root=cache_root, spike_time_quantization_us=spike_time_quantization_us, monitor=monitor)
            pending.add(future)
            future_to_pid[future] = str(item.pid)
        while pending:
            done, pending = wait(pending, timeout=SPIKE_PROGRESS_INTERVAL_S, return_when=FIRST_COMPLETED)
            if not done:
                monitor.maybe_report(len(pending))
                continue
            for future in done:
                result = future.result()
                completed += 1
                monitor.record_completion(result)
                metrics.append(_metric_row_from_result(result, completed_order=completed))
                status = result['status']
                if status == 'missing':
                    missing_pids.append(result['pid'])
                elif status == 'empty':
                    empty_pids.append(result['pid'])
                elif status == 'ok':
                    total_spikes += int(result['n_spikes'])
                    insertions_written += 1
                    written_bytes += int(result['shard_bytes'])
                elif status == 'failed':
                    failed.append({'pid': result['pid'], 'error': result.get('error', 'unknown error')})
            monitor.maybe_report(len(pending), force=True)
    monitor.maybe_report(0, force=True)
    metrics_df = pd.DataFrame(metrics)
    metrics_path = path.parent / SPIKE_METRICS_FILENAME
    if not metrics_df.empty:
        metrics_df.sort_values(['status', 'total_s', 'n_spikes'], ascending=[True, False, False], inplace=True, kind='mergesort')
        metrics_df.to_parquet(metrics_path, engine=PARQUET_ENGINE, compression=PARQUET_COMPRESSION, index=False)
    fallback_insertions = int(metrics_df['fallback_used'].fillna(False).sum()) if not metrics_df.empty and 'fallback_used' in metrics_df.columns else 0
    return {
        'insertions_written': insertions_written,
        'total_spikes_written': total_spikes,
        'missing_insertions': missing_pids,
        'empty_insertions': empty_pids,
        'failed_insertions': failed,
        'time_encoding': DEFAULT_SPIKE_TIME_ENCODING,
        'time_quantization_us': int(spike_time_quantization_us),
        'time_storage_dtype': 'adaptive_uint16_or_uint32',
        'jobs': int(max(1, jobs)),
        'container_format': SIGNAL_CONTAINER_FORMAT,
        'bytes_written': written_bytes,
        'metrics_path': str(metrics_path.name),
        'metrics_rows': int(len(metrics_df)),
        'running_peak': int(monitor.running_peak),
        'slowest_insertions': monitor.slowest,
        'fallback_insertions': fallback_insertions,
    }


def _order_spike_items(roster: pd.DataFrame, *, cache_root: Path, grouped_units: pd.Series) -> list[Any]:
    items = []
    for item in roster.itertuples(index=False):
        revision_dir = bwm_simple._resolve_revision_dir(cache_root, lab=str(item.lab), subject=str(item.subject), date=str(item.date), session_number=int(item.session_number), probe_name=str(item.probe_name), allow_remote_fetch=False, one_remote=None, eid=str(item.eid))
        estimated_input_bytes = 0
        if revision_dir is not None:
            for name in ('spikes.times.npy', 'spikes.clusters.npy'):
                path = revision_dir / name
                if path.exists():
                    estimated_input_bytes += int(path.stat().st_size)
        n_units = int(len(grouped_units.get(str(item.pid), [])))
        items.append((estimated_input_bytes, n_units, item))
    items.sort(key=lambda x: (x[0], x[1]), reverse=True)
    ordered = []
    for estimated_input_bytes, _, item in items:
        from types import SimpleNamespace
        payload = dict(item._asdict())
        payload['_estimated_input_bytes'] = estimated_input_bytes
        ordered.append(SimpleNamespace(**payload))
    return ordered


def _write_spike_shard_task(*, writer: SpikeShardWriter, item: Any, good_cluster_ids: np.ndarray | None, cache_root: Path, spike_time_quantization_us: int, monitor: SpikeWorkerMonitor) -> dict[str, Any]:
    pid = str(item.pid)
    total_start = perf_counter()
    result: dict[str, Any] = {'pid': pid, 'eid': str(item.eid), 'probe_name': str(item.probe_name), 'estimated_input_bytes': int(getattr(item, '_estimated_input_bytes', 0))}
    try:
        monitor.update(pid, 'resolve')
        revision_dir = bwm_simple._resolve_revision_dir(cache_root, lab=str(item.lab), subject=str(item.subject), date=str(item.date), session_number=int(item.session_number), probe_name=str(item.probe_name), allow_remote_fetch=False, one_remote=None, eid=str(item.eid))
        if revision_dir is None or not _spike_assets_present(revision_dir):
            result.update(status='missing', total_s=perf_counter() - total_start)
            return result
        if good_cluster_ids is None or good_cluster_ids.size == 0:
            result.update(status='empty', total_s=perf_counter() - total_start)
            return result
        timings: dict[str, float] = {}
        t = perf_counter()
        monitor.update(pid, 'load')
        spike_times = np.load(revision_dir / 'spikes.times.npy')
        spike_clusters = np.load(revision_dir / 'spikes.clusters.npy')
        timings['load_s'] = perf_counter() - t
        t = perf_counter()
        monitor.update(pid, 'filter')
        mask = np.isin(spike_clusters, good_cluster_ids)
        spike_times = np.asarray(spike_times[mask], dtype=np.float64)
        spike_clusters = np.asarray(spike_clusters[mask], dtype=np.int64)
        timings['filter_s'] = perf_counter() - t
        if spike_times.size == 0:
            result.update(status='empty', total_s=perf_counter() - total_start, **timings)
            return result
        t = perf_counter()
        monitor.update(pid, 'remap')
        dense_local = np.searchsorted(good_cluster_ids, spike_clusters).astype(np.int32, copy=False)
        if np.any(good_cluster_ids[dense_local] != spike_clusters.astype(good_cluster_ids.dtype, copy=False)):
            raise BuildError(f'Cluster remap failed for pid={pid}')
        counts = np.bincount(dense_local, minlength=int(good_cluster_ids.size)).astype(np.int32)
        timings['remap_s'] = perf_counter() - t
        t = perf_counter()
        monitor.update(pid, 'encode')
        encoded_times, attrs = _encode_spike_times_dataset(
            spike_times,
            quantization_us=spike_time_quantization_us,
        )
        timings['encode_s'] = perf_counter() - t
        metadata = {
            'format': 'ibl_ai_agent_spike_shard_v2',
            'dataset_name': DATASET_NAME,
            'dataset_version': DATASET_VERSION,
            'pid': pid,
            'eid': str(item.eid),
            'probe_name': str(item.probe_name),
            'subject': str(item.subject),
            'date': str(item.date),
            'session_number': int(item.session_number),
            'lab': str(item.lab),
            'n_spikes': int(spike_times.size),
            'n_good_units': int(good_cluster_ids.size),
            'time_encoding': DEFAULT_SPIKE_TIME_ENCODING,
            'time_quantization_us': int(spike_time_quantization_us),
            'cluster_encoding': 'dense_local_indices',
            'compression': {'name': SIGNAL_COMPRESSION_VARIANT},
            **attrs,
        }
        arrays = {
            'spike_times_delta_ticks': encoded_times,
            'spike_clusters': dense_local,
            'cluster_ids': good_cluster_ids.astype(np.int32, copy=False),
            'cluster_spike_counts': counts,
        }
        def progress(array_name: str, phase: str, **details: Any) -> None:
            monitor.update(pid, f"write:{array_name}:{phase}", **details)
        t = perf_counter()
        monitor.update(pid, 'write')
        shard_bytes = writer.write(pid, metadata=metadata, arrays=arrays, progress=progress)
        timings['write_s'] = perf_counter() - t
        result.update(
            status='ok',
            n_spikes=int(spike_times.size),
            n_good_units=int(good_cluster_ids.size),
            shard_bytes=int(shard_bytes),
            time_storage_dtype=str(metadata.get('storage_dtype', metadata.get('time_dtype', 'unknown'))),
            fallback_used=bool(metadata.get('fallback_used', False)),
            total_s=perf_counter() - total_start,
            **timings,
        )
        return result
    except Exception as exc:
        result.update(status='failed', error=str(exc), total_s=perf_counter() - total_start)
        return result


def _metric_row_from_result(result: dict[str, Any], *, completed_order: int) -> dict[str, Any]:
    row = {
        'completed_order': int(completed_order),
        'pid': result['pid'],
        'eid': result.get('eid'),
        'probe_name': result.get('probe_name'),
        'status': result['status'],
        'n_spikes': int(result.get('n_spikes', 0) or 0),
        'n_good_units': int(result.get('n_good_units', 0) or 0),
        'estimated_input_bytes': int(result.get('estimated_input_bytes', 0) or 0),
        'shard_bytes': int(result.get('shard_bytes', 0) or 0),
        'total_s': float(result.get('total_s', 0.0) or 0.0),
        'load_s': float(result.get('load_s', 0.0) or 0.0),
        'filter_s': float(result.get('filter_s', 0.0) or 0.0),
        'remap_s': float(result.get('remap_s', 0.0) or 0.0),
        'encode_s': float(result.get('encode_s', 0.0) or 0.0),
        'write_s': float(result.get('write_s', 0.0) or 0.0),
        'time_storage_dtype': result.get('time_storage_dtype'),
        'fallback_used': bool(result.get('fallback_used', False)),
        'error': result.get('error'),
    }
    return row


def _encode_spike_times(spike_times_seconds: np.ndarray, *, encoding: str, quantization_us: int) -> tuple[np.ndarray, dict[str, Any]]:
    if encoding == 'float_seconds':
        return np.asarray(spike_times_seconds, dtype=np.float32), {'time_dtype': 'float32', 'delta_encoded': False}
    ticks = np.rint(np.asarray(spike_times_seconds, dtype=np.float64) * 1_000_000.0 / quantization_us).astype(np.uint64)
    if encoding == 'int_ticks':
        return ticks, {'time_dtype': str(ticks.dtype), 'delta_encoded': False}
    if ticks.size == 0:
        return ticks, {'time_dtype': str(ticks.dtype), 'delta_encoded': True}
    deltas = np.empty_like(ticks)
    deltas[0] = ticks[0]
    if ticks.size > 1:
        deltas[1:] = np.diff(ticks)
    return deltas, {'time_dtype': str(deltas.dtype), 'delta_encoded': True}


def _encode_spike_times_dataset(spike_times_seconds: np.ndarray, *, quantization_us: int) -> tuple[np.ndarray, dict[str, Any]]:
    spike_times = np.asarray(spike_times_seconds, dtype=np.float64)
    if spike_times.size == 0:
        return np.asarray([], dtype=DEFAULT_SPIKE_TIME_STORAGE_DTYPE), {
            'time_dtype': np.dtype(DEFAULT_SPIKE_TIME_STORAGE_DTYPE).str,
            'delta_encoded': True,
            'overflow_checked': True,
            'fallback_used': False,
            'storage_dtype': np.dtype(DEFAULT_SPIKE_TIME_STORAGE_DTYPE).name,
            'time_origin_seconds': 0.0,
            'time_origin_ticks': 0,
        }
    origin_time_seconds = float(spike_times[0])
    shifted = spike_times - origin_time_seconds
    if np.any(shifted < -1e-9):
        raise BuildError('shifted spike times became negative after origin normalization')
    shifted = np.maximum(shifted, 0.0)
    ticks = np.rint(shifted * 1_000_000.0 / quantization_us).astype(np.uint64)
    deltas = np.empty_like(ticks)
    deltas[0] = 0
    if ticks.size > 1:
        deltas[1:] = np.diff(ticks)
    max_delta = int(deltas.max(initial=0))
    origin_ticks = int(np.rint(origin_time_seconds * 1_000_000.0 / quantization_us))
    primary_limit = int(np.iinfo(DEFAULT_SPIKE_TIME_STORAGE_DTYPE).max)
    if max_delta <= primary_limit:
        return deltas.astype(DEFAULT_SPIKE_TIME_STORAGE_DTYPE), {
            'time_dtype': np.dtype(DEFAULT_SPIKE_TIME_STORAGE_DTYPE).str,
            'delta_encoded': True,
            'overflow_checked': True,
            'fallback_used': False,
            'storage_dtype': np.dtype(DEFAULT_SPIKE_TIME_STORAGE_DTYPE).name,
            'max_delta_ticks': max_delta,
            'time_origin_seconds': origin_time_seconds,
            'time_origin_ticks': origin_ticks,
        }
    fallback_limit = int(np.iinfo(FALLBACK_SPIKE_TIME_STORAGE_DTYPE).max)
    if max_delta > fallback_limit:
        raise BuildError(f"delta_int_ticks overflow for uint32: max delta ticks={max_delta} exceeds {fallback_limit} at quantization_us={quantization_us}")
    return deltas.astype(FALLBACK_SPIKE_TIME_STORAGE_DTYPE), {
        'time_dtype': np.dtype(FALLBACK_SPIKE_TIME_STORAGE_DTYPE).str,
        'delta_encoded': True,
        'overflow_checked': True,
        'fallback_used': True,
        'storage_dtype': np.dtype(FALLBACK_SPIKE_TIME_STORAGE_DTYPE).name,
        'fallback_reason': f'uint16_overflow_max_delta_ticks={max_delta}',
        'max_delta_ticks': max_delta,
        'time_origin_seconds': origin_time_seconds,
        'time_origin_ticks': origin_ticks,
    }


def load_spike_shard(path: Path) -> dict[str, Any]:
    shard = bwm_shared.read_array_directory(path)
    arrays = shard['arrays']
    meta = shard['meta']
    ticks = np.cumsum(arrays['spike_times_delta_ticks'].astype(np.int64), dtype=np.int64)
    ticks = ticks + int(meta.get('time_origin_ticks', 0))
    arrays['spike_times_seconds'] = ticks * int(meta['time_quantization_us']) / 1_000_000.0
    return {'meta': meta, **arrays}


def _build_schema(outputs: BuildOutputs) -> dict[str, Any]:
    return {
        'dataset_name': DATASET_NAME,
        'dataset_version': DATASET_VERSION,
        'schema_version': SCHEMA_VERSION,
        'tables': {
            'sessions': {'path': str(outputs.sessions_path.relative_to(outputs.dataset_dir)), 'primary_key': ['eid']},
            'insertions': {'path': str(outputs.insertions_path.relative_to(outputs.dataset_dir)), 'primary_key': ['pid']},
            'units': {'path': str(outputs.units_path.relative_to(outputs.dataset_dir)), 'primary_key': ['pid', 'cluster_id']},
            'channels': {'path': str(outputs.channels_path.relative_to(outputs.dataset_dir)), 'primary_key': ['pid', 'channel_id']},
            'trials': {'path': str(outputs.trials_path.relative_to(outputs.dataset_dir)), 'primary_key': ['eid', 'trial_id']},
            'events': {'path': str(outputs.events_path.relative_to(outputs.dataset_dir)), 'primary_key': ['eid', 'event_id']},
            'unit_features': {'path': str(outputs.unit_features_path.relative_to(outputs.dataset_dir)), 'primary_key': ['pid', 'cluster_id']},
            'event_response_features': {'path': str(outputs.event_response_features_path.relative_to(outputs.dataset_dir)), 'primary_key': ['pid', 'cluster_id', 'event_name', 'window_spec']},
        },
        'stores': {
            'spikes': {
                'path': str(outputs.spikes_store_path.relative_to(outputs.dataset_dir)),
                'shard_key': 'pid',
                'container_format': SIGNAL_CONTAINER_FORMAT,
                'shard_layout': '<pid>/meta.json + <pid>/*.blosc',
                'arrays': ['spike_times_delta_ticks', 'spike_clusters', 'cluster_ids', 'cluster_spike_counts'],
            }
        },
    }


def _build_provenance(*, config: BuildConfig, clusters_path: Path, trials_path: Path) -> dict[str, Any]:
    return {
        'dataset_name': DATASET_NAME,
        'dataset_version': DATASET_VERSION,
        'created_at': bwm_shared.now_iso(),
        'source': {
            'freeze': bwm_simple.FREEZE,
            'sorter_revision': bwm_simple.SORTER_REVISION,
            'good_unit_rule': f"label >= {bwm_simple.GOOD_UNIT_THRESHOLD}",
            'clusters_table': bwm_simple._artifact_id(clusters_path),
            'trials_table': bwm_simple._artifact_id(trials_path),
        },
        'storage': {
            'metadata_format': 'parquet',
            'metadata_compression': PARQUET_COMPRESSION,
            'signal_format': SIGNAL_CONTAINER_FORMAT,
            'signal_compression': SIGNAL_COMPRESSION_VARIANT,
            'included_signal_stores': ['spikes'],
        },
        'spike_encoding': {
            'time_encoding': config.spike_time_encoding,
            'time_quantization_us': int(config.spike_time_quantization_us),
            'cluster_encoding': 'dense_local_indices',
            'delta_encoding_explicit': True,
            'time_storage_dtype': 'adaptive_uint16_or_uint32',
            'overflow_policy': 'hard_fail_if_uint16_limit_exceeded',
        },
    }


def _build_report(*, config: BuildConfig, sessions_df: pd.DataFrame, insertions_df: pd.DataFrame, units_df: pd.DataFrame, channels_df: pd.DataFrame, trials_df: pd.DataFrame, events_df: pd.DataFrame, unit_features_df: pd.DataFrame, event_response_features_df: pd.DataFrame, spike_stats: dict[str, Any], prefetch_report: dict[str, Any], stages: list[StageMetric]) -> dict[str, Any]:
    prefetch_attempted = bool(prefetch_report.get('actions'))
    return {
        'dataset_name': DATASET_NAME,
        'dataset_version': DATASET_VERSION,
        'build_timestamp': bwm_shared.now_iso(),
        'build_mode': 'local-cache-only' if not config.allow_remote_fetch else ('remote-prefetch-used' if prefetch_attempted else 'remote-prefetch-allowed-not-needed'),
        'package_versions': bwm_simple._package_versions(),
        'row_counts': {'sessions': int(len(sessions_df)), 'insertions': int(len(insertions_df)), 'units': int(len(units_df)), 'channels': int(len(channels_df)), 'trials': int(len(trials_df)), 'events': int(len(events_df)), 'unit_features': int(len(unit_features_df)), 'event_response_features': int(len(event_response_features_df))},
        'stores': {'spikes': spike_stats},
        'prefetch': {'enabled': bool(config.prefetch_missing), 'attempted': prefetch_attempted, 'initial_missing_required_assets': _preflight_has_missing_required_inputs(prefetch_report['initial']), 'final_missing_required_assets': _preflight_has_missing_required_inputs(prefetch_report['final'])},
        'stages': [{'name': stage.name, 'started_at': stage.started_at, 'elapsed_s': stage.elapsed_s, 'details': stage.details} for stage in stages],
    }


def _build_summary(*, sessions_df: pd.DataFrame, insertions_df: pd.DataFrame, units_df: pd.DataFrame, channels_df: pd.DataFrame, trials_df: pd.DataFrame, events_df: pd.DataFrame, unit_features_df: pd.DataFrame, event_response_features_df: pd.DataFrame, spike_stats: dict[str, Any], prefetch_report: dict[str, Any], stages: list[StageMetric]) -> str:
    lines = [
        '# BWM Ephys Dataset Build Summary',
        '',
        f"- Sessions: {len(sessions_df):,}",
        f"- Insertions: {len(insertions_df):,}",
        f"- Units: {len(units_df):,}",
        f"- Channels: {len(channels_df):,}",
        f"- Trials: {len(trials_df):,}",
        f"- Events: {len(events_df):,}",
        f"- Unit feature rows: {len(unit_features_df):,}",
        f"- Event-response feature rows: {len(event_response_features_df):,}",
        f"- Spikes written: {spike_stats['total_spikes_written']:,}",
        f"- Spike insertions written: {spike_stats['insertions_written']:,}",
        '', '## Workflow', '',
        f"- Initial missing required assets: `{_preflight_has_missing_required_inputs(prefetch_report['initial'])}`",
        f"- Prefetch enabled: `{prefetch_report['config']['prefetch_missing']}`",
        f"- Prefetch attempted: `{bool(prefetch_report.get('actions'))}`",
        f"- Final missing required assets: `{_preflight_has_missing_required_inputs(prefetch_report['final'])}`",
        f"- Parallel jobs: `{spike_stats['jobs']}`",
        f"- Spike shard bytes written: `{spike_stats.get('bytes_written', 0)}`",
        f"- Spike metrics file: `{spike_stats.get('metrics_path', SPIKE_METRICS_FILENAME)}`",
        '', '## Encoding', '',
        f"- Spike time encoding: `{spike_stats['time_encoding']}`",
        f"- Spike time quantization: `{spike_stats['time_quantization_us']}` us",
        f"- Spike time storage dtype: `{spike_stats['time_storage_dtype']}`",
        '- Cluster encoding: `dense_local_indices`',
        f"- Container format: `{spike_stats['container_format']}`",
        '', '## Stage timings', '', *[f"- `{stage.name}`: {stage.elapsed_s:.2f}s" for stage in stages], '',
    ]
    if spike_stats.get('slowest_insertions'):
        lines.extend(['## Slowest Insertions', ''])
        for item in spike_stats['slowest_insertions'][:10]:
            lines.append(f"- `{item['pid']}`: {item['total_s']:.2f}s, spikes={item.get('n_spikes',0):,}, status={item['status']}")
        lines.append('')
    if spike_stats['missing_insertions']:
        lines.extend(['## Missing Spike Insertions During Write', '', *[f"- `{pid}`" for pid in spike_stats['missing_insertions'][:25]], ''])
    if spike_stats.get('empty_insertions'):
        lines.extend(['## Empty Spike Insertions During Write', '', *[f"- `{pid}`" for pid in spike_stats['empty_insertions'][:25]], ''])
    if spike_stats.get('failed_insertions'):
        lines.extend(['## Failed Spike Insertions During Write', ''])
        for item in spike_stats['failed_insertions'][:25]:
            lines.append(f"- `{item['pid']}`: {item['error']}")
        lines.append('')
    return '\n'.join(lines) + '\n'


def _progress_line(label: str, completed: int, total: int, started_at: float, *, current: str | None = None, state: str | None = None) -> str:
    elapsed = max(perf_counter() - started_at, 1e-9)
    rate = completed / elapsed
    remaining = max(total - completed, 0)
    eta_s = remaining / rate if rate > 0 else float('inf')
    eta = '?' if eta_s == float('inf') else _format_seconds(eta_s)
    current_text = f" | current={current}" if current else ''
    state_text = f" | state={state}" if state else ''
    return f"{label}: {completed}/{total} ({completed / max(total, 1):.1%}) | {rate:.1f}/s | ETA {eta}{current_text}{state_text}"


def _format_seconds(seconds: float) -> str:
    mins, secs = divmod(int(round(seconds)), 60)
    hrs, mins = divmod(mins, 60)
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"


def _format_bytes(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024.0 or unit == 'TB':
            return f"{value:0.1f} {unit}"
        value /= 1024.0
    return f"{num_bytes} B"



def repair_bwm_ephys_spikes(*, dataset_dir: Path, cache_root: Path, jobs: int = DEFAULT_BUILD_JOBS, spike_time_quantization_us: int | None = None, limit_insertions: int | None = None, pids: list[str] | None = None, verbose: bool = True) -> BuildOutputs:
    if not dataset_dir.exists():
        raise BuildError(f"Dataset directory does not exist: {dataset_dir}")
    outputs = _final_outputs(dataset_dir)
    reporter = BuildProgressReporter(verbose=verbose)
    insertions_df = pd.read_parquet(outputs.insertions_path)
    units_df = pd.read_parquet(outputs.units_path)
    sessions_df = pd.read_parquet(outputs.sessions_path)
    channels_df = pd.read_parquet(outputs.channels_path)
    trials_df = pd.read_parquet(outputs.trials_path)
    events_df = pd.read_parquet(outputs.events_path)
    unit_features_df = pd.read_parquet(outputs.unit_features_path)
    event_response_features_df = _read_event_response_features(outputs.event_response_features_path)
    roster = insertions_df[["pid", "eid", "probe_name", "session_number", "date", "subject", "lab"]].copy()
    if pids:
        wanted = {str(pid) for pid in pids}
        roster = roster[roster["pid"].astype(str).isin(wanted)].copy()
    missing_pids, failed_pids, existing_metrics = _identify_repair_targets(outputs, roster)
    target_pids = sorted(set(missing_pids) | set(failed_pids))
    if limit_insertions is not None:
        target_pids = target_pids[:limit_insertions]
    if not target_pids:
        reporter.emit("Repair: no failed or missing spike shards found.")
        _refresh_dataset_sidecars(dataset_dir=dataset_dir, outputs=outputs, config=BuildConfig(output_root=dataset_dir.parent.parent, cache_root=cache_root, allow_remote_fetch=False, limit_insertions=None, spike_time_quantization_us=spike_time_quantization_us or _infer_quantization_us(outputs), jobs=jobs, verbose=verbose), sessions_df=sessions_df, insertions_df=insertions_df, units_df=units_df, channels_df=channels_df, trials_df=trials_df, events_df=events_df, unit_features_df=unit_features_df, event_response_features_df=event_response_features_df, spike_stats=_summarize_existing_spike_store(outputs, insertions_df), prefetch_report=_load_prefetch_report(outputs.prefetch_report_path), stages=[StageMetric(name='repair-spikes', started_at=bwm_shared.now_iso(), elapsed_s=0.0, details={'repaired_insertions': 0})], provenance_path=outputs.provenance_path)
        return outputs
    target_roster = roster[roster["pid"].astype(str).isin(set(target_pids))].copy()
    reporter.emit(f"Repair: patching {len(target_roster)} failed/missing insertion(s).")
    stage_perf, stage_started_at = reporter.stage_start('repair-spikes', f'patch {len(target_roster)} insertion(s)')
    repair_stats = _write_spike_store(outputs.spikes_store_path, roster=target_roster, units_df=units_df, cache_root=cache_root, spike_time_quantization_us=spike_time_quantization_us or _infer_quantization_us(outputs), jobs=jobs, reporter=reporter)
    repair_metrics = pd.read_parquet(outputs.spike_metrics_path) if outputs.spike_metrics_path.exists() else pd.DataFrame()
    merged_metrics = _merge_spike_metrics(existing_metrics, repair_metrics)
    if not merged_metrics.empty:
        merged_metrics.to_parquet(outputs.spike_metrics_path, engine=PARQUET_ENGINE, compression=PARQUET_COMPRESSION, index=False)
    spike_stats = _summarize_existing_spike_store(outputs, insertions_df, metrics_df=merged_metrics)
    repair_stage = reporter.stage_done('repair-spikes', stage_perf, stage_started_at, repaired_insertions=len(target_roster), fallback_insertions=repair_stats.get('fallback_insertions', 0))
    _refresh_dataset_sidecars(dataset_dir=dataset_dir, outputs=outputs, config=BuildConfig(output_root=dataset_dir.parent.parent, cache_root=cache_root, allow_remote_fetch=False, limit_insertions=None, spike_time_quantization_us=spike_time_quantization_us or _infer_quantization_us(outputs), jobs=jobs, verbose=verbose), sessions_df=sessions_df, insertions_df=insertions_df, units_df=units_df, channels_df=channels_df, trials_df=trials_df, events_df=events_df, unit_features_df=unit_features_df, event_response_features_df=event_response_features_df, spike_stats=spike_stats, prefetch_report=_load_prefetch_report(outputs.prefetch_report_path), stages=[repair_stage], provenance_path=outputs.provenance_path)
    return outputs


def refresh_bwm_ephys_features(
    *,
    dataset_dir: Path,
    cache_root: Path,
    jobs: int = DEFAULT_BUILD_JOBS,
    verbose: bool = True,
) -> BuildOutputs:
    if not dataset_dir.exists():
        raise BuildError(f"Dataset directory does not exist: {dataset_dir}")
    outputs = _final_outputs(dataset_dir)
    reporter = BuildProgressReporter(verbose=verbose)
    for path in (
        outputs.sessions_path,
        outputs.insertions_path,
        outputs.units_path,
        outputs.channels_path,
        outputs.trials_path,
        outputs.events_path,
    ):
        if not path.exists():
            raise BuildError(f"Required dataset table is missing: {path}")
    clusters_path = _resolve_clusters_table_from_cache_or_provenance(cache_root=cache_root, provenance_path=outputs.provenance_path)
    sessions_df = pd.read_parquet(outputs.sessions_path)
    insertions_df = pd.read_parquet(outputs.insertions_path)
    units_df = pd.read_parquet(outputs.units_path)
    channels_df = pd.read_parquet(outputs.channels_path)
    trials_df = pd.read_parquet(outputs.trials_path)
    events_df = pd.read_parquet(outputs.events_path)
    roster = insertions_df[["pid", "eid", "probe_name", "session_number", "date", "subject", "lab"]].copy()
    stage_perf, stage_started_at = reporter.stage_start("refresh-features", f"recompute derived features for {len(units_df):,} units")
    unit_features_df = _build_unit_features(clusters_path, units_df, roster=roster, cache_root=cache_root)
    event_response_features_df = _build_event_response_features(dataset_dir=dataset_dir, units_df=units_df, trials_df=trials_df, jobs=jobs, reporter=reporter)
    if not unit_features_df.empty:
        unit_features_df.sort_values(["pid", "cluster_id"], inplace=True, kind="mergesort")
    if not event_response_features_df.empty:
        event_response_features_df.sort_values(["pid", "cluster_id", "event_name"], inplace=True, kind="mergesort")
    outputs.unit_features_path.parent.mkdir(parents=True, exist_ok=True)
    unit_features_df.to_parquet(outputs.unit_features_path, engine=PARQUET_ENGINE, compression=PARQUET_COMPRESSION, index=False)
    event_response_features_df.to_parquet(outputs.event_response_features_path, engine=PARQUET_ENGINE, compression=PARQUET_COMPRESSION, index=False)
    refresh_stage = reporter.stage_done(
        "refresh-features",
        stage_perf,
        stage_started_at,
        unit_feature_rows=int(len(unit_features_df)),
        event_response_feature_rows=int(len(event_response_features_df)),
        waveform_columns_present=all(col in unit_features_df.columns for col in ("spike_width_ms", "peak_to_trough_ms", "waveform_amplitude_uv", "pt_ratio")),
    )
    spike_metrics_df = pd.read_parquet(outputs.spike_metrics_path) if outputs.spike_metrics_path.exists() else pd.DataFrame()
    spike_stats = _summarize_existing_spike_store(outputs, insertions_df, metrics_df=spike_metrics_df)
    _refresh_dataset_sidecars(
        dataset_dir=dataset_dir,
        outputs=outputs,
        config=BuildConfig(
            output_root=dataset_dir.parent.parent,
            cache_root=cache_root,
            allow_remote_fetch=False,
            limit_insertions=None,
            spike_time_quantization_us=_infer_quantization_us(outputs),
            jobs=jobs,
            verbose=verbose,
        ),
        sessions_df=sessions_df,
        insertions_df=insertions_df,
        units_df=units_df,
        channels_df=channels_df,
        trials_df=trials_df,
        events_df=events_df,
        unit_features_df=unit_features_df,
        event_response_features_df=event_response_features_df,
        spike_stats=spike_stats,
        prefetch_report=_load_prefetch_report(outputs.prefetch_report_path),
        stages=[refresh_stage],
        provenance_path=outputs.provenance_path,
    )
    refresh_report = {
        "dataset_dir": str(dataset_dir),
        "generated_at": bwm_shared.now_iso(),
        "operation": "refresh_bwm_ephys_features",
        "unit_feature_rows": int(len(unit_features_df)),
        "event_response_feature_rows": int(len(event_response_features_df)),
        "spikes_rebuilt": 0,
        "spikes_reused": int(spike_stats.get("insertions_written", 0)),
        "clusters_table": str(clusters_path),
        "jobs": int(jobs),
    }
    (dataset_dir / "feature_refresh_report.yaml").write_text(yaml.safe_dump(refresh_report, sort_keys=False), encoding="utf-8")
    return outputs


def _identify_repair_targets(outputs: BuildOutputs, roster: pd.DataFrame) -> tuple[list[str], list[str], pd.DataFrame]:
    existing_metrics = pd.read_parquet(outputs.spike_metrics_path) if outputs.spike_metrics_path.exists() else pd.DataFrame()
    shard_pids = {path.name for path in outputs.spikes_store_path.iterdir() if path.is_dir()} if outputs.spikes_store_path.exists() else set()
    roster_pids = [str(pid) for pid in roster['pid'].astype(str)]
    missing_pids = [pid for pid in roster_pids if pid not in shard_pids]
    failed_pids: list[str] = []
    if not existing_metrics.empty and 'status' in existing_metrics.columns:
        latest = existing_metrics.sort_values('completed_order').drop_duplicates('pid', keep='last')
        failed_pids = latest.loc[latest['status'].isin(['failed', 'missing']), 'pid'].astype(str).tolist()
    return missing_pids, failed_pids, existing_metrics


def _merge_spike_metrics(existing_metrics: pd.DataFrame, new_metrics: pd.DataFrame) -> pd.DataFrame:
    if existing_metrics.empty:
        return new_metrics.copy()
    if new_metrics.empty:
        return existing_metrics.copy()
    overlap = set(new_metrics['pid'].astype(str))
    kept = existing_metrics.loc[~existing_metrics['pid'].astype(str).isin(overlap)].copy()
    merged = pd.concat([kept, new_metrics], ignore_index=True)
    sort_cols = [col for col in ['completed_order', 'total_s', 'n_spikes'] if col in merged.columns]
    if sort_cols:
        ascending = [True] * len(sort_cols)
        merged.sort_values(sort_cols, ascending=ascending, inplace=True, kind='mergesort')
    return merged


def _read_event_response_features(path: Path) -> pd.DataFrame:
    if path.exists():
        return pd.read_parquet(path)
    return _empty_event_response_features_df()


def _summarize_existing_spike_store(outputs: BuildOutputs, insertions_df: pd.DataFrame, metrics_df: pd.DataFrame | None = None) -> dict[str, Any]:
    total_spikes = 0
    bytes_written = 0
    shard_pids: list[str] = []
    dtype_counts: dict[str, int] = {}
    fallback_insertions = 0
    slowest: list[dict[str, Any]] = []
    if outputs.spikes_store_path.exists():
        for shard_dir in sorted(path for path in outputs.spikes_store_path.iterdir() if path.is_dir()):
            meta_path = shard_dir / 'meta.json'
            if not meta_path.exists():
                continue
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
            shard_pids.append(str(meta['pid']))
            total_spikes += int(meta.get('n_spikes', 0))
            bytes_written += sum(path.stat().st_size for path in shard_dir.iterdir() if path.is_file())
            dtype = str(meta.get('storage_dtype', meta.get('time_dtype', 'unknown')))
            dtype_counts[dtype] = dtype_counts.get(dtype, 0) + 1
            if meta.get('fallback_used'):
                fallback_insertions += 1
    missing_insertions = [str(pid) for pid in insertions_df['pid'].astype(str) if str(pid) not in set(shard_pids)]
    failed_insertions: list[dict[str, Any]] = []
    metrics = metrics_df if metrics_df is not None else (pd.read_parquet(outputs.spike_metrics_path) if outputs.spike_metrics_path.exists() else pd.DataFrame())
    if not metrics.empty:
        latest = metrics.sort_values('completed_order').drop_duplicates('pid', keep='last')
        failed_insertions = latest.loc[latest['status'].isin(['failed', 'missing']), ['pid', 'error']].fillna('').to_dict('records')
        ok_latest = latest.loc[latest['status'] == 'ok'].copy()
        if 'fallback_used' in ok_latest.columns:
            fallback_series = ok_latest['fallback_used']
            fallback_series = fallback_series.where(fallback_series.notna(), False).astype(bool)
            fallback_insertions = int(fallback_series.sum())
        if 'total_s' in ok_latest.columns:
            slowest = ok_latest.sort_values('total_s', ascending=False).head(10)[['pid', 'total_s', 'n_spikes', 'status']].to_dict('records')
    dtype_label = 'adaptive_uint16_or_uint32' if dtype_counts else 'unknown'
    return {
        'insertions_written': int(len(shard_pids)),
        'total_spikes_written': int(total_spikes),
        'missing_insertions': missing_insertions,
        'empty_insertions': [],
        'failed_insertions': failed_insertions,
        'time_encoding': DEFAULT_SPIKE_TIME_ENCODING,
        'time_quantization_us': int(_infer_quantization_us(outputs)),
        'time_storage_dtype': dtype_label,
        'dtype_counts': dtype_counts,
        'jobs': None,
        'container_format': SIGNAL_CONTAINER_FORMAT,
        'bytes_written': int(bytes_written),
        'metrics_path': str(outputs.spike_metrics_path.name),
        'metrics_rows': int(len(metrics)),
        'running_peak': None,
        'slowest_insertions': slowest,
        'fallback_insertions': int(fallback_insertions),
    }


def _infer_quantization_us(outputs: BuildOutputs) -> int:
    if outputs.provenance_path.exists():
        provenance = yaml.safe_load(outputs.provenance_path.read_text(encoding='utf-8')) or {}
        return int(provenance.get('spike_encoding', {}).get('time_quantization_us', DEFAULT_SPIKE_TIME_QUANTIZATION_US))
    return DEFAULT_SPIKE_TIME_QUANTIZATION_US


def _resolve_clusters_table_from_cache_or_provenance(*, cache_root: Path, provenance_path: Path) -> Path:
    cache_candidate = cache_root / "openalyx.internationalbrainlab.org" / "bwm_tables" / "clusters.pqt"
    if cache_candidate.exists():
        return cache_candidate
    if provenance_path.exists():
        provenance = yaml.safe_load(provenance_path.read_text(encoding="utf-8")) or {}
        artifact = provenance.get("source", {}).get("clusters_table")
        if isinstance(artifact, dict):
            path_value = artifact.get("path")
            if path_value:
                candidate = Path(path_value)
                if candidate.exists():
                    return candidate
    raise BuildError(
        "Could not resolve the clusters aggregate table needed to refresh unit features. "
        f"Expected cache file at {cache_candidate} or a valid path in {provenance_path}."
    )


def _load_prefetch_report(path: Path) -> dict[str, Any]:
    if path.exists():
        return yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    empty_scan = {
        'aggregate_tables': {'clusters': {'present': True}, 'trials': {'present': True}},
        'signals': {
            'spikes': {'required_insertions': 0, 'present_insertions': 0, 'missing': []},
            'passive': {'required_sessions': 0, 'present_sessions': 0, 'missing': []},
        },
        'selection': {'insertions': 0, 'sessions': 0},
    }
    return {'config': {'prefetch_missing': False}, 'initial': empty_scan, 'final': empty_scan, 'actions': []}


def _refresh_dataset_sidecars(*, dataset_dir: Path, outputs: BuildOutputs, config: BuildConfig, sessions_df: pd.DataFrame, insertions_df: pd.DataFrame, units_df: pd.DataFrame, channels_df: pd.DataFrame, trials_df: pd.DataFrame, events_df: pd.DataFrame, unit_features_df: pd.DataFrame, event_response_features_df: pd.DataFrame, spike_stats: dict[str, Any], prefetch_report: dict[str, Any], stages: list[StageMetric], provenance_path: Path) -> None:
    schema = _build_schema(outputs)
    build_report = _build_report(config=config, sessions_df=sessions_df, insertions_df=insertions_df, units_df=units_df, channels_df=channels_df, trials_df=trials_df, events_df=events_df, unit_features_df=unit_features_df, event_response_features_df=event_response_features_df, spike_stats=spike_stats, prefetch_report=prefetch_report, stages=stages)
    summary = _build_summary(sessions_df=sessions_df, insertions_df=insertions_df, units_df=units_df, channels_df=channels_df, trials_df=trials_df, events_df=events_df, unit_features_df=unit_features_df, event_response_features_df=event_response_features_df, spike_stats=spike_stats, prefetch_report=prefetch_report, stages=stages)
    outputs.schema_path.write_text(yaml.safe_dump(schema, sort_keys=False), encoding='utf-8')
    outputs.build_report_path.write_text(yaml.safe_dump(build_report, sort_keys=False), encoding='utf-8')
    outputs.summary_path.write_text(summary, encoding='utf-8')
    provenance = yaml.safe_load(provenance_path.read_text(encoding='utf-8')) if provenance_path.exists() else {}
    provenance.setdefault('spike_encoding', {})['time_storage_dtype'] = 'adaptive_uint16_or_uint32'
    provenance['spike_encoding']['overflow_policy'] = 'fallback_to_uint32_if_uint16_limit_exceeded'
    provenance_path.write_text(yaml.safe_dump(provenance, sort_keys=False), encoding='utf-8')
    manifest = bwm_shared.build_manifest(dataset_name=DATASET_NAME, dataset_version=DATASET_VERSION, dataset_dir=dataset_dir)
    outputs.manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding='utf-8')


def normalize_bwm_ephys_spikes(*, dataset_dir: Path, cache_root: Path, jobs: int = DEFAULT_BUILD_JOBS, limit_insertions: int | None = None, pids: list[str] | None = None, rewrite_all: bool = False, create_empty_shards: bool = True, validate_decode: bool = True, verbose: bool = True) -> BuildOutputs:
    if not dataset_dir.exists():
        raise BuildError(f"Dataset directory does not exist: {dataset_dir}")
    outputs = _final_outputs(dataset_dir)
    reporter = BuildProgressReporter(verbose=verbose)
    insertions_df = pd.read_parquet(outputs.insertions_path)
    units_df = pd.read_parquet(outputs.units_path)
    sessions_df = pd.read_parquet(outputs.sessions_path)
    channels_df = pd.read_parquet(outputs.channels_path)
    trials_df = pd.read_parquet(outputs.trials_path)
    events_df = pd.read_parquet(outputs.events_path)
    unit_features_df = pd.read_parquet(outputs.unit_features_path)
    event_response_features_df = _read_event_response_features(outputs.event_response_features_path)
    roster = insertions_df[["pid", "eid", "probe_name", "session_number", "date", "subject", "lab"]].copy()
    if pids:
        wanted = {str(pid) for pid in pids}
        roster = roster[roster["pid"].astype(str).isin(wanted)].copy()
    if limit_insertions is not None:
        roster = roster.head(limit_insertions).copy()
    metrics_df = pd.read_parquet(outputs.spike_metrics_path) if outputs.spike_metrics_path.exists() else pd.DataFrame()
    latest_metrics = metrics_df.sort_values('completed_order').drop_duplicates('pid', keep='last') if not metrics_df.empty and 'completed_order' in metrics_df.columns else pd.DataFrame()
    units_by_pid = units_df.groupby('pid')['cluster_id'].apply(lambda s: np.sort(s.to_numpy(dtype=np.int32)))
    plan_rows: list[dict[str, Any]] = []
    rebuild_pids: list[str] = []
    metadata_patch_pids: list[str] = []
    empty_pids: list[str] = []
    for row in roster.itertuples(index=False):
        pid = str(row.pid)
        shard_dir = outputs.spikes_store_path / pid
        meta_path = shard_dir / 'meta.json'
        latest_status = None
        if not latest_metrics.empty and pid in set(latest_metrics['pid'].astype(str)):
            latest_status = latest_metrics.loc[latest_metrics['pid'].astype(str) == pid, 'status'].iloc[-1]
        if not shard_dir.exists() or not meta_path.exists():
            if latest_status == 'empty' and create_empty_shards:
                empty_pids.append(pid)
                plan_rows.append({'pid': pid, 'action': 'create_empty_shard', 'reason': 'empty_status_without_shard'})
            else:
                rebuild_pids.append(pid)
                plan_rows.append({'pid': pid, 'action': 'rebuild', 'reason': 'missing_shard'})
            continue
        action = 'metadata_patch'
        reason = []
        try:
            shard = bwm_shared.read_array_directory(shard_dir)
            meta = shard['meta']
            arrays = shard['arrays']
            patched = _normalize_spike_meta_dict(meta=meta, arrays=arrays, pid=pid)
            if rewrite_all:
                action = 'rewrite_shard'
                reason.append('rewrite_all')
            else:
                if patched != meta:
                    reason.append('meta_missing_fields')
                if validate_decode:
                    decoded = load_spike_shard(shard_dir)
                    if int(decoded['spike_times_delta_ticks'].shape[0]) != int(meta.get('n_spikes', decoded['spike_times_delta_ticks'].shape[0])):
                        action = 'rebuild'
                        reason.append('decode_length_mismatch')
                if not reason:
                    reason.append('already_normalized')
            if action == 'metadata_patch' and reason != ['already_normalized']:
                metadata_patch_pids.append(pid)
            elif action == 'rewrite_shard':
                rebuild_pids.append(pid)
        except Exception as exc:
            rebuild_pids.append(pid)
            action = 'rebuild'
            reason = [f'validation_failed:{exc}']
        plan_rows.append({'pid': pid, 'action': action, 'reason': ';'.join(reason)})
    plan_df = pd.DataFrame(plan_rows)
    if not plan_df.empty:
        (dataset_dir / 'spike_normalization_plan.parquet').write_bytes(b'')
        plan_df.to_parquet(dataset_dir / 'spike_normalization_plan.parquet', engine=PARQUET_ENGINE, compression=PARQUET_COMPRESSION, index=False)
    perf, started_at = reporter.stage_start('normalize-spikes', f"scan={len(roster)} patch={len(metadata_patch_pids)} empty={len(empty_pids)} rebuild={len(rebuild_pids)}")
    for pid in metadata_patch_pids:
        shard_dir = outputs.spikes_store_path / pid
        shard = bwm_shared.read_array_directory(shard_dir)
        patched = _normalize_spike_meta_dict(meta=shard['meta'], arrays=shard['arrays'], pid=pid)
        (shard_dir / 'meta.json').write_text(json.dumps(patched, indent=2, sort_keys=True), encoding='utf-8')
    for pid in empty_pids:
        _create_empty_spike_shard(outputs, insertions_df=insertions_df, units_by_pid=units_by_pid, pid=pid)
    if rebuild_pids:
        rebuild_roster = roster[roster['pid'].astype(str).isin(set(rebuild_pids))].copy()
        _write_spike_store(outputs.spikes_store_path, roster=rebuild_roster, units_df=units_df, cache_root=cache_root, spike_time_quantization_us=_infer_quantization_us(outputs), jobs=jobs, reporter=reporter)
    # Normalize all metas at end for consistency
    for shard_dir in sorted(p for p in outputs.spikes_store_path.iterdir() if p.is_dir()):
        shard = bwm_shared.read_array_directory(shard_dir)
        patched = _normalize_spike_meta_dict(meta=shard['meta'], arrays=shard['arrays'], pid=shard_dir.name)
        (shard_dir / 'meta.json').write_text(json.dumps(patched, indent=2, sort_keys=True), encoding='utf-8')
    refreshed_metrics = pd.read_parquet(outputs.spike_metrics_path) if outputs.spike_metrics_path.exists() else pd.DataFrame()
    spike_stats = _summarize_existing_spike_store(outputs, insertions_df, metrics_df=refreshed_metrics)
    stage = reporter.stage_done('normalize-spikes', perf, started_at, scanned=len(roster), metadata_patched=len(metadata_patch_pids), empty_created=len(empty_pids), rebuilt=len(rebuild_pids))
    _refresh_dataset_sidecars(dataset_dir=dataset_dir, outputs=outputs, config=BuildConfig(output_root=dataset_dir.parent.parent, cache_root=cache_root, allow_remote_fetch=False, limit_insertions=None, spike_time_quantization_us=_infer_quantization_us(outputs), jobs=jobs, verbose=verbose), sessions_df=sessions_df, insertions_df=insertions_df, units_df=units_df, channels_df=channels_df, trials_df=trials_df, events_df=events_df, unit_features_df=unit_features_df, event_response_features_df=event_response_features_df, spike_stats=spike_stats, prefetch_report=_load_prefetch_report(outputs.prefetch_report_path), stages=[stage], provenance_path=outputs.provenance_path)
    report = {
        'dataset_dir': str(dataset_dir),
        'generated_at': bwm_shared.now_iso(),
        'scanned_insertions': int(len(roster)),
        'metadata_patched': int(len(metadata_patch_pids)),
        'empty_shards_created': int(len(empty_pids)),
        'rebuilt_insertions': int(len(rebuild_pids)),
        'rewrite_all': bool(rewrite_all),
        'validate_decode': bool(validate_decode),
    }
    (dataset_dir / 'spike_normalization_report.yaml').write_text(yaml.safe_dump(report, sort_keys=False), encoding='utf-8')
    return outputs


def _normalize_spike_meta_dict(*, meta: dict[str, Any], arrays: dict[str, np.ndarray], pid: str) -> dict[str, Any]:
    patched = dict(meta)
    arrays_meta = patched.setdefault('arrays', {})
    for name, arr in arrays.items():
        if name in arrays_meta:
            arrays_meta[name].setdefault('entry', f'{name}.blosc')
        else:
            arrays_meta[name] = {'entry': f'{name}.blosc', 'dtype': arr.dtype.str, 'shape': list(arr.shape), 'order': 'C', 'nbytes': int(arr.nbytes)}
    time_arr = arrays.get('spike_times_delta_ticks')
    patched.setdefault('format', 'ibl_ai_agent_spike_shard_v2')
    patched.setdefault('dataset_name', DATASET_NAME)
    patched.setdefault('dataset_version', DATASET_VERSION)
    patched.setdefault('pid', pid)
    patched.setdefault('time_encoding', DEFAULT_SPIKE_TIME_ENCODING)
    patched.setdefault('cluster_encoding', 'dense_local_indices')
    patched.setdefault('compression', {'name': SIGNAL_COMPRESSION_VARIANT})
    patched.setdefault('delta_encoded', True)
    patched.setdefault('overflow_checked', True)
    if time_arr is not None:
        patched.setdefault('time_dtype', time_arr.dtype.str)
        patched.setdefault('storage_dtype', np.dtype(time_arr.dtype).name)
    else:
        patched.setdefault('time_dtype', np.dtype(DEFAULT_SPIKE_TIME_STORAGE_DTYPE).str)
        patched.setdefault('storage_dtype', np.dtype(DEFAULT_SPIKE_TIME_STORAGE_DTYPE).name)
    patched.setdefault('fallback_used', patched.get('storage_dtype') == np.dtype(FALLBACK_SPIKE_TIME_STORAGE_DTYPE).name)
    patched.setdefault('time_origin_seconds', 0.0)
    patched.setdefault('time_origin_ticks', 0)
    patched.setdefault('n_spikes', int(time_arr.shape[0] if time_arr is not None else 0))
    patched.setdefault('n_good_units', int(arrays.get('cluster_ids', np.asarray([], dtype=np.int32)).shape[0]))
    return patched


def _create_empty_spike_shard(outputs: BuildOutputs, *, insertions_df: pd.DataFrame, units_by_pid: pd.Series, pid: str) -> None:
    row = insertions_df.loc[insertions_df['pid'].astype(str) == pid].iloc[0]
    cluster_ids = units_by_pid.get(pid)
    if cluster_ids is None:
        cluster_ids = np.asarray([], dtype=np.int32)
    metadata = {
        'format': 'ibl_ai_agent_spike_shard_v2',
        'dataset_name': DATASET_NAME,
        'dataset_version': DATASET_VERSION,
        'pid': pid,
        'eid': str(row.eid),
        'probe_name': str(row.probe_name),
        'subject': str(row.subject),
        'date': str(row.date),
        'session_number': int(row.session_number),
        'lab': str(row.lab),
        'n_spikes': 0,
        'n_good_units': int(cluster_ids.shape[0]),
        'time_encoding': DEFAULT_SPIKE_TIME_ENCODING,
        'time_quantization_us': _infer_quantization_us(outputs),
        'cluster_encoding': 'dense_local_indices',
        'compression': {'name': SIGNAL_COMPRESSION_VARIANT},
        'time_dtype': np.dtype(DEFAULT_SPIKE_TIME_STORAGE_DTYPE).str,
        'storage_dtype': np.dtype(DEFAULT_SPIKE_TIME_STORAGE_DTYPE).name,
        'delta_encoded': True,
        'overflow_checked': True,
        'fallback_used': False,
        'time_origin_seconds': 0.0,
        'time_origin_ticks': 0,
    }
    arrays = {
        'spike_times_delta_ticks': np.asarray([], dtype=DEFAULT_SPIKE_TIME_STORAGE_DTYPE),
        'spike_clusters': np.asarray([], dtype=np.int32),
        'cluster_ids': cluster_ids.astype(np.int32, copy=False),
        'cluster_spike_counts': np.zeros(cluster_ids.shape[0], dtype=np.int32),
    }
    bwm_shared.write_array_directory(outputs.spikes_store_path / pid, metadata=metadata, arrays=arrays)

def _final_outputs(target_dir: Path) -> BuildOutputs:
    return BuildOutputs(dataset_dir=target_dir, sessions_path=target_dir / 'metadata' / 'sessions.parquet', insertions_path=target_dir / 'metadata' / 'insertions.parquet', units_path=target_dir / 'metadata' / 'units.parquet', channels_path=target_dir / 'metadata' / 'channels.parquet', trials_path=target_dir / 'metadata' / 'trials.parquet', events_path=target_dir / 'metadata' / 'events.parquet', unit_features_path=target_dir / 'features' / 'unit_features.parquet', event_response_features_path=target_dir / 'features' / 'event_response_features.parquet', manifest_path=target_dir / 'manifest.json', schema_path=target_dir / 'schema.yaml', provenance_path=target_dir / 'provenance.yaml', prefetch_report_path=target_dir / 'prefetch_report.yaml', build_report_path=target_dir / 'build_report.yaml', summary_path=target_dir / 'SUMMARY.md', spikes_store_path=target_dir / 'spikes', spike_metrics_path=target_dir / SPIKE_METRICS_FILENAME, wheel_store_path=target_dir / 'wheel', pose_store_path=target_dir / 'pose')
