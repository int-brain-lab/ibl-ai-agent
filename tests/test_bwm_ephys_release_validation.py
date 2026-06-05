from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import yaml


def _load_validator() -> ModuleType:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "validate_bwm_ephys_release.py"
    spec = importlib.util.spec_from_file_location("validate_bwm_ephys_release", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_table(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, engine="pyarrow", compression="zstd", index=False)


def _write_synthetic_ephys_release(root: Path, *, version: str = "1.2.0") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "spikes" / "pid-1").mkdir(parents=True)
    schema = {
        "dataset_name": "bwm_ephys",
        "dataset_version": version,
        "schema_version": 1,
        "tables": {
            "clusters": {"path": "clusters.pqt", "primary_key": ["pid", "cluster_id"]},
        },
        "stores": {
            "cells": {
                "arrays": [
                    "clusters.waveforms_peak.npy",
                    "clusters.acgs_log.npy",
                    "acgs_log.times.npy",
                ]
            }
        },
    }
    provenance = {"dataset_name": "bwm_ephys", "dataset_version": version}
    (root / "schema.yaml").write_text(yaml.safe_dump(schema), encoding="utf-8")
    (root / "provenance.yaml").write_text(yaml.safe_dump(provenance), encoding="utf-8")

    clusters = pd.DataFrame(
        {
            "pid": ["pid-1", "pid-1"],
            "eid": ["eid-1", "eid-1"],
            "cluster_id": [0, 1],
            "peak_channel": [10, 11],
            "peak_to_trough_duration": [0.0004, 0.0005],
            "peak_to_trough_ratio": [1.2, 1.4],
            "depolarisation_slope": [0.1, 0.2],
            "repolarisation_slope": [-0.1, -0.2],
            "extra_a": [1.0, 2.0],
            "extra_b": [3.0, 4.0],
        }
    )
    _write_table(root / "clusters.pqt", clusters)

    base_tables = {
        "metadata/sessions.parquet": pd.DataFrame({"eid": ["eid-1"]}),
        "metadata/insertions.parquet": pd.DataFrame({"pid": ["pid-1"], "eid": ["eid-1"]}),
        "metadata/units.parquet": pd.DataFrame({"pid": ["pid-1"], "cluster_id": [0]}),
        "metadata/channels.parquet": pd.DataFrame({"pid": ["pid-1"], "channel_id": [10]}),
        "metadata/trials.parquet": pd.DataFrame({"eid": ["eid-1"], "trial_id": [0]}),
        "metadata/events.parquet": pd.DataFrame({"eid": ["eid-1"], "event_id": [0]}),
        "metadata/passive_sessions.parquet": pd.DataFrame({"eid": ["eid-1"]}),
        "metadata/passive_events.parquet": pd.DataFrame({"eid": ["eid-1"], "event_id": [0]}),
        "features/unit_features.parquet": pd.DataFrame({"pid": ["pid-1"], "cluster_id": [0]}),
        "features/event_response_features.parquet": pd.DataFrame({"pid": ["pid-1"], "cluster_id": [0]}),
        "features/passive_response_features.parquet": pd.DataFrame({"pid": ["pid-1"], "cluster_id": [0]}),
    }
    for relative, frame in base_tables.items():
        _write_table(root / relative, frame)

    np.save(root / "clusters.waveforms_peak.npy", np.ones((2, 4), dtype=np.float16))
    np.save(root / "clusters.acgs_log.npy", np.ones((2, 4), dtype=np.float16))
    np.save(root / "acgs_log.times.npy", np.asarray([0.001, 0.002, 0.004, 0.008], dtype=np.float64))

    manifest_files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            manifest_files.append({"path": path.relative_to(root).as_posix(), "size_bytes": path.stat().st_size})
    (root / "manifest.json").write_text(
        json.dumps({"dataset_name": "bwm_ephys", "dataset_version": version, "files": manifest_files}),
        encoding="utf-8",
    )


def test_validate_synthetic_bwm_ephys_1_2_release(tmp_path: Path) -> None:
    module = _load_validator()
    dataset_dir = tmp_path / "bwm_ephys" / "1.2.0"
    _write_synthetic_ephys_release(dataset_dir)

    report = module.validate_bwm_ephys_release(
        dataset_dir,
        expected_cluster_rows=2,
        expected_cluster_columns=10,
        expected_array_bins=4,
        expected_table_rows={},
    )

    assert report.ok, report.failures
    assert report.details["clusters_rows"] == 2
    assert report.details["clusters.waveforms_peak.npy.shape"] == (2, 4)


def test_validate_synthetic_bwm_ephys_1_2_release_compares_legacy(tmp_path: Path) -> None:
    module = _load_validator()
    current_dir = tmp_path / "bwm_ephys" / "1.2.0"
    legacy_dir = tmp_path / "bwm_ephys" / "1.1.0"
    _write_synthetic_ephys_release(current_dir)
    _write_synthetic_ephys_release(legacy_dir, version="1.1.0")

    report = module.validate_bwm_ephys_release(
        current_dir,
        compare_legacy=legacy_dir,
        expected_cluster_rows=2,
        expected_cluster_columns=10,
        expected_array_bins=4,
        expected_table_rows={},
    )

    assert report.ok, report.failures
    assert any("preserves legacy row count" in check for check in report.checks)


def test_validate_synthetic_bwm_ephys_release_rejects_misaligned_arrays(tmp_path: Path) -> None:
    module = _load_validator()
    dataset_dir = tmp_path / "bwm_ephys" / "1.2.0"
    _write_synthetic_ephys_release(dataset_dir)
    np.save(dataset_dir / "clusters.acgs_log.npy", np.ones((1, 4), dtype=np.float16))

    report = module.validate_bwm_ephys_release(
        dataset_dir,
        expected_cluster_rows=2,
        expected_cluster_columns=10,
        expected_array_bins=4,
        expected_table_rows={},
    )

    assert not report.ok
    assert any("clusters.acgs_log.npy shape" in failure for failure in report.failures)
