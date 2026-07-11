from __future__ import annotations

from pathlib import Path
import tarfile

import numpy as np
import pandas as pd
import pytest
import yaml

from ibl_ai_agent.datasets import bwm_behavior, bwm_simple
from ibl_ai_agent.datasets.bwm_behavior_upgrade import TARGET_DATASET_VERSION, upgrade_bwm_behavior_dataset_compression
from ibl_ai_agent.datasets.bwm_behavior_compression import (
    ProfileConfig,
    FeatureValidationConfig,
    ValidationConfig,
    profile_bwm_behavior_compression,
    write_behavior_session_shard,
    validate_bwm_behavior_compression_features,
    validate_bwm_behavior_compression,
)


def test_detect_wheel_state_rows_uses_existing_ibl_detector(monkeypatch: pytest.MonkeyPatch) -> None:
    import ibllib.io.extractors.training_wheel as training_wheel

    def _fake_extract_wheel_moves(re_ts, re_pos, display=False):
        return {
            "intervals": np.asarray([[0.2, 0.5], [0.8, 1.1]], dtype=np.float64),
            "peakAmplitude": np.asarray([0.4, -0.3], dtype=np.float64),
            "peakVelocity_times": np.asarray([0.35, 0.95], dtype=np.float64),
        }

    monkeypatch.setattr(training_wheel, "extract_wheel_moves", _fake_extract_wheel_moves)

    movement_rows, quiescence_rows, session_summary = bwm_behavior._detect_wheel_state_rows(
        eid="eid-1",
        timestamps=np.asarray([0.0, 0.5, 1.0, 1.5], dtype=np.float64),
        position=np.asarray([0.0, 0.1, 0.2, 0.3], dtype=np.float64),
    )

    assert len(movement_rows) == 2
    assert movement_rows[0]["detector_name"] == bwm_behavior.WHEEL_STATE_DETECTOR_NAME
    assert movement_rows[0]["t_start"] == pytest.approx(0.2)
    assert movement_rows[1]["peak_velocity_time"] == pytest.approx(0.95)
    assert len(quiescence_rows) == 3
    assert quiescence_rows[0]["t_start"] == pytest.approx(0.0)
    assert quiescence_rows[2]["t_end"] == pytest.approx(1.5)
    assert session_summary["wheel_present"] is True
    assert session_summary["movement_epoch_count"] == 2
    assert session_summary["quiescence_epoch_count"] == 3
    assert session_summary["fraction_time_moving"] == pytest.approx((0.3 + 0.3) / 1.5)


def test_detect_wheel_state_rows_allows_duplicate_timestamps(monkeypatch: pytest.MonkeyPatch) -> None:
    import ibllib.io.extractors.training_wheel as training_wheel

    def _fake_extract_wheel_moves(re_ts, re_pos, display=False):
        assert np.all(np.diff(re_ts) >= 0)
        return {
            "intervals": np.asarray([[0.2, 0.5]], dtype=np.float64),
            "peakAmplitude": np.asarray([0.4], dtype=np.float64),
            "peakVelocity_times": np.asarray([0.35], dtype=np.float64),
        }

    monkeypatch.setattr(training_wheel, "extract_wheel_moves", _fake_extract_wheel_moves)

    movement_rows, quiescence_rows, session_summary = bwm_behavior._detect_wheel_state_rows(
        eid="eid-dup",
        timestamps=np.asarray([0.0, 0.5, 0.5, 1.0, 1.5], dtype=np.float64),
        position=np.asarray([0.0, 0.1, 0.1, 0.2, 0.3], dtype=np.float64),
    )

    assert len(movement_rows) == 1
    assert len(quiescence_rows) == 2
    assert session_summary["wheel_present"] is True
    assert session_summary["movement_epoch_count"] == 1


def _write_wheel_files(alf_root: Path) -> None:
    np.save(alf_root / "wheel.timestamps.npy", np.asarray([0.0, 0.5, 1.0], dtype=np.float64))
    np.save(alf_root / "wheel.position.npy", np.asarray([0.0, 1.0, 1.5], dtype=np.float32))


def _write_pose_files(alf_root: Path) -> None:
    np.save(alf_root / "leftCamera.times.npy", np.asarray([0.0, 0.5, 1.0], dtype=np.float64))
    # Both trackers are written so tests can exercise the prefer-Lightning-Pose,
    # fallback-to-DLC selection logic in ``_resolve_camera_trackers``.
    np.save(alf_root / "leftCamera.lightningPose.npy", np.asarray([[1.0, 2.0], [1.5, 2.5], [2.0, 3.0]], dtype=np.float32))
    np.save(alf_root / "leftCamera.dlc.npy", np.asarray([[1.0, 2.0], [1.5, 2.5], [2.0, 3.0]], dtype=np.float32))
    pd.DataFrame({"pupilDiameter": [1.0, 1.1, 1.2], "likelihood": [0.9, 0.8, 0.95]}).to_parquet(
        alf_root / "leftCamera.features.pqt", engine="pyarrow", compression="zstd", index=False
    )


