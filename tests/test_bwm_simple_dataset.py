from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from ibl_ai_agent.datasets import bwm_simple
from ibl_ai_agent.core.access import OfflineModeError


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
    np.save(
        root / "channels.localCoordinates.npy",
        np.asarray([[11.0, 20.0], [43.0, 40.0]], dtype=np.float32),
    )
    np.save(
        root / "channels.mlapdv.npy",
        np.asarray([[1000, 2000, 3000], [1100, 2100, 3100]], dtype=np.int32),
    )


def test_build_bwm_simple_dataset_small_synthetic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
            },
            {
                "pid": "pid-2",
                "eid": "eid-2",
                "probe_name": "probe00",
                "session_number": 1,
                "date": "2020-01-02",
                "subject": "SUBJ_2",
                "lab": "lab_b",
            },
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
                "acronym": "VISp",
                "x": 1.0,
                "y": 2.0,
                "z": 3.0,
                "axial_um": 20.0,
                "lateral_um": 11.0,
                "depths": 20.0,
                "spike_count": 1000.0,
                "firing_rate": 5.0,
            },
            {
                "pid": "pid-1",
                "eid": "eid-1",
                "cluster_id": 1,
                "channels": 1,
                "label": 0.5,
                "atlas_id": 100,
                "acronym": "VISp",
                "x": 1.0,
                "y": 2.0,
                "z": 3.0,
                "axial_um": 40.0,
                "lateral_um": 43.0,
                "depths": 40.0,
                "spike_count": 100.0,
                "firing_rate": 0.5,
            },
            {
                "pid": "pid-2",
                "eid": "eid-2",
                "cluster_id": 0,
                "channels": 1,
                "label": 1.2,
                "atlas_id": 200,
                "acronym": "MOs",
                "x": 10.0,
                "y": 20.0,
                "z": 30.0,
                "axial_um": 40.0,
                "lateral_um": 43.0,
                "depths": 40.0,
                "spike_count": 250.0,
                "firing_rate": 2.5,
            },
        ]
    )
    clusters.to_parquet(bwm_tables / "clusters.pqt", engine="pyarrow", compression="zstd", index=False)

    trials = pd.DataFrame(
        [
            {
                "eid": "eid-1",
                "stimOff_times": 0.9,
                "goCueTrigger_times": 0.4,
                "firstMovement_times": 0.6,
                "goCue_times": 0.5,
                "probabilityLeft": 0.8,
                "response_times": 0.7,
                "feedbackType": 1.0,
                "rewardVolume": 1.5,
                "contrastRight": 0.0,
                "choice": 1.0,
                "feedback_times": 0.8,
                "stimOn_times": 0.3,
                "contrastLeft": 1.0,
                "intervals_0": 0.0,
                "intervals_1": 1.0,
                "bwm_include": True,
            },
            {
                "eid": "eid-1",
                "stimOff_times": 1.9,
                "goCueTrigger_times": 1.4,
                "firstMovement_times": 1.6,
                "goCue_times": 1.5,
                "probabilityLeft": 0.2,
                "response_times": 1.7,
                "feedbackType": -1.0,
                "rewardVolume": 1.5,
                "contrastRight": 1.0,
                "choice": -1.0,
                "feedback_times": 1.8,
                "stimOn_times": 1.3,
                "contrastLeft": 0.0,
                "intervals_0": 1.0,
                "intervals_1": 2.0,
                "bwm_include": False,
            },
            {
                "eid": "eid-2",
                "stimOff_times": 0.9,
                "goCueTrigger_times": 0.4,
                "firstMovement_times": 0.6,
                "goCue_times": 0.5,
                "probabilityLeft": 0.5,
                "response_times": 0.7,
                "feedbackType": 1.0,
                "rewardVolume": 1.5,
                "contrastRight": 0.0,
                "choice": 1.0,
                "feedback_times": 0.8,
                "stimOn_times": 0.3,
                "contrastLeft": 0.5,
                "intervals_0": 0.0,
                "intervals_1": 1.0,
                "bwm_include": True,
            },
        ]
    )
    trials.to_parquet(bwm_tables / "trials.pqt", engine="pyarrow", compression="zstd", index=False)

    for row in roster.itertuples(index=False):
        revision_dir = (
            cache_root
            / "openalyx.internationalbrainlab.org"
            / row.lab
            / "Subjects"
            / row.subject
            / row.date
            / f"{int(row.session_number):03d}"
            / "alf"
            / row.probe_name
            / "pykilosort"
            / f"#{bwm_simple.SORTER_REVISION}#"
        )
        _write_channel_files(revision_dir)

    outputs = bwm_simple.build_bwm_simple_dataset(
        bwm_simple.BuildConfig(
            output_root=tmp_path / "out",
            cache_root=cache_root,
            allow_remote_fetch=False,
            limit_insertions=1,
        )
    )

    insertions_df = pd.read_parquet(outputs.insertions_path)
    units_df = pd.read_parquet(outputs.units_path)
    trials_df = pd.read_parquet(outputs.trials_path)
    channels_df = pd.read_parquet(outputs.channels_path)
    metadata = yaml.safe_load(outputs.metadata_path.read_text(encoding="utf-8"))
    build_report = yaml.safe_load(outputs.build_report_path.read_text(encoding="utf-8"))

    assert len(insertions_df) == 1
    assert insertions_df.iloc[0]["pid"] == "pid-1"
    assert insertions_df.iloc[0]["n_good_units"] == 1
    assert insertions_df.iloc[0]["n_trials"] == 2
    assert insertions_df.iloc[0]["n_included_trials"] == 1

    assert list(units_df["cluster_id"]) == [0]
    assert list(units_df["channels"]) == [0]
    assert list(units_df["beryl_acronym"]) == ["VISp"]

    assert len(trials_df) == 2
    assert list(trials_df["trial_id"]) == [0, 1]
    assert list(channels_df["channel_id"]) == [0, 1]
    assert list(channels_df["beryl_acronym"]) == ["VISp", "MOs"]

    assert metadata["dataset_name"] == "bwm_simple"
    assert metadata["dataset_version"] == "1.0.0"
    assert build_report["build_completeness_status"] == "complete"
    assert build_report["row_counts"]["insertions"] == 1
    assert build_report["row_counts"]["units"] == 1
    assert build_report["row_counts"]["trials"] == 2
    assert build_report["row_counts"]["channels"] == 2

def test_make_one_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IBL_AGENT_DATA_OFFLINE", "1")
    with pytest.raises(OfflineModeError):
        bwm_simple._make_one(tmp_path, mode="remote")
