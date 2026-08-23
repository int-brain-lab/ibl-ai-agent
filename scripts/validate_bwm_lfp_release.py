"""Validate a local bwm_lfp release directory after download.

Unlike bwm_ephys/bwm_behavior, bwm_lfp is a single HDF5 file produced upstream
by lfpack; its schema.yaml/provenance.yaml/manifest.json are authored by
scripts/download_datasets.py rather than shipped inside an archive. This
script checks those sidecars are present and consistent, and (when the
optional `lfp` extra is installed) opens the file to confirm the recording
and channel-count distributions.

Usage:
    UV_CACHE_DIR=.uv-cache uv run python scripts/validate_bwm_lfp_release.py \
        reports/datasets/bwm_lfp/1.0.0
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


DEFAULT_EXPECTED_VERSION = "1.1.0"
DEFAULT_EXPECTED_FILENAME = "lf_compressed_all_bwm.h5"
DEFAULT_EXPECTED_N_RECORDINGS = 699
DEFAULT_EXPECTED_CHANNEL_COUNT_DISTRIBUTION = {96: 4, 384: 695}

REQUIRED_ROOT_FILES = ("schema.yaml", "provenance.yaml", "manifest.json")


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


def validate_bwm_lfp_release(
    dataset_dir: Path,
    *,
    expected_version: str = DEFAULT_EXPECTED_VERSION,
    expected_filename: str = DEFAULT_EXPECTED_FILENAME,
    expected_n_recordings: int = DEFAULT_EXPECTED_N_RECORDINGS,
    expected_channel_count_distribution: dict[int, int] | None = None,
) -> ValidationReport:
    dataset_dir = dataset_dir.expanduser().resolve()
    report = ValidationReport(dataset_dir=dataset_dir)
    expected_channel_count_distribution = (
        DEFAULT_EXPECTED_CHANNEL_COUNT_DISTRIBUTION
        if expected_channel_count_distribution is None
        else expected_channel_count_distribution
    )
    expected_n_channels = sorted(expected_channel_count_distribution)

    report.check(dataset_dir.exists(), f"dataset directory exists: {dataset_dir}")
    if not dataset_dir.exists():
        return report

    for relative in (*REQUIRED_ROOT_FILES, expected_filename):
        report.check((dataset_dir / relative).exists(), f"required file exists: {relative}")

    schema = _read_yaml(dataset_dir / "schema.yaml", report, "schema.yaml")
    provenance = _read_yaml(dataset_dir / "provenance.yaml", report, "provenance.yaml")
    manifest = _read_json(dataset_dir / "manifest.json", report, "manifest.json")

    if schema is not None:
        report.check(schema.get("dataset_name") == "bwm_lfp", "schema dataset_name is bwm_lfp")
        report.check(
            str(schema.get("dataset_version")) == expected_version,
            f"schema dataset_version is {expected_version}",
        )
        stores = schema.get("stores")
        store = stores.get("lf_compressed") if isinstance(stores, dict) else None
        report.check(isinstance(store, dict), "schema advertises the lf_compressed store")
        if isinstance(store, dict):
            report.check(store.get("path") == expected_filename, f"schema store path is {expected_filename}")
            report.check(
                store.get("n_recordings") == expected_n_recordings,
                f"schema n_recordings is {expected_n_recordings}",
            )
            report.check(
                store.get("n_channels") == expected_n_channels,
                f"schema n_channels are {expected_n_channels}",
            )
            normalized_distribution = _normalize_channel_count_distribution(
                store.get("channel_count_distribution")
            )
            report.check(
                normalized_distribution == expected_channel_count_distribution,
                f"schema channel_count_distribution is {expected_channel_count_distribution}",
            )
            report.check(store.get("compression_tier") == "standard", "schema compression_tier is standard")

    if provenance is not None:
        report.check(provenance.get("dataset_name") == "bwm_lfp", "provenance dataset_name is bwm_lfp")
        report.check(
            str(provenance.get("dataset_version")) == expected_version,
            f"provenance dataset_version is {expected_version}",
        )
        source = provenance.get("source")
        report.check(
            isinstance(source, dict) and source.get("package") == "lfpack",
            "provenance records lfpack as the source package",
        )

    manifest_entry = None
    if manifest is not None:
        files = manifest.get("files")
        if not isinstance(files, list):
            files = []
        manifest_entry = next(
            (item for item in files if isinstance(item, dict) and item.get("path") == expected_filename),
            None,
        )
        report.check(manifest_entry is not None, f"manifest includes an entry for {expected_filename}")

    data_path = dataset_dir / expected_filename
    if data_path.exists():
        size_bytes = data_path.stat().st_size
        report.details["size_bytes"] = size_bytes
        if manifest_entry is not None:
            report.check(
                manifest_entry.get("size_bytes") == size_bytes,
                "manifest size_bytes matches the file on disk",
            )
            actual_sha1 = _sha1(data_path)
            report.details["sha1"] = actual_sha1
            report.check(
                manifest_entry.get("sha1") == actual_sha1,
                "manifest sha1 matches the file on disk",
            )

        try:
            import lfpack
        except ImportError:
            report.warn(
                "lfpack is not installed (optional `lfp` extra) — skipping the recording-count check; "
                "run `uv sync --extra lfp` to enable it"
            )
        else:
            try:
                recordings = lfpack.LFPackReader.recordings(str(data_path))
            except Exception as exc:
                report.failures.append(f"failed to open {expected_filename} with lfpack: {exc}")
            else:
                report.details["n_recordings"] = len(recordings)
                report.check(
                    len(recordings) == expected_n_recordings, f"file contains {expected_n_recordings} recordings"
                )
                try:
                    channel_count_distribution: dict[int, int] = {}
                    for recording in recordings:
                        reader = lfpack.LFPackReader(str(data_path), recording=recording, scale=0)
                        channel_count_distribution[reader.nc] = channel_count_distribution.get(reader.nc, 0) + 1
                except Exception as exc:
                    report.failures.append(f"failed to inspect recording channel counts with lfpack: {exc}")
                else:
                    report.details["channel_count_distribution"] = channel_count_distribution
                    report.check(
                        channel_count_distribution == expected_channel_count_distribution,
                        f"file channel-count distribution is {expected_channel_count_distribution}",
                    )

    return report


def _sha1(path: Path, chunk_size: int = 1024 * 1024) -> str:
    hasher = hashlib.sha1()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(chunk_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _normalize_channel_count_distribution(payload: Any) -> dict[int, int] | None:
    if not isinstance(payload, dict):
        return None
    try:
        return {int(key): int(value) for key, value in payload.items()}
    except (TypeError, ValueError):
        return None


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
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dataset_dir", type=Path, help="Downloaded bwm_lfp version directory")
    parser.add_argument("--expected-version", default=DEFAULT_EXPECTED_VERSION)
    parser.add_argument("--expected-filename", default=DEFAULT_EXPECTED_FILENAME)
    parser.add_argument("--expected-n-recordings", type=int, default=DEFAULT_EXPECTED_N_RECORDINGS)
    parser.add_argument("--expected-96-channel-recordings", type=int, default=4)
    parser.add_argument("--expected-384-channel-recordings", type=int, default=695)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    report = validate_bwm_lfp_release(
        args.dataset_dir,
        expected_version=args.expected_version,
        expected_filename=args.expected_filename,
        expected_n_recordings=args.expected_n_recordings,
        expected_channel_count_distribution={
            96: args.expected_96_channel_recordings,
            384: args.expected_384_channel_recordings,
        },
    )
    _print_report(report)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