@pytest.fixture(autouse=True)
def _offline_wheel_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Load synthetic wheel from local files so build tests run without ONE.

    Production ``_prepare_wheel_payload`` resolves dataset revisions via ONE;
    the synthetic fixtures write plain local ``.npy`` files, so this patches the
    loader with an offline equivalent that applies the same 100 Hz interpolation
    and Butterworth-filtered velocity as ``SessionLoader.load_wheel``.
    """
    from ibl_ai_agent.datasets import bwm_session_assets as sa

    def _loader(*, row: object, cache_root: Path) -> dict:
        from brainbox.io.one import interpolate_position, velocity_filtered

        alf = sa.resolve_session_alf_dir(cache_root, lab=str(row.lab), subject=str(row.subject), date=str(row.date), session_number=int(row.session_number))
        timestamps_path = sa.first_existing(alf, sa.WHEEL_TIMESTAMPS_CANDIDATES) if alf is not None else None
        position_path = sa.first_existing(alf, sa.WHEEL_POSITION_CANDIDATES) if alf is not None else None
        if timestamps_path is None or position_path is None:
            return {"status": "missing", "eid": str(row.eid)}
        raw_t = np.asarray(np.load(timestamps_path), dtype=np.float64)
        raw_p = np.asarray(np.load(position_path), dtype=np.float64)
        position, times = interpolate_position(raw_t, raw_p, freq=bwm_behavior.WHEEL_FS_HZ)
        velocity, _acceleration = velocity_filtered(np.asarray(position, dtype=np.float64), fs=bwm_behavior.WHEEL_FS_HZ, corner_frequency=20, order=8)
        return {
            "status": "ok",
            "eid": str(row.eid),
            "timestamps": np.asarray(times, dtype=np.float64),
            "position": np.asarray(position, dtype=np.float64),
            "velocity": np.asarray(velocity, dtype=np.float32),
            "fs": float(bwm_behavior.WHEEL_FS_HZ),
        }

    monkeypatch.setattr(bwm_behavior, "_prepare_wheel_payload", _loader)


@pytest.fixture(autouse=True)
def _offline_pose_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Load synthetic pose/motion-energy/pupil data from local files so build tests run without ONE.

    Production ``_prepare_pose_payload`` resolves per-camera tracker availability via
    ``one.list_datasets`` and loads data through ``SessionLoader``; the synthetic fixtures write
    plain local ALF-style files, so this patches the loader with an offline equivalent that
    prefers ``.lightningPose`` over ``.dlc`` per camera, mirroring ``_resolve_camera_trackers``.
    """
    from ibl_ai_agent.datasets import bwm_session_assets as sa

    def _loader(*, row: object, cache_root: Path) -> dict:
        alf = sa.resolve_session_alf_dir(cache_root, lab=str(row.lab), subject=str(row.subject), date=str(row.date), session_number=int(row.session_number))
        if alf is None:
            return {"status": "missing", "eid": str(row.eid)}
        cameras: dict = {}
        for camera_name in bwm_behavior.CAMERA_NAMES:
            stems = [camera_name, f"_ibl_{camera_name}"]
            times_path = sa.find_camera_file(alf, stems, [".times.npy"])
            if times_path is None:
                continue
            timestamps = np.asarray(np.load(times_path), dtype=np.float64)
            lp_path = sa.find_camera_file(alf, stems, [".lightningPose.npy", ".lightningPose.pqt"])
            dlc_path = sa.find_camera_file(alf, stems, [".dlc.npy", ".dlc.pqt"])
            if lp_path is not None:
                tracker, pose_path = "lightningPose", lp_path
            elif dlc_path is not None:
                tracker, pose_path = "dlc", dlc_path
            else:
                continue
            pose_matrix = np.asarray(np.load(pose_path), dtype=np.float32)
            matrices = [pose_matrix]
            columns = [f"bodypart{idx}" for idx in range(pose_matrix.shape[1])]
            skipped_sources: list[str] = []
            features_path = sa.find_camera_file(alf, stems, [".features.pqt"])
            if features_path is not None:
                feats = pd.read_parquet(features_path)
                if len(feats) == timestamps.shape[0]:
                    matrices.append(feats.to_numpy(dtype=np.float32))
                    columns.extend(list(feats.columns))
                else:
                    skipped_sources.append("pupil" if camera_name == "leftCamera" else "motion_energy")
            features = np.concatenate(matrices, axis=1).astype(np.float32, copy=False)
            cameras[camera_name] = {
                "timestamps": timestamps,
                "features": features,
                "columns": columns,
                "tracker": tracker,
                "skipped_sources": skipped_sources,
            }
        if not cameras:
            return {"status": "missing", "eid": str(row.eid)}
        return {"status": "ok", "eid": str(row.eid), "cameras": cameras}

    monkeypatch.setattr(bwm_behavior, "_prepare_pose_payload", _loader)


def test_refresh_bwm_behavior_features_reuses_existing_session_shards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("numcodecs")

    roster = pd.DataFrame(
        [{
            "pid": "pid-1",
            "eid": "eid-1",
            "probe_name": "probe00",
            "session_number": 1,
            "date": "2020-01-01",
            "subject": "SUBJ_1",
            "lab": "lab_a",
        }]
    )
    monkeypatch.setattr(bwm_simple, "bwm_query", lambda freeze: roster.copy())

    cache_root = tmp_path / "one-cache"
    bwm_tables = cache_root / "openalyx.internationalbrainlab.org" / "bwm_tables"
    bwm_tables.mkdir(parents=True, exist_ok=True)

    trials = pd.DataFrame(
        [{
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
        }]
    )
    trials.to_parquet(bwm_tables / "trials.pqt", engine="pyarrow", compression="zstd", index=False)

    alf_root = (
        cache_root / "openalyx.internationalbrainlab.org" / "lab_a" / "Subjects" / "SUBJ_1" / "2020-01-01" / "001" / "alf"
    )
    alf_root.mkdir(parents=True, exist_ok=True)
    _write_wheel_files(alf_root)
    _write_pose_files(alf_root)

    outputs = bwm_behavior.build_bwm_behavior_dataset(
        bwm_behavior.BuildConfig(output_root=tmp_path / "out", cache_root=cache_root, allow_remote_fetch=False, jobs=1)
    )

    shard_path = outputs.pose_store_path / "eid-1.zip"
    before_mtime = shard_path.stat().st_mtime_ns
    refreshed = bwm_behavior.refresh_bwm_behavior_features(dataset_dir=outputs.dataset_dir, cache_root=cache_root, verbose=False)
    assert refreshed.dataset_dir == outputs.dataset_dir
    assert shard_path.stat().st_mtime_ns == before_mtime
    assert outputs.event_aligned_behavior_features_path.exists()
    assert (outputs.dataset_dir / "feature_refresh_report.yaml").exists()


