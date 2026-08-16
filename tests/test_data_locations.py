from __future__ import annotations

from pathlib import Path

import pytest

from ibl_ai_agent.data_locations import (
    BWM_DATASET_DEFAULTS,
    DataLocationError,
    find_dataset_versions,
    load_data_locations,
    resolve_dataset_dir,
)


def test_resolve_latest_dataset_version_from_local_config(tmp_path: Path) -> None:
    root = tmp_path / "bwm_ephys"
    for version in ("1.0.0", "1.1.0", "1.2.0"):
        dataset_dir = root / version
        dataset_dir.mkdir(parents=True)
        (dataset_dir / "schema.yaml").write_text("dataset_name: bwm_ephys\n", encoding="utf-8")
    config = tmp_path / "data_locations.local.yaml"
    config.write_text(
        f"datasets:\n  bwm_ephys:\n    root: {root.as_posix()!r}\n    preferred_version: latest\n",
        encoding="utf-8",
    )

    locations = load_data_locations(config)

    assert find_dataset_versions("bwm_ephys", locations) == [root / "1.0.0", root / "1.1.0", root / "1.2.0"]
    assert resolve_dataset_dir("bwm_ephys", locations) == root / "1.2.0"


def test_preferred_dataset_version_still_selects_older_release(tmp_path: Path) -> None:
    root = tmp_path / "bwm_ephys"
    for version in ("1.1.0", "1.2.0"):
        dataset_dir = root / version
        dataset_dir.mkdir(parents=True)
        (dataset_dir / "schema.yaml").write_text("dataset_name: bwm_ephys\n", encoding="utf-8")
    config = tmp_path / "data_locations.local.yaml"
    config.write_text(
        f"datasets:\n  bwm_ephys:\n    root: {root.as_posix()!r}\n    preferred_version: 1.1.0\n",
        encoding="utf-8",
    )

    locations = load_data_locations(config)

    assert resolve_dataset_dir("bwm_ephys", locations) == root / "1.1.0"


def test_direct_dataset_dir_with_schema_is_allowed(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "bwm_behavior" / "1.1.0"
    dataset_dir.mkdir(parents=True)
    (dataset_dir / "schema.yaml").write_text("dataset_name: bwm_behavior\n", encoding="utf-8")
    config = tmp_path / "data_locations.local.yaml"
    config.write_text(
        f"datasets:\n  bwm_behavior:\n    root: {dataset_dir.as_posix()!r}\n",
        encoding="utf-8",
    )

    locations = load_data_locations(config)

    assert resolve_dataset_dir("bwm_behavior", locations) == dataset_dir


def test_relative_roots_are_resolved_against_config_file(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "relative_data" / "bwm_ephys" / "1.0.0"
    dataset_dir.mkdir(parents=True)
    (dataset_dir / "schema.yaml").write_text("dataset_name: bwm_ephys\n", encoding="utf-8")
    config = tmp_path / "data_locations.local.yaml"
    config.write_text(
        "datasets:\n  bwm_ephys:\n    root: relative_data/bwm_ephys\n",
        encoding="utf-8",
    )

    locations = load_data_locations(config)

    assert resolve_dataset_dir("bwm_ephys", locations) == dataset_dir


def test_missing_non_bwm_dataset_configuration_raises_clear_error(tmp_path: Path) -> None:
    config = tmp_path / "data_locations.local.yaml"
    config.write_text("datasets: {}\n", encoding="utf-8")
    locations = load_data_locations(config)

    with pytest.raises(DataLocationError, match="not configured"):
        resolve_dataset_dir("custom_dataset", locations)


def test_default_bwm_dataset_root_is_used_without_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "reports" / "datasets" / "bwm_ephys"
    dataset_dir = root / "1.1.0"
    dataset_dir.mkdir(parents=True)
    (dataset_dir / "schema.yaml").write_text("dataset_name: bwm_ephys\n", encoding="utf-8")
    monkeypatch.setitem(BWM_DATASET_DEFAULTS["bwm_ephys"], "root", root)

    locations = load_data_locations(start=tmp_path)

    assert resolve_dataset_dir("bwm_ephys", locations) == dataset_dir


def test_backup_like_version_directories_are_ignored(tmp_path: Path) -> None:
    root = tmp_path / "bwm_ephys"
    for version in ("1.1.0", "1.1.0~"):
        dataset_dir = root / version
        dataset_dir.mkdir(parents=True)
        (dataset_dir / "schema.yaml").write_text("dataset_name: bwm_ephys\n", encoding="utf-8")
    config = tmp_path / "data_locations.local.yaml"
    config.write_text(
        f"datasets:\n  bwm_ephys:\n    root: {root.as_posix()!r}\n    preferred_version: latest\n",
        encoding="utf-8",
    )

    locations = load_data_locations(config)

    assert find_dataset_versions("bwm_ephys", locations) == [root / "1.1.0"]
    assert resolve_dataset_dir("bwm_ephys", locations) == root / "1.1.0"


def test_missing_bwm_dataset_error_instructs_stop_before_one_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "reports" / "datasets" / "bwm_ephys"
    monkeypatch.setitem(BWM_DATASET_DEFAULTS["bwm_ephys"], "root", root)

    with pytest.raises(DataLocationError) as exc_info:
        resolve_dataset_dir("bwm_ephys")

    message = str(exc_info.value)
    assert "stop before falling back to ONE/session loaders" in message
    assert "about 5 GB" in message
    assert "reports/datasets/bwm_ephys" in message


def test_bwm_lfp_resolves_through_the_same_schema_based_registry(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "bwm_lfp" / "1.0.0"
    dataset_dir.mkdir(parents=True)
    (dataset_dir / "schema.yaml").write_text("dataset_name: bwm_lfp\n", encoding="utf-8")
    config = tmp_path / "data_locations.local.yaml"
    config.write_text(
        f"datasets:\n  bwm_lfp:\n    root: {(tmp_path / 'bwm_lfp').as_posix()!r}\n    preferred_version: latest\n",
        encoding="utf-8",
    )

    locations = load_data_locations(config)

    assert resolve_dataset_dir("bwm_lfp", locations) == dataset_dir


def test_missing_bwm_lfp_error_points_at_the_opt_in_lfp_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "reports" / "datasets" / "bwm_lfp"
    monkeypatch.setitem(BWM_DATASET_DEFAULTS["bwm_lfp"], "root", root)

    with pytest.raises(DataLocationError) as exc_info:
        resolve_dataset_dir("bwm_lfp")

    message = str(exc_info.value)
    assert "about 14 GB (opt-in)" in message
    assert "scripts/download_datasets.py --lfp" in message
