from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_downloader() -> ModuleType:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "download_datasets.py"
    spec = importlib.util.spec_from_file_location("download_datasets", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_schema(path: Path) -> None:
    path.mkdir(parents=True)
    (path / "schema.yaml").write_text("dataset_name: bwm_ephys\n", encoding="utf-8")


def _current_version(module: ModuleType, dataset: str) -> str:
    """Return the current archive version the downloader ships for ``dataset``."""
    (spec,) = [archive for archive in module.ARCHIVES if archive.dataset == dataset]
    return spec.version


def test_download_plan_fetches_missing_current_version_when_old_version_exists(tmp_path: Path) -> None:
    module = _load_downloader()
    ephys_version = _current_version(module, "bwm_ephys")
    behavior_version = _current_version(module, "bwm_behavior")
    ephys_root = tmp_path / "bwm_ephys"
    behavior_root = tmp_path / "bwm_behavior"
    _write_schema(ephys_root / "1.1.0")
    _write_schema(behavior_root / behavior_version)
    config = {
        "datasets": {
            "bwm_ephys": {"root": str(ephys_root), "preferred_version": "latest"},
            "bwm_behavior": {"root": str(behavior_root), "preferred_version": "latest"},
        }
    }

    plan = module.plan_current_archives(config)

    assert [(item.archive.dataset, item.archive.version, item.target_dir) for item in plan.downloads] == [
        ("bwm_ephys", ephys_version, ephys_root / ephys_version)
    ]
    assert not plan.exact_version_pins
    assert not plan.invalid_manual_roots


def test_download_plan_respects_exact_older_dataset_root(tmp_path: Path) -> None:
    module = _load_downloader()
    ephys_version = _current_version(module, "bwm_ephys")
    behavior_version = _current_version(module, "bwm_behavior")
    ephys_exact_root = tmp_path / "bwm_ephys" / "1.1.0"
    behavior_root = tmp_path / "bwm_behavior"
    _write_schema(ephys_exact_root)
    _write_schema(behavior_root / behavior_version)
    config = {
        "datasets": {
            "bwm_ephys": {"root": str(ephys_exact_root), "preferred_version": "latest"},
            "bwm_behavior": {"root": str(behavior_root), "preferred_version": "latest"},
        }
    }

    plan = module.plan_current_archives(config)

    assert not plan.downloads
    assert [(pin.dataset, pin.configured_version, pin.current_version) for pin in plan.exact_version_pins] == [
        ("bwm_ephys", "1.1.0", ephys_version)
    ]
    assert not plan.invalid_manual_roots


def test_download_plan_flags_missing_manual_root(tmp_path: Path) -> None:
    module = _load_downloader()
    behavior_version = _current_version(module, "bwm_behavior")
    missing_ephys_root = tmp_path / "missing" / "bwm_ephys"
    behavior_root = tmp_path / "bwm_behavior"
    _write_schema(behavior_root / behavior_version)
    config = {
        "datasets": {
            "bwm_ephys": {"root": str(missing_ephys_root), "preferred_version": "latest"},
            "bwm_behavior": {"root": str(behavior_root), "preferred_version": "latest"},
        }
    }

    plan = module.plan_current_archives(config)

    assert not plan.downloads
    assert plan.invalid_manual_roots == [("bwm_ephys", missing_ephys_root)]