def test_build_bwm_behavior_dataset_small_synthetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    cache_root = tmp_path / "one-cache"
    bwm_tables = cache_root / "openalyx.internationalbrainlab.org" / "bwm_tables"
    bwm_tables.mkdir(parents=True, exist_ok=True)

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
    alf_root.mkdir(parents=True, exist_ok=True)
    _write_wheel_files(alf_root)
    _write_pose_files(alf_root)

    outputs = bwm_behavior.build_bwm_behavior_dataset(
        bwm_behavior.BuildConfig(
            output_root=tmp_path / "out",
            cache_root=cache_root,
            allow_remote_fetch=False,
            jobs=2,
        )
    )

    assert outputs.dataset_dir.exists()
    assert outputs.trials_path.exists()
    assert outputs.events_path.exists()
    assert outputs.wheel_availability_path.exists()
    assert outputs.pose_availability_path.exists()
    assert outputs.trial_behavior_features_path.exists()
    assert outputs.wheel_trial_features_path.exists()
    assert outputs.pose_trial_features_path.exists()
    assert outputs.event_aligned_behavior_features_path.exists()
    assert outputs.behavior_session_features_path.exists()
    assert outputs.movement_state_epochs_path.exists()
    assert outputs.quiescence_state_epochs_path.exists()
    assert outputs.behavior_state_session_features_path.exists()
    assert outputs.wheel_store_path.exists()
    assert outputs.pose_store_path.exists()
    assert (outputs.pose_store_path / "eid-1.zip").exists()

    provenance = yaml.safe_load(outputs.provenance_path.read_text(encoding="utf-8"))
    assert provenance["storage"]["pose_float_dtype"] == "float32"

    wheel_availability = pd.read_parquet(outputs.wheel_availability_path)
    assert wheel_availability.loc[0, 'wheel_present']
    trial_behavior_features = pd.read_parquet(outputs.trial_behavior_features_path)
    assert set(['signed_contrast', 'choice_label', 'reaction_time', 'movement_time']).issubset(trial_behavior_features.columns)
    wheel_trial_features = pd.read_parquet(outputs.wheel_trial_features_path)
    assert set(['movement_direction', 'movement_amplitude', 'mean_velocity', 'max_velocity']).issubset(wheel_trial_features.columns)
    pose_trial_features = pd.read_parquet(outputs.pose_trial_features_path)
    assert set(['camera', 'feature_mean', 'feature_peak']).issubset(pose_trial_features.columns)
    assert 'leftCamera' in set(pose_trial_features['camera'].astype(str))
    pose_availability = pd.read_parquet(outputs.pose_availability_path)
    assert 'tracker' in pose_availability.columns
    left_row = pose_availability.loc[pose_availability['camera'] == 'leftCamera'].iloc[0]
    assert left_row['tracker'] == 'lightningPose'
    event_aligned_behavior_features = pd.read_parquet(outputs.event_aligned_behavior_features_path)
    assert set(['signal_name', 'event_name', 'baseline', 'peak', 'peak_latency_ms', 'modulation_index']).issubset(event_aligned_behavior_features.columns)
    assert 'wheel' in set(event_aligned_behavior_features['signal_name'].astype(str))
    assert 'stimOn' in set(event_aligned_behavior_features['event_name'].astype(str))
    behavior_session_features = pd.read_parquet(outputs.behavior_session_features_path)
    assert set(['performance', 'median_reaction_time', 'wheel_present', 'pose_present']).issubset(behavior_session_features.columns)
    movement_state_epochs = pd.read_parquet(outputs.movement_state_epochs_path)
    assert set(['movement_id', 't_start', 't_end', 'duration_s', 'detector_name']).issubset(movement_state_epochs.columns)
    quiescence_state_epochs = pd.read_parquet(outputs.quiescence_state_epochs_path)
    assert set(['quiescence_id', 't_start', 't_end', 'duration_s', 'derived_from']).issubset(quiescence_state_epochs.columns)
    behavior_state_session_features = pd.read_parquet(outputs.behavior_state_session_features_path)
    assert set(['wheel_present', 'movement_epoch_count', 'quiescence_epoch_count', 'fraction_time_moving']).issubset(behavior_state_session_features.columns)

    shard = bwm_behavior.load_behavior_session_shard(outputs.pose_store_path / "eid-1.zip")
    assert shard["leftCamera.features"].dtype == np.float32
    assert shard["meta"]["cameras"]["leftCamera"]["columns"]
    assert shard["meta"]["cameras"]["leftCamera"]["tracker"] == "lightningPose"


def test_upgrade_bwm_behavior_dataset_writes_release_tar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    cache_root = tmp_path / "one-cache"
    bwm_tables = cache_root / "openalyx.internationalbrainlab.org" / "bwm_tables"
    bwm_tables.mkdir(parents=True, exist_ok=True)

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
    alf_root.mkdir(parents=True, exist_ok=True)
    _write_wheel_files(alf_root)
    _write_pose_files(alf_root)

    base_outputs = bwm_behavior.build_bwm_behavior_dataset(
        bwm_behavior.BuildConfig(output_root=tmp_path / "out", cache_root=cache_root, allow_remote_fetch=False, jobs=1)
    )
    release_root = tmp_path / "releases"
    upgraded = upgrade_bwm_behavior_dataset_compression(
        source_dataset_dir=base_outputs.dataset_dir,
        output_root=tmp_path / "out",
        jobs=1,
        resume=False,
        verbose=False,
        release_root=release_root,
    )

    assert upgraded.archive_path is not None
    assert upgraded.archive_checksum_path is not None
    assert upgraded.archive_path.exists()
    assert upgraded.archive_checksum_path.exists()
    assert upgraded.archive_path.parent == release_root / "bwm_behavior" / TARGET_DATASET_VERSION

    with tarfile.open(upgraded.archive_path, mode="r") as tar:
        members = tar.getnames()
    assert f"bwm_behavior/{TARGET_DATASET_VERSION}/metadata/sessions.parquet" in members
    assert all(".feature-refresh-cache" not in name for name in members)


