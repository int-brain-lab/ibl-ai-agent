from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from ibl_ai_agent.datasets import bwm_ephys, bwm_simple


class DummyBrainRegions:
    def acronym2acronym(self, acronym, mapping=None):
        values = np.asarray(acronym, dtype=object)
        out = []
        for item in values:
            if item == "VISp":
                out.append("VISp")
            elif item == "MOs":
                out.append("MOs")
            else:
                out.append("root")
        return np.asarray(out, dtype=object)

    def acronym2id(self, acronym, mapping=None, hemisphere=None):
        values = np.asarray(acronym, dtype=object)
        out = []
        for item in values:
            if item == "VISp":
                out.append(10)
            elif item == "MOs":
                out.append(20)
            else:
                out.append(997)
        return np.asarray(out, dtype=np.int32)

    def id2acronym(self, atlas_id, mapping=None):
        values = np.asarray(atlas_id)
        out = []
        for item in values:
            if int(item) == 100:
                out.append("VISp")
            elif int(item) == 200:
                out.append("MOs")
            else:
                out.append("root")
        return np.asarray(out, dtype=object)


def _write_channel_files(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    np.save(root / "channels.rawInd.npy", np.asarray([0, 1], dtype=np.int64))
    np.save(root / "channels.labels.npy", np.asarray([1, 2], dtype=np.int8))
    np.save(root / "channels.brainLocationIds_ccf_2017.npy", np.asarray([100, 200], dtype=np.int64))
    np.save(root / "channels.localCoordinates.npy", np.asarray([[11.0, 20.0], [43.0, 40.0]], dtype=np.float32))
    np.save(root / "channels.mlapdv.npy", np.asarray([[1000, 2000, 3000], [1100, 2100, 3100]], dtype=np.int32))


def _write_spike_files(root: Path) -> None:
    np.save(root / "spikes.times.npy", np.asarray([0.001, 0.003, 0.010, 0.014], dtype=np.float64))
    np.save(root / "spikes.clusters.npy", np.asarray([0, 0, 1, 0], dtype=np.int32))


def _write_waveform_files(root: Path) -> None:
    templates = np.asarray(
        [
            [[0.0, -2.0, 1.0, 0.2], [0.0, -1.0, 0.5, 0.1]],
            [[0.0, -3.0, 1.5, 0.3], [0.0, -1.5, 0.7, 0.2]],
        ],
        dtype=np.float32,
    )
    np.save(root / "waveforms.templates.npy", templates)
    pd.DataFrame({"cluster": [0, 1], "sample": [0, 0]}).to_parquet(
        root / "waveforms.table.pqt", engine="pyarrow", compression="zstd", index=False
    )


def _write_wheel_files(alf_root: Path) -> None:
    np.save(alf_root / "wheel.timestamps.npy", np.asarray([0.0, 0.5, 1.0], dtype=np.float64))
    np.save(alf_root / "wheel.position.npy", np.asarray([0.0, 1.0, 1.5], dtype=np.float32))


def _write_pose_files(alf_root: Path) -> None:
    np.save(alf_root / "leftCamera.times.npy", np.asarray([0.0, 0.5, 1.0], dtype=np.float64))
    np.save(alf_root / "leftCamera.dlc.npy", np.asarray([[1.0, 2.0], [1.5, 2.5], [2.0, 3.0]], dtype=np.float32))
    pd.DataFrame({"pupilDiameter": [1.0, 1.1, 1.2], "likelihood": [0.9, 0.8, 0.95]}).to_parquet(
        alf_root / "leftCamera.features.pqt", engine="pyarrow", compression="zstd", index=False
    )


def test_build_bwm_ephys_dataset_small_synthetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("numcodecs")

    roster = pd.DataFrame(
        [
            {
                "pid": "pid-1",
                "eid": "eid-1",
                "probe_name": "probe00",
                "session_number": 1,
                "date": "2020-01-01",
                "subject": "SUBJ_1",
                "lab": "lab_a",
            }
        ]
    )
    monkeypatch.setattr(bwm_simple, "bwm_query", lambda freeze: roster.copy())
    monkeypatch.setattr(bwm_simple, "BrainRegions", DummyBrainRegions)

    cache_root = tmp_path / "one-cache"
    bwm_tables = cache_root / "openalyx.internationalbrainlab.org" / "bwm_tables"
    bwm_tables.mkdir(parents=True, exist_ok=True)

    clusters = pd.DataFrame(
        [
            {
                "pid": "pid-1",
                "eid": "eid-1",
                "cluster_id": 0,
                "channels": 0,
                "label": 1.0,
                "atlas_id": 100,
                "presence_ratio": 0.95,
                "peakToTrough": 0.45,
                "spike_width": 0.20,
                "depths": 20.0,
                "spike_count": 1000.0,
                "firing_rate": 5.0,
                "x": 1.0,
                "y": 2.0,
                "z": 3.0,
                "axial_um": 20.0,
                "lateral_um": 11.0,
            },
            {
                "pid": "pid-1",
                "eid": "eid-1",
                "cluster_id": 1,
                "channels": 1,
                "label": 1.1,
                "atlas_id": 200,
                "presence_ratio": 0.90,
                "peakToTrough": 0.50,
                "spike_width": 0.25,
                "depths": 40.0,
                "spike_count": 250.0,
                "firing_rate": 2.5,
                "x": 10.0,
                "y": 20.0,
                "z": 30.0,
                "axial_um": 40.0,
                "lateral_um": 43.0,
            },
        ]
    )
    clusters.to_parquet(bwm_tables / "clusters.pqt", engine="pyarrow", compression="zstd", index=False)

    trials = pd.DataFrame(
        [
            {
                "eid": "eid-1",
                "firstMovement_times": 0.6,
                "goCue_times": 0.5,
                "probabilityLeft": 0.8,
                "response_times": 0.7,
                "feedbackType": 1.0,
                "contrastRight": 0.0,
                "choice": 1.0,
                "feedback_times": 0.8,
                "stimOn_times": 0.3,
                "contrastLeft": 1.0,
                "intervals_0": 0.0,
                "intervals_1": 1.0,
                "bwm_include": True,
            }
        ]
    )
    trials.to_parquet(bwm_tables / "trials.pqt", engine="pyarrow", compression="zstd", index=False)

    alf_root = (
        cache_root
        / "openalyx.internationalbrainlab.org"
        / "lab_a"
        / "Subjects"
        / "SUBJ_1"
        / "2020-01-01"
        / "001"
        / "alf"
    )
    revision_dir = alf_root / "probe00" / "pykilosort" / f"#{bwm_simple.SORTER_REVISION}#"
    _write_channel_files(revision_dir)
    _write_spike_files(revision_dir)
    _write_waveform_files(revision_dir)
    _write_wheel_files(alf_root)
    _write_pose_files(alf_root)

    outputs = bwm_ephys.build_bwm_ephys_dataset(
        bwm_ephys.BuildConfig(
            output_root=tmp_path / "out",
            cache_root=cache_root,
            allow_remote_fetch=False,
            jobs=2,
            spike_time_quantization_us=100,
            spike_time_encoding="delta_int_ticks",
        )
    )

    assert outputs.dataset_dir.exists()
    assert outputs.events_path.exists()
    assert outputs.spikes_store_path.exists()
    assert (outputs.spikes_store_path / "pid-1" / "meta.json").exists()
    assert not outputs.wheel_store_path.exists()
    assert not outputs.pose_store_path.exists()

    shard = bwm_ephys.load_spike_shard(outputs.spikes_store_path / "pid-1")
    assert shard["spike_times_delta_ticks"].dtype == np.uint16
    assert np.allclose(shard["spike_times_seconds"], np.asarray([0.001, 0.003, 0.010, 0.014]))

    events = pd.read_parquet(outputs.events_path)
    assert set(events["event_name"]) >= {"stimOn", "goCue", "firstMovement", "response", "feedback"}

    unit_features = pd.read_parquet(outputs.unit_features_path)
    assert "peak_to_trough_ms" in unit_features.columns
    assert "spike_width_ms" in unit_features.columns

    event_response_features = pd.read_parquet(outputs.event_response_features_path)
    assert set(["pid", "cluster_id", "event_name", "window_spec", "baseline_fr", "peak_fr", "peak_latency_ms", "modulation_index"]).issubset(event_response_features.columns)
    assert "stimOn" in set(event_response_features["event_name"].astype(str))

    provenance = yaml.safe_load(outputs.provenance_path.read_text(encoding="utf-8"))
    assert provenance["spike_encoding"]["time_encoding"] == "delta_int_ticks"
    assert provenance["spike_encoding"]["time_quantization_us"] == 100
    assert provenance["spike_encoding"]["time_storage_dtype"] == "adaptive_uint16_or_uint32"



def test_build_bwm_ephys_dataset_waveform_feature_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("numcodecs")

    roster = pd.DataFrame(
        [
            {
                "pid": "pid-1",
                "eid": "eid-1",
                "probe_name": "probe00",
                "session_number": 1,
                "date": "2020-01-01",
                "subject": "SUBJ_1",
                "lab": "lab_a",
            }
        ]
    )
    monkeypatch.setattr(bwm_simple, "bwm_query", lambda freeze: roster.copy())
    monkeypatch.setattr(bwm_simple, "BrainRegions", DummyBrainRegions)

    cache_root = tmp_path / "one-cache"
    bwm_tables = cache_root / "openalyx.internationalbrainlab.org" / "bwm_tables"
    bwm_tables.mkdir(parents=True, exist_ok=True)

    clusters = pd.DataFrame(
        [
            {
                "pid": "pid-1",
                "eid": "eid-1",
                "cluster_id": 0,
                "channels": 0,
                "label": 1.0,
                "atlas_id": 100,
                "presence_ratio": 0.95,
                "depths": 20.0,
                "spike_count": 1000.0,
                "firing_rate": 5.0,
                "x": 1.0,
                "y": 2.0,
                "z": 3.0,
                "axial_um": 20.0,
                "lateral_um": 11.0,
            },
            {
                "pid": "pid-1",
                "eid": "eid-1",
                "cluster_id": 1,
                "channels": 1,
                "label": 1.1,
                "atlas_id": 200,
                "presence_ratio": 0.90,
                "depths": 40.0,
                "spike_count": 250.0,
                "firing_rate": 2.5,
                "x": 10.0,
                "y": 20.0,
                "z": 30.0,
                "axial_um": 40.0,
                "lateral_um": 43.0,
            },
        ]
    )
    clusters.to_parquet(bwm_tables / "clusters.pqt", engine="pyarrow", compression="zstd", index=False)

    trials = pd.DataFrame(
        [
            {
                "eid": "eid-1",
                "firstMovement_times": 0.6,
                "goCue_times": 0.5,
                "probabilityLeft": 0.8,
                "response_times": 0.7,
                "feedbackType": 1.0,
                "contrastRight": 0.0,
                "choice": 1.0,
                "feedback_times": 0.8,
                "stimOn_times": 0.3,
                "contrastLeft": 1.0,
                "intervals_0": 0.0,
                "intervals_1": 1.0,
                "bwm_include": True,
            }
        ]
    )
    trials.to_parquet(bwm_tables / "trials.pqt", engine="pyarrow", compression="zstd", index=False)

    alf_root = (
        cache_root
        / "openalyx.internationalbrainlab.org"
        / "lab_a"
        / "Subjects"
        / "SUBJ_1"
        / "2020-01-01"
        / "001"
        / "alf"
    )
    revision_dir = alf_root / "probe00" / "pykilosort" / f"#{bwm_simple.SORTER_REVISION}#"
    _write_channel_files(revision_dir)
    _write_spike_files(revision_dir)
    _write_waveform_files(revision_dir)

    outputs = bwm_ephys.build_bwm_ephys_dataset(
        bwm_ephys.BuildConfig(
            output_root=tmp_path / "out",
            cache_root=cache_root,
            allow_remote_fetch=False,
            jobs=1,
            spike_time_quantization_us=100,
            spike_time_encoding="delta_int_ticks",
        )
    )

    unit_features = pd.read_parquet(outputs.unit_features_path)
    assert unit_features["spike_width_ms"].notna().all()
    assert unit_features["peak_to_trough_ms"].notna().all()
    assert unit_features["waveform_amplitude_uv"].notna().all()
    assert unit_features["pt_ratio"].notna().all()
    assert (unit_features["spike_width_ms"] > 0).all()
    assert (unit_features["peak_to_trough_ms"] > 0).all()
    assert (unit_features["waveform_amplitude_uv"] > 0).all()

    event_response_features = pd.read_parquet(outputs.event_response_features_path)
    assert event_response_features["peak_latency_ms"].notna().any()


def test_refresh_bwm_ephys_features_reuses_existing_spike_shards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("numcodecs")

    roster = pd.DataFrame(
        [
            {
                "pid": "pid-1",
                "eid": "eid-1",
                "probe_name": "probe00",
                "session_number": 1,
                "date": "2020-01-01",
                "subject": "SUBJ_1",
                "lab": "lab_a",
            }
        ]
    )
    monkeypatch.setattr(bwm_simple, "bwm_query", lambda freeze: roster.copy())
    monkeypatch.setattr(bwm_simple, "BrainRegions", DummyBrainRegions)

    cache_root = tmp_path / "one-cache"
    bwm_tables = cache_root / "openalyx.internationalbrainlab.org" / "bwm_tables"
    bwm_tables.mkdir(parents=True, exist_ok=True)

    clusters_initial = pd.DataFrame(
        [
            {
                "pid": "pid-1",
                "eid": "eid-1",
                "cluster_id": 0,
                "channels": 0,
                "label": 1.0,
                "atlas_id": 100,
                "presence_ratio": 0.95,
                "depths": 20.0,
                "spike_count": 1000.0,
                "firing_rate": 5.0,
                "x": 1.0,
                "y": 2.0,
                "z": 3.0,
                "axial_um": 20.0,
                "lateral_um": 11.0,
            },
            {
                "pid": "pid-1",
                "eid": "eid-1",
                "cluster_id": 1,
                "channels": 1,
                "label": 1.1,
                "atlas_id": 200,
                "presence_ratio": 0.90,
                "depths": 40.0,
                "spike_count": 250.0,
                "firing_rate": 2.5,
                "x": 10.0,
                "y": 20.0,
                "z": 30.0,
                "axial_um": 40.0,
                "lateral_um": 43.0,
            },
        ]
    )
    clusters_initial.to_parquet(bwm_tables / "clusters.pqt", engine="pyarrow", compression="zstd", index=False)

    trials = pd.DataFrame(
        [
            {
                "eid": "eid-1",
                "firstMovement_times": 0.6,
                "goCue_times": 0.5,
                "probabilityLeft": 0.8,
                "response_times": 0.7,
                "feedbackType": 1.0,
                "contrastRight": 0.0,
                "choice": 1.0,
                "feedback_times": 0.8,
                "stimOn_times": 0.3,
                "contrastLeft": 1.0,
                "intervals_0": 0.0,
                "intervals_1": 1.0,
                "bwm_include": True,
            }
        ]
    )
    trials.to_parquet(bwm_tables / "trials.pqt", engine="pyarrow", compression="zstd", index=False)

    alf_root = (
        cache_root
        / "openalyx.internationalbrainlab.org"
        / "lab_a"
        / "Subjects"
        / "SUBJ_1"
        / "2020-01-01"
        / "001"
        / "alf"
    )
    revision_dir = alf_root / "probe00" / "pykilosort" / f"#{bwm_simple.SORTER_REVISION}#"
    _write_channel_files(revision_dir)
    _write_spike_files(revision_dir)
    _write_waveform_files(revision_dir)

    outputs = bwm_ephys.build_bwm_ephys_dataset(
        bwm_ephys.BuildConfig(
            output_root=tmp_path / "out",
            cache_root=cache_root,
            allow_remote_fetch=False,
            jobs=1,
            spike_time_quantization_us=100,
            spike_time_encoding="delta_int_ticks",
        )
    )

    shard_path = outputs.spikes_store_path / "pid-1" / "spike_times_delta_ticks.blosc"
    before_mtime = shard_path.stat().st_mtime_ns

    clusters_refresh = clusters_initial.copy()
    clusters_refresh["peakToTrough"] = [0.45, 0.50]
    clusters_refresh["spike_width"] = [0.20, 0.25]
    clusters_refresh.to_parquet(bwm_tables / "clusters.pqt", engine="pyarrow", compression="zstd", index=False)

    refreshed = bwm_ephys.refresh_bwm_ephys_features(dataset_dir=outputs.dataset_dir, cache_root=cache_root, verbose=False)
    assert refreshed.dataset_dir == outputs.dataset_dir
    assert shard_path.stat().st_mtime_ns == before_mtime

    unit_features = pd.read_parquet(outputs.unit_features_path)
    assert unit_features["spike_width_ms"].notna().all()
    assert unit_features["peak_to_trough_ms"].notna().all()
    event_response_features = pd.read_parquet(outputs.event_response_features_path)
    assert not event_response_features.empty
    assert "stimOn" in set(event_response_features["event_name"].astype(str))
    assert (outputs.dataset_dir / "feature_refresh_report.yaml").exists()
