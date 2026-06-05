"""Validate a local bwm_ephys release archive after extraction.

This script is intentionally separate from the ordinary unit test suite because
the real bwm_ephys archive is large. Use it before merging or tagging a dataset
release that changes the public archive.

Usage:
    UV_CACHE_DIR=.uv-cache uv run python scripts/validate_bwm_ephys_release.py \
        reports/datasets/bwm_ephys/1.2.0 \
        --compare-legacy reports/datasets/bwm_ephys/1.1.0
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml


DEFAULT_EXPECTED_VERSION = "1.2.0"
DEFAULT_EXPECTED_CLUSTER_ROWS = 621_733
DEFAULT_EXPECTED_CLUSTER_COLUMNS = 59
DEFAULT_EXPECTED_ARRAY_BINS = 128

REQUIRED_TABLES = (
    "metadata/sessions.parquet",
    "metadata/insertions.parquet",
    "metadata/units.parquet",
    "metadata/channels.parquet",
    "metadata/trials.parquet",
    "metadata/events.parquet",
    "metadata/passive_sessions.parquet",
    "metadata/passive_events.parquet",
    "features/unit_features.parquet",
    "features/event_response_features.parquet",
    "features/passive_response_features.parquet",
)

REQUIRED_ROOT_FILES = (
    "schema.yaml",
    "manifest.json",
    "provenance.yaml",
    "clusters.pqt",
    "clusters.waveforms_peak.npy",
    "clusters.acgs_log.npy",
    "acgs_log.times.npy",
)

REQUIRED_CLUSTER_COLUMNS = {
    "pid",
    "eid",
    "cluster_id",
    "peak_channel",
    "peak_to_trough_duration",
    "peak_to_trough_ratio",
    "depolarisation_slope",
    "repolarisation_slope",
}

EXPECTED_TABLE_ROWS = {
    "metadata/sessions.parquet": 459,
    "metadata/insertions.parquet": 699,
    "metadata/units.parquet": 75_395,
    "metadata/channels.parquet": 267_264,
    "metadata/trials.parquet": 295_920,
    "metadata/events.parquet": 2_066_041,
    "metadata/passive_sessions.parquet": 459,
    "metadata/passive_events.parquet": 6_959_652,
    "features/unit_features.parquet": 75_395,
    "features/event_response_features.parquet": 376_975,
    "features/passive_response_features.parquet": 577_566,
}


@dataclass
class ValidationReport:
    dataset_dir: Path
    checks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.failures

    def check(self, condition: bool, message: str) -> None:
        if condition:
            self.checks.append(message)
        else:
            self.failures.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def validate_bwm_ephys_release(
    dataset_dir: Path,
    *,
    compare_legacy: Path | None = None,
    expected_version: str = DEFAULT_EXPECTED_VERSION,
    expected_cluster_rows: int = DEFAULT_EXPECTED_CLUSTER_ROWS,
    expected_cluster_columns: int = DEFAULT_EXPECTED_CLUSTER_COLUMNS,
    expected_array_bins: int = DEFAULT_EXPECTED_ARRAY_BINS,
    expected_table_rows: dict[str, int] | None = EXPECTED_TABLE_ROWS,
) -> ValidationReport:
    dataset_dir = dataset_dir.expanduser().resolve()
    report = ValidationReport(dataset_dir=dataset_dir)

    report.check(dataset_dir.exists(), f"dataset directory exists: {dataset_dir}")
    if not dataset_dir.exists():
        return report

    for relative in (*REQUIRED_ROOT_FILES, *REQUIRED_TABLES):
        report.check((dataset_dir / relative).exists(), f"required file exists: {relative}")

    schema = _read_yaml(dataset_dir / "schema.yaml", report, "schema.yaml")
    manifest = _read_json(dataset_dir / "manifest.json", report, "manifest.json")
    provenance = _read_yaml(dataset_dir / "provenance.yaml", report, "provenance.yaml")

    if schema:
        report.check(schema.get("dataset_name") == "bwm_ephys", "schema dataset_name is bwm_ephys")
        report.check(
            str(schema.get("dataset_version")) == expected_version,
            f"schema dataset_version is {expected_version}",
        )
        _validate_schema_entries(schema, report)
    if provenance:
        report.check(provenance.get("dataset_name") == "bwm_ephys", "provenance dataset_name is bwm_ephys")
        report.check(
            str(provenance.get("dataset_version")) == expected_version,
            f"provenance dataset_version is {expected_version}",
        )
    if manifest:
        manifest_paths = {str(item.get("path")) for item in manifest.get("files", []) if isinstance(item, dict)}
        for relative in REQUIRED_ROOT_FILES:
            if relative == "manifest.json":
                continue
            report.check(relative in manifest_paths, f"manifest includes {relative}")

    clusters = _read_parquet(dataset_dir / "clusters.pqt", report, "clusters.pqt")
    insertions = _read_parquet(dataset_dir / "metadata" / "insertions.parquet", report, "metadata/insertions.parquet")
    units = _read_parquet(dataset_dir / "metadata" / "units.parquet", report, "metadata/units.parquet")

    if expected_table_rows is not None:
        for relative, expected_rows in expected_table_rows.items():
            table = _read_parquet(dataset_dir / relative, report, relative)
            if table is not None:
                report.details[f"{relative}.rows"] = int(len(table))
                report.check(len(table) == expected_rows, f"{relative} has {expected_rows:,} rows")

    if clusters is not None:
        report.details["clusters_rows"] = int(len(clusters))
        report.details["clusters_columns"] = int(len(clusters.columns))
        report.check(len(clusters) == expected_cluster_rows, f"clusters.pqt has {expected_cluster_rows:,} rows")
        report.check(
            len(clusters.columns) == expected_cluster_columns,
            f"clusters.pqt has {expected_cluster_columns} columns",
        )
        missing_columns = sorted(REQUIRED_CLUSTER_COLUMNS.difference(clusters.columns))
        report.check(not missing_columns, f"clusters.pqt contains required columns: {sorted(REQUIRED_CLUSTER_COLUMNS)}")
        if {"pid", "cluster_id"}.issubset(clusters.columns):
            duplicates = int(clusters.duplicated(["pid", "cluster_id"]).sum())
            report.details["clusters_duplicate_keys"] = duplicates
            report.check(duplicates == 0, "clusters.pqt has unique (pid, cluster_id)")

    if clusters is not None and insertions is not None and "pid" in clusters.columns and "pid" in insertions.columns:
        cluster_pids = set(clusters["pid"].astype(str))
        insertion_pids = set(insertions["pid"].astype(str))
        missing_from_insertions = sorted(cluster_pids - insertion_pids)
        report.details["cluster_pid_count"] = len(cluster_pids)
        report.details["insertion_pid_count"] = len(insertion_pids)
        report.check(not missing_from_insertions, "all clusters.pqt pids exist in metadata/insertions.parquet")
        if len(cluster_pids) != len(insertion_pids):
            report.warn(
                "clusters.pqt and metadata/insertions.parquet have different pid counts "
                f"({len(cluster_pids)} vs {len(insertion_pids)})"
            )

    if clusters is not None and units is not None and {"pid", "cluster_id"}.issubset(clusters.columns) and {"pid", "cluster_id"}.issubset(units.columns):
        cluster_keys = set(zip(clusters["pid"].astype(str), clusters["cluster_id"].astype(str), strict=False))
        unit_keys = set(zip(units["pid"].astype(str), units["cluster_id"].astype(str), strict=False))
        report.details["unit_rows"] = int(len(units))
        report.check(unit_keys.issubset(cluster_keys), "metadata/units.parquet keys are a subset of clusters.pqt")

    _validate_array(
        dataset_dir / "clusters.waveforms_peak.npy",
        report,
        name="clusters.waveforms_peak.npy",
        expected_shape=(expected_cluster_rows, expected_array_bins),
        expected_dtype=np.float16,
    )
    _validate_array(
        dataset_dir / "clusters.acgs_log.npy",
        report,
        name="clusters.acgs_log.npy",
        expected_shape=(expected_cluster_rows, expected_array_bins),
        expected_dtype=np.float16,
    )
    acg_times = _validate_array(
        dataset_dir / "acgs_log.times.npy",
        report,
        name="acgs_log.times.npy",
        expected_shape=(expected_array_bins,),
        expected_dtype=np.float64,
    )
    if acg_times is not None:
        report.check(np.all(np.diff(acg_times) > 0), "acgs_log.times.npy is strictly increasing")

    if compare_legacy is not None:
        _compare_legacy_tables(report, dataset_dir, compare_legacy.expanduser().resolve())

    return report


def _read_yaml(path: Path, report: ValidationReport, label: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # pragma: no cover - defensive reporting
        report.failures.append(f"failed to read {label}: {exc}")
        return None
    if not isinstance(payload, dict):
        report.failures.append(f"{label} must contain a YAML mapping")
        return None
    return payload


def _read_json(path: Path, report: ValidationReport, label: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive reporting
        report.failures.append(f"failed to read {label}: {exc}")
        return None
    if not isinstance(payload, dict):
        report.failures.append(f"{label} must contain a JSON object")
        return None
    return payload


def _validate_schema_entries(schema: dict[str, Any], report: ValidationReport) -> None:
    tables = schema.get("tables")
    stores = schema.get("stores")
    if not isinstance(tables, dict):
        report.failures.append("schema tables must be a mapping")
        return
    if not isinstance(stores, dict):
        report.failures.append("schema stores must be a mapping")
        return

    clusters = tables.get("clusters")
    report.check(isinstance(clusters, dict), "schema advertises clusters table")
    if isinstance(clusters, dict):
        report.check(clusters.get("path") == "clusters.pqt", "schema clusters table path is clusters.pqt")
        report.check(
            clusters.get("primary_key") == ["pid", "cluster_id"],
            "schema clusters primary key is (pid, cluster_id)",
        )

    cells = stores.get("cells")
    report.check(isinstance(cells, dict), "schema advertises cells store")
    if isinstance(cells, dict):
        arrays = cells.get("arrays")
        report.check(isinstance(arrays, list), "schema cells store lists arrays")
        if isinstance(arrays, list):
            for relative in ("clusters.waveforms_peak.npy", "clusters.acgs_log.npy", "acgs_log.times.npy"):
                report.check(relative in arrays, f"schema cells store includes {relative}")


def _read_parquet(path: Path, report: ValidationReport, label: str) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception as exc:
        report.failures.append(f"failed to read {label}: {exc}")
        return None


def _validate_array(
    path: Path,
    report: ValidationReport,
    *,
    name: str,
    expected_shape: tuple[int, ...],
    expected_dtype: np.dtype[Any] | type[np.generic],
) -> np.ndarray | None:
    if not path.exists():
        return None
    try:
        array = np.load(path, mmap_mode="r")
    except Exception as exc:
        report.failures.append(f"failed to memory-map {name}: {exc}")
        return None
    report.details[f"{name}.shape"] = tuple(int(value) for value in array.shape)
    report.details[f"{name}.dtype"] = str(array.dtype)
    report.check(tuple(array.shape) == expected_shape, f"{name} shape is {expected_shape}")
    report.check(array.dtype == np.dtype(expected_dtype), f"{name} dtype is {np.dtype(expected_dtype).name}")
    return array


def _compare_legacy_tables(report: ValidationReport, dataset_dir: Path, legacy_dir: Path) -> None:
    report.check(legacy_dir.exists(), f"legacy comparison directory exists: {legacy_dir}")
    if not legacy_dir.exists():
        return

    for relative in REQUIRED_TABLES:
        current_path = dataset_dir / relative
        legacy_path = legacy_dir / relative
        if not legacy_path.exists():
            report.warn(f"legacy file missing, skipped comparison: {relative}")
            continue
        current = _read_parquet(current_path, report, relative)
        legacy = _read_parquet(legacy_path, report, f"legacy {relative}")
        if current is None or legacy is None:
            continue
        missing_columns = sorted(set(legacy.columns) - set(current.columns))
        report.check(not missing_columns, f"{relative} preserves legacy columns")
        report.check(len(current) == len(legacy), f"{relative} preserves legacy row count")

    current_spikes = dataset_dir / "spikes"
    legacy_spikes = legacy_dir / "spikes"
    if current_spikes.exists() and legacy_spikes.exists():
        current_pids = {path.name for path in current_spikes.iterdir() if path.is_dir()}
        legacy_pids = {path.name for path in legacy_spikes.iterdir() if path.is_dir()}
        report.check(legacy_pids.issubset(current_pids), "spikes/ preserves legacy pid shard directories")


def _print_report(report: ValidationReport) -> None:
    print(f"Dataset: {report.dataset_dir}")
    print(f"Checks passed: {len(report.checks)}")
    if report.details:
        print("Details:")
        for key, value in sorted(report.details.items()):
            print(f"  {key}: {value}")
    if report.warnings:
        print("Warnings:")
        for message in report.warnings:
            print(f"  - {message}")
    if report.failures:
        print("Failures:")
        for message in report.failures:
            print(f"  - {message}")
    print("Result:", "PASS" if report.ok else "FAIL")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir", type=Path, help="Extracted bwm_ephys version directory")
    parser.add_argument("--compare-legacy", type=Path, default=None, help="Optional bwm_ephys 1.1.0 directory")
    parser.add_argument("--expected-version", default=DEFAULT_EXPECTED_VERSION)
    parser.add_argument("--expected-cluster-rows", type=int, default=DEFAULT_EXPECTED_CLUSTER_ROWS)
    parser.add_argument("--expected-cluster-columns", type=int, default=DEFAULT_EXPECTED_CLUSTER_COLUMNS)
    parser.add_argument("--expected-array-bins", type=int, default=DEFAULT_EXPECTED_ARRAY_BINS)
    parser.add_argument("--skip-table-row-counts", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    report = validate_bwm_ephys_release(
        args.dataset_dir,
        compare_legacy=args.compare_legacy,
        expected_version=args.expected_version,
        expected_cluster_rows=args.expected_cluster_rows,
        expected_cluster_columns=args.expected_cluster_columns,
        expected_array_bins=args.expected_array_bins,
        expected_table_rows=None if args.skip_table_row_counts else EXPECTED_TABLE_ROWS,
    )
    _print_report(report)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