def test_write_behavior_session_shards_resumes_without_rewriting(tmp_path: Path) -> None:
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
    cache_root = tmp_path / "one-cache"
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
    alf_root.mkdir(parents=True, exist_ok=True)
    _write_wheel_files(alf_root)
    _write_pose_files(alf_root)

    shard_dir = tmp_path / "sessions"
    first = bwm_behavior._write_behavior_session_shards(
        shard_dir,
        roster=roster,
        cache_root=cache_root,
        jobs=1,
        verbose=False,
        resume=True,
    )
    shard_path = shard_dir / "eid-1.zip"
    before_mtime = shard_path.stat().st_mtime_ns

    second = bwm_behavior._write_behavior_session_shards(
        shard_dir,
        roster=roster,
        cache_root=cache_root,
        jobs=1,
        verbose=False,
        resume=True,
    )

    assert first["sessions_written"] == 1
    assert second["sessions_written"] == 1
    assert second["sessions_skipped"] == 1
    assert shard_path.stat().st_mtime_ns == before_mtime


def test_ensure_bwm_behavior_dataset_restores_missing_state_tables_from_shards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    cache_root = tmp_path / "one-cache"
    bwm_tables = cache_root / "openalyx.internationalbrainlab.org" / "bwm_tables"
    bwm_tables.mkdir(parents=True, exist_ok=True)
    trials = pd.DataFrame(
        [{
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
        }]
    )
    trials.to_parquet(bwm_tables / "trials.pqt", engine="pyarrow", compression="zstd", index=False)

    alf_root = (
        cache_root / "openalyx.internationalbrainlab.org" / "lab_a" / "Subjects" / "SUBJ_1" / "2020-01-01" / "001" / "alf"
    )
    alf_root.mkdir(parents=True, exist_ok=True)
    _write_wheel_files(alf_root)
    _write_pose_files(alf_root)

    outputs = bwm_behavior.build_bwm_behavior_dataset(
        bwm_behavior.BuildConfig(output_root=tmp_path / "out", cache_root=cache_root, allow_remote_fetch=False, jobs=1)
    )
    outputs.movement_state_epochs_path.unlink()
    outputs.quiescence_state_epochs_path.unlink()
    outputs.behavior_state_session_features_path.unlink()

    before = bwm_behavior.inspect_bwm_behavior_dataset(dataset_dir=outputs.dataset_dir)
    assert set(before["missing_derived_tables"]) == {"movement_state_epochs", "quiescence_state_epochs", "behavior_state_session_features"}

    _, after = bwm_behavior.ensure_bwm_behavior_dataset(dataset_dir=outputs.dataset_dir, jobs=1, verbose=False)

    assert outputs.movement_state_epochs_path.exists()
    assert outputs.quiescence_state_epochs_path.exists()
    assert outputs.behavior_state_session_features_path.exists()


def test_inspect_bwm_behavior_dataset_flags_stale_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    cache_root = tmp_path / "one-cache"
    bwm_tables = cache_root / "openalyx.internationalbrainlab.org" / "bwm_tables"
    bwm_tables.mkdir(parents=True, exist_ok=True)
    trials = pd.DataFrame(
        [{
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
        }]
    )
    trials.to_parquet(bwm_tables / "trials.pqt", engine="pyarrow", compression="zstd", index=False)

    alf_root = (
        cache_root / "openalyx.internationalbrainlab.org" / "lab_a" / "Subjects" / "SUBJ_1" / "2020-01-01" / "001" / "alf"
    )
    alf_root.mkdir(parents=True, exist_ok=True)
    _write_wheel_files(alf_root)
    _write_pose_files(alf_root)

    outputs = bwm_behavior.build_bwm_behavior_dataset(
        bwm_behavior.BuildConfig(output_root=tmp_path / "out", cache_root=cache_root, allow_remote_fetch=False, jobs=1)
    )

    stale_schema = yaml.safe_load(outputs.schema_path.read_text(encoding="utf-8"))
    stale_schema["dataset_version"] = "0.0.0"
    stale_schema["schema_version"] = -1
    outputs.schema_path.write_text(yaml.safe_dump(stale_schema, sort_keys=False), encoding="utf-8")

    report = bwm_behavior.inspect_bwm_behavior_dataset(dataset_dir=outputs.dataset_dir)

    assert report["schema_dataset_version_matches"] is False
    assert report["schema_version_matches"] is False
    assert report["recommended_action"] == "refresh_sidecars_only"


def test_ensure_bwm_behavior_dataset_dry_run_does_not_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    cache_root = tmp_path / "one-cache"
    bwm_tables = cache_root / "openalyx.internationalbrainlab.org" / "bwm_tables"
    bwm_tables.mkdir(parents=True, exist_ok=True)
    trials = pd.DataFrame(
        [{
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
        }]
    )
    trials.to_parquet(bwm_tables / "trials.pqt", engine="pyarrow", compression="zstd", index=False)

    alf_root = (
        cache_root / "openalyx.internationalbrainlab.org" / "lab_a" / "Subjects" / "SUBJ_1" / "2020-01-01" / "001" / "alf"
    )
    alf_root.mkdir(parents=True, exist_ok=True)
    _write_wheel_files(alf_root)
    _write_pose_files(alf_root)

    outputs = bwm_behavior.build_bwm_behavior_dataset(
        bwm_behavior.BuildConfig(output_root=tmp_path / "out", cache_root=cache_root, allow_remote_fetch=False, jobs=1)
    )
    outputs.movement_state_epochs_path.unlink()

    _, report = bwm_behavior.ensure_bwm_behavior_dataset(
        dataset_dir=outputs.dataset_dir,
        jobs=1,
        verbose=False,
        dry_run=True,
    )

    assert not outputs.movement_state_epochs_path.exists()
    assert "movement_state_epochs" in report["missing_derived_tables"]


def test_build_bwm_behavior_dataset_partial_finalize_with_missing_pose(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    cache_root = tmp_path / "one-cache"
    bwm_tables = cache_root / "openalyx.internationalbrainlab.org" / "bwm_tables"
    bwm_tables.mkdir(parents=True, exist_ok=True)

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
    alf_root.mkdir(parents=True, exist_ok=True)
    _write_wheel_files(alf_root)

    outputs = bwm_behavior.build_bwm_behavior_dataset(
        bwm_behavior.BuildConfig(
            output_root=tmp_path / "out",
            cache_root=cache_root,
            allow_remote_fetch=False,
            require_signals=False,
            jobs=1,
        )
    )

    assert outputs.dataset_dir.exists()
    build_report = yaml.safe_load(outputs.build_report_path.read_text(encoding="utf-8"))
    assert build_report["release_status"] == "partial"
    assert build_report["prefetch"]["partial_build"] is True
    summary = outputs.summary_path.read_text(encoding="utf-8")
    assert "- Release status: `partial`" in summary
    assert "- Partial build: `True`" in summary


def test_build_bwm_behavior_dataset_strict_mode_raises_on_missing_pose(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    cache_root = tmp_path / "one-cache"
    bwm_tables = cache_root / "openalyx.internationalbrainlab.org" / "bwm_tables"
    bwm_tables.mkdir(parents=True, exist_ok=True)

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
    alf_root.mkdir(parents=True, exist_ok=True)
    _write_wheel_files(alf_root)

    with pytest.raises(bwm_behavior.BuildError, match="Strict mode is enabled"):
        bwm_behavior.build_bwm_behavior_dataset(
            bwm_behavior.BuildConfig(
                output_root=tmp_path / "out",
                cache_root=cache_root,
                allow_remote_fetch=False,
                require_signals=True,
                jobs=1,
            )
        )


def test_bwm_behavior_compression_profile_writes_reports(tmp_path: Path) -> None:
    pytest.importorskip("numcodecs")

    dataset_dir = tmp_path / "bwm_behavior" / "1.0.0"
    sessions_dir = dataset_dir / "sessions"
    sessions_dir.mkdir(parents=True)
    rng = np.random.default_rng(123)
    frames = 200
    columns = [
        "_ibl_leftCamera_pose__nose_tip_x",
        "_ibl_leftCamera_pose__nose_tip_y",
        "_ibl_leftCamera_pose__nose_tip_likelihood",
        "_ibl_leftCamera_features__pupilDiameter_raw",
    ]
    arrays = {
        "leftCamera.timestamps": np.arange(frames, dtype=np.float64) / 60.0,
        "leftCamera.features": np.column_stack(
            [
                320.0 + rng.normal(0.0, 3.0, size=frames),
                240.0 + rng.normal(0.0, 3.0, size=frames),
                np.clip(rng.normal(0.95, 0.02, size=frames), 0.0, 1.0),
                40.0 + rng.normal(0.0, 0.3, size=frames),
            ]
        ).astype(np.float32),
    }
    bwm_behavior.bwm_shared.write_array_shard(
        sessions_dir / "eid-1.zip",
        metadata={
            "format": "ibl_ai_agent_behavior_session_shard_v1",
            "dataset_name": "bwm_behavior",
            "dataset_version": "1.0.0",
            "eid": "eid-1",
            "compression": {"name": "blosc_zstd_shuffle"},
            "wheel": {"present": False},
            "cameras": {"leftCamera": {"n_frames": frames, "n_features": len(columns), "columns": columns}},
        },
        arrays=arrays,
    )

    outputs = profile_bwm_behavior_compression(
        ProfileConfig(
            dataset_dir=dataset_dir,
            output_root=tmp_path / "profiles",
            max_shards=1,
            strategy_names=(
                "lossless-baseline",
                "conservative",
                "balanced-pose-delta",
                "aggressive-pose-delta-wheel-native-left60-right60-body30",
                "aggressive-pose-delta-wheel100-pose50",
                "compact-pose-delta",
                "aggressive-pose-delta-30hz",
            ),
            verbose=False,
        )
    )

    assert outputs.summary_path.exists()
    assert outputs.strategy_summary_path.exists()
    summary = pd.read_csv(outputs.strategy_summary_path)
    assert set(summary["strategy"]) == {
        "lossless-baseline",
        "conservative",
        "balanced-pose-delta",
        "aggressive-pose-delta-wheel-native-left60-right60-body30",
        "aggressive-pose-delta-wheel100-pose50",
        "compact-pose-delta",
        "aggressive-pose-delta-30hz",
    }
    assert "compression_factor_vs_current" in summary.columns
    assert outputs.summary_path.read_text(encoding="utf-8").startswith("# BWM behavior compression profile")


def test_bwm_behavior_compression_validation_writes_reports(tmp_path: Path) -> None:
    pytest.importorskip("numcodecs")

    dataset_dir = tmp_path / "bwm_behavior" / "1.0.0"
    sessions_dir = dataset_dir / "sessions"
    sessions_dir.mkdir(parents=True)
    frames = 120
    columns = [
        "_ibl_leftCamera_pose__nose_tip_x",
        "_ibl_leftCamera_pose__nose_tip_y",
        "_ibl_leftCamera_pose__nose_tip_likelihood",
    ]
    arrays = {
        "wheel.timestamps": np.arange(frames * 10, dtype=np.float64) / 300.0,
        "wheel.position": np.linspace(0.0, 1.0, frames * 10, dtype=np.float32),
        "wheel.velocity": np.full(frames * 10, 0.2, dtype=np.float32),
        "leftCamera.timestamps": np.arange(frames, dtype=np.float64) / 60.0,
        "leftCamera.features": np.column_stack(
            [
                np.linspace(320.0, 330.0, frames),
                np.linspace(240.0, 250.0, frames),
                np.linspace(0.8, 1.0, frames),
            ]
        ).astype(np.float32),
    }
    bwm_behavior.bwm_shared.write_array_shard(
        sessions_dir / "eid-1.zip",
        metadata={
            "format": "ibl_ai_agent_behavior_session_shard_v1",
            "dataset_name": "bwm_behavior",
            "dataset_version": "1.0.0",
            "eid": "eid-1",
            "compression": {"name": "blosc_zstd_shuffle"},
            "wheel": {"present": True},
            "cameras": {"leftCamera": {"n_frames": frames, "n_features": len(columns), "columns": columns}},
        },
        arrays=arrays,
    )

    outputs = validate_bwm_behavior_compression(
        ValidationConfig(
            dataset_dir=dataset_dir,
            output_root=tmp_path / "profiles",
            max_shards=1,
            strategy_name="aggressive-pose-delta-30hz",
            verbose=False,
        )
    )

    assert outputs.summary_path.exists()
    assert outputs.array_validation_path.exists()
    validation = pd.read_csv(outputs.array_validation_path)
    assert {"array", "retained_row_ratio", "max_abs_error"}.issubset(validation.columns)
    assert validation["retained_row_ratio"].min() < 1.0
    assert outputs.summary_path.read_text(encoding="utf-8").startswith("# BWM behavior compression validation")


def test_bwm_behavior_compression_feature_validation_writes_reports(tmp_path: Path) -> None:
    pytest.importorskip("numcodecs")

    dataset_dir = tmp_path / "bwm_behavior" / "1.0.0"
    sessions_dir = dataset_dir / "sessions"
    metadata_dir = dataset_dir / "metadata"
    sessions_dir.mkdir(parents=True)
    metadata_dir.mkdir(parents=True)
    frames = 120
    wheel_frames = 1200
    columns = [
        "_ibl_leftCamera_pose__nose_tip_x",
        "_ibl_leftCamera_pose__nose_tip_y",
        "_ibl_leftCamera_pose__nose_tip_likelihood",
        "_ibl_leftCamera_features__pupilDiameter_raw",
    ]
    trials = pd.DataFrame(
        [
            {
                "eid": "eid-1",
                "trial_id": 0,
                "stimOn_times": 0.20,
                "goCue_times": 0.25,
                "firstMovement_times": 0.30,
                "response_times": 0.55,
                "feedback_times": 0.60,
            },
            {
                "eid": "eid-1",
                "trial_id": 1,
                "stimOn_times": 0.85,
                "goCue_times": 0.90,
                "firstMovement_times": 0.95,
                "response_times": 1.20,
                "feedback_times": 1.25,
            },
        ]
    )
    trials.to_parquet(metadata_dir / "trials.parquet", engine="pyarrow", compression="zstd", index=False)
    camera_times = np.arange(frames, dtype=np.float64) / 60.0
    arrays = {
        "wheel.timestamps": np.arange(wheel_frames, dtype=np.float64) / 300.0,
        "wheel.position": np.linspace(0.0, 2.0, wheel_frames, dtype=np.float32),
        "wheel.velocity": np.full(wheel_frames, 0.5, dtype=np.float32),
        "leftCamera.timestamps": camera_times,
        "leftCamera.features": np.column_stack(
            [
                320.0 + camera_times,
                240.0 + camera_times,
                np.linspace(0.8, 1.0, frames),
                40.0 + 0.2 * camera_times,
            ]
        ).astype(np.float32),
    }
    bwm_behavior.bwm_shared.write_array_shard(
        sessions_dir / "eid-1.zip",
        metadata={
            "format": "ibl_ai_agent_behavior_session_shard_v1",
            "dataset_name": "bwm_behavior",
            "dataset_version": "1.0.0",
            "eid": "eid-1",
            "compression": {"name": "blosc_zstd_shuffle"},
            "wheel": {"present": True},
            "cameras": {"leftCamera": {"n_frames": frames, "n_features": len(columns), "columns": columns}},
        },
        arrays=arrays,
    )

    outputs = validate_bwm_behavior_compression_features(
        FeatureValidationConfig(
            dataset_dir=dataset_dir,
            output_root=tmp_path / "profiles",
            max_shards=1,
            strategy_name="aggressive-pose-delta-30hz",
            verbose=False,
        )
    )

    assert outputs.summary_path.exists()
    assert outputs.feature_validation_path.exists()
    assert outputs.row_validation_path.exists()
    rows = pd.read_csv(outputs.row_validation_path)
    features = pd.read_csv(outputs.feature_validation_path)
    assert {"table", "source_rows", "candidate_rows", "paired_rows"}.issubset(rows.columns)
    assert {"table", "column", "kind", "p95_abs_error"}.issubset(features.columns)
    assert "wheel_trial_features" in set(rows["table"])
    assert outputs.summary_path.read_text(encoding="utf-8").startswith("# BWM behavior compression feature validation")


def test_behavior_compressed_session_shard_roundtrip(tmp_path: Path) -> None:
    pytest.importorskip("numcodecs")

    path = tmp_path / "eid-1.zip"
    frames = 120
    wheel_frames = 300
    arrays = {
        "wheel.timestamps": np.arange(wheel_frames, dtype=np.float64) / 1000.0,
        "wheel.position": np.linspace(0.0, 1.0, wheel_frames, dtype=np.float32),
        "wheel.velocity": np.full(wheel_frames, 0.25, dtype=np.float32),
        "leftCamera.timestamps": np.arange(frames, dtype=np.float64) / 60.0,
        "leftCamera.features": np.column_stack(
            [
                np.linspace(320.0, 321.0, frames),
                np.linspace(240.0, 241.0, frames),
                np.linspace(0.8, 1.0, frames),
                np.linspace(40.0, 40.5, frames),
            ]
        ).astype(np.float32),
        "rightCamera.timestamps": np.arange(frames * 3, dtype=np.float64) / 150.0,
        "rightCamera.features": np.column_stack(
            [
                np.linspace(10.0, 15.0, frames * 3),
                np.linspace(20.0, 25.0, frames * 3),
                np.linspace(0.7, 0.95, frames * 3),
                np.linspace(5.0, 6.0, frames * 3),
            ]
        ).astype(np.float32),
    }
    metadata = {
        "format": "ibl_ai_agent_behavior_session_shard_v1",
        "dataset_name": "bwm_behavior",
        "dataset_version": "1.0.0",
        "eid": "eid-1",
        "compression": {"name": "blosc_zstd_shuffle"},
        "wheel": {"present": True, "has_velocity": True},
        "cameras": {
            "leftCamera": {"n_frames": frames, "n_features": 4, "columns": ["nose_x", "nose_y", "nose_likelihood", "pupil"]},
            "rightCamera": {"n_frames": frames * 3, "n_features": 4, "columns": ["paw_x", "paw_y", "paw_likelihood", "motion"]},
        },
    }
    write_behavior_session_shard(
        path,
        metadata=metadata,
        arrays=arrays,
        strategy_name="aggressive-pose-delta-wheel-native-left60-right60-body30",
    )

    shard = bwm_behavior.load_behavior_session_shard(path)
    assert shard["meta"]["format"] == "ibl_ai_agent_behavior_session_shard_v2"
    assert shard["wheel.position"].dtype == np.float32
    assert shard["leftCamera.features"].shape == arrays["leftCamera.features"].shape
    assert shard["rightCamera.features"].shape[0] < arrays["rightCamera.features"].shape[0]
    assert shard["wheel.timestamps"].shape == arrays["wheel.timestamps"].shape


def test_upgrade_bwm_behavior_dataset_compression_writes_v11(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("numcodecs")

    roster = pd.DataFrame(
        [{
            "pid": "pid-1",
            "eid": "eid-1",
            "probe_name": "probe00",
            "session_number": 1,
            "date": "2020-01-01",
            "subject": "SUBJ_1",
            "lab": "lab_a",
        }]
    )
    monkeypatch.setattr(bwm_simple, "bwm_query", lambda freeze: roster.copy())

    cache_root = tmp_path / "one-cache"
    bwm_tables = cache_root / "openalyx.internationalbrainlab.org" / "bwm_tables"
    bwm_tables.mkdir(parents=True, exist_ok=True)
    trials = pd.DataFrame(
        [{
            "eid": "eid-1",
            "trial_id": 0,
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
        }]
    )
    trials.to_parquet(bwm_tables / "trials.pqt", engine="pyarrow", compression="zstd", index=False)
    alf_root = cache_root / "openalyx.internationalbrainlab.org" / "lab_a" / "Subjects" / "SUBJ_1" / "2020-01-01" / "001" / "alf"
    alf_root.mkdir(parents=True, exist_ok=True)
    _write_wheel_files(alf_root)
    _write_pose_files(alf_root)

    source = bwm_behavior.build_bwm_behavior_dataset(
        bwm_behavior.BuildConfig(output_root=tmp_path / "out", cache_root=cache_root, allow_remote_fetch=False, jobs=1, verbose=False)
    )
    upgraded = upgrade_bwm_behavior_dataset_compression(
        source_dataset_dir=source.dataset_dir,
        output_root=tmp_path / "upgraded",
        resume=False,
        verbose=False,
    )

    assert upgraded.dataset_dir.name == TARGET_DATASET_VERSION
    shard = bwm_behavior.load_behavior_session_shard(upgraded.dataset_dir / "sessions" / "eid-1.zip")
    assert shard["meta"]["format"] == "ibl_ai_agent_behavior_session_shard_v2"
    assert shard["meta"]["compression"]["profile"] == "aggressive-pose-delta-wheel-native-left60-right60-body30"
    assert upgraded.schema_path.exists()
    assert upgraded.provenance_path.exists()


def test_upgrade_bwm_behavior_dataset_compression_resume_completed_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("numcodecs")

    roster = pd.DataFrame(
        [{
            "pid": "pid-1",
            "eid": "eid-1",
            "probe_name": "probe00",
            "session_number": 1,
            "date": "2020-01-01",
            "subject": "SUBJ_1",
            "lab": "lab_a",
        }]
    )
    monkeypatch.setattr(bwm_simple, "bwm_query", lambda freeze: roster.copy())

    cache_root = tmp_path / "one-cache"
    bwm_tables = cache_root / "openalyx.internationalbrainlab.org" / "bwm_tables"
    bwm_tables.mkdir(parents=True, exist_ok=True)
    trials = pd.DataFrame(
        [{
            "eid": "eid-1",
            "trial_id": 0,
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
        }]
    )
    trials.to_parquet(bwm_tables / "trials.pqt", engine="pyarrow", compression="zstd", index=False)
    alf_root = cache_root / "openalyx.internationalbrainlab.org" / "lab_a" / "Subjects" / "SUBJ_1" / "2020-01-01" / "001" / "alf"
    alf_root.mkdir(parents=True, exist_ok=True)
    _write_wheel_files(alf_root)
    _write_pose_files(alf_root)

    source = bwm_behavior.build_bwm_behavior_dataset(
        bwm_behavior.BuildConfig(output_root=tmp_path / "out", cache_root=cache_root, allow_remote_fetch=False, jobs=1, verbose=False)
    )
    upgraded = upgrade_bwm_behavior_dataset_compression(
        source_dataset_dir=source.dataset_dir,
        output_root=tmp_path / "upgraded",
        jobs=1,
        resume=False,
        verbose=False,
    )
    shard_path = upgraded.dataset_dir / "sessions" / "eid-1.zip"
    before_mtime = shard_path.stat().st_mtime_ns
    resumed = upgrade_bwm_behavior_dataset_compression(
        source_dataset_dir=source.dataset_dir,
        output_root=tmp_path / "upgraded",
        jobs=1,
        resume=True,
        verbose=False,
    )
    assert resumed.dataset_dir == upgraded.dataset_dir
    assert shard_path.stat().st_mtime_ns == before_mtime


def test_inspect_bwm_behavior_dataset_detects_upgrade_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("numcodecs")

    roster = pd.DataFrame(
        [{
            "pid": "pid-1",
            "eid": "eid-1",
            "probe_name": "probe00",
            "session_number": 1,
            "date": "2020-01-01",
            "subject": "SUBJ_1",
            "lab": "lab_a",
        }]
    )
    monkeypatch.setattr(bwm_simple, "bwm_query", lambda freeze: roster.copy())

    cache_root = tmp_path / "one-cache"
    bwm_tables = cache_root / "openalyx.internationalbrainlab.org" / "bwm_tables"
    bwm_tables.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [{
            "eid": "eid-1",
            "trial_id": 0,
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
        }]
    ).to_parquet(bwm_tables / "trials.pqt", engine="pyarrow", compression="zstd", index=False)
    alf_root = cache_root / "openalyx.internationalbrainlab.org" / "lab_a" / "Subjects" / "SUBJ_1" / "2020-01-01" / "001" / "alf"
    alf_root.mkdir(parents=True, exist_ok=True)
    _write_wheel_files(alf_root)
    _write_pose_files(alf_root)

    source = bwm_behavior.build_bwm_behavior_dataset(
        bwm_behavior.BuildConfig(output_root=tmp_path / "out", cache_root=cache_root, allow_remote_fetch=False, jobs=1, verbose=False)
    )
    upgraded = upgrade_bwm_behavior_dataset_compression(
        source_dataset_dir=source.dataset_dir,
        output_root=tmp_path / "upgraded",
        jobs=1,
        resume=False,
        verbose=False,
    )

    report = bwm_behavior.inspect_bwm_behavior_dataset(dataset_dir=upgraded.dataset_dir)

    assert report["dataset_kind"] == "upgrade_v1_1"
    assert report["expected_dataset_version"] == TARGET_DATASET_VERSION
    assert report["expected_schema_version"] == 3
    assert report["schema_dataset_version_matches"] is True
    assert report["schema_version_matches"] is True
    assert report["recommended_action"] == "none"


def test_refresh_bwm_behavior_features_from_shards_preserves_upgrade_sidecars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("numcodecs")

    roster = pd.DataFrame(
        [{
            "pid": "pid-1",
            "eid": "eid-1",
            "probe_name": "probe00",
            "session_number": 1,
            "date": "2020-01-01",
            "subject": "SUBJ_1",
            "lab": "lab_a",
        }]
    )
    monkeypatch.setattr(bwm_simple, "bwm_query", lambda freeze: roster.copy())

    cache_root = tmp_path / "one-cache"
    bwm_tables = cache_root / "openalyx.internationalbrainlab.org" / "bwm_tables"
    bwm_tables.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [{
            "eid": "eid-1",
            "trial_id": 0,
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
        }]
    ).to_parquet(bwm_tables / "trials.pqt", engine="pyarrow", compression="zstd", index=False)
    alf_root = cache_root / "openalyx.internationalbrainlab.org" / "lab_a" / "Subjects" / "SUBJ_1" / "2020-01-01" / "001" / "alf"
    alf_root.mkdir(parents=True, exist_ok=True)
    _write_wheel_files(alf_root)
    _write_pose_files(alf_root)

    source = bwm_behavior.build_bwm_behavior_dataset(
        bwm_behavior.BuildConfig(output_root=tmp_path / "out", cache_root=cache_root, allow_remote_fetch=False, jobs=1, verbose=False)
    )
    upgraded = upgrade_bwm_behavior_dataset_compression(
        source_dataset_dir=source.dataset_dir,
        output_root=tmp_path / "upgraded",
        jobs=1,
        resume=False,
        verbose=False,
    )

    bwm_behavior.refresh_bwm_behavior_features_from_shards(
        dataset_dir=upgraded.dataset_dir,
        jobs=1,
        verbose=False,
    )

    schema = yaml.safe_load(upgraded.schema_path.read_text(encoding="utf-8"))
    manifest = yaml.safe_load(upgraded.manifest_path.read_text(encoding="utf-8"))
    build_report = yaml.safe_load(upgraded.build_report_path.read_text(encoding="utf-8"))

    assert schema["dataset_version"] == TARGET_DATASET_VERSION
    assert schema["schema_version"] == 3
    assert schema["compression_profile"] == "aggressive-pose-delta-wheel-native-left60-right60-body30"
    assert manifest["dataset_version"] == TARGET_DATASET_VERSION
    assert build_report["dataset_version"] == TARGET_DATASET_VERSION
