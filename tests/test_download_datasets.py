from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml


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


def _lfp_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Load the downloader with LFP paths redirected under ``tmp_path``."""
    module = _load_downloader()
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "LFP_DIR", tmp_path / "reports" / "datasets" / "bwm_lfp")
    monkeypatch.setattr(module, "CONFIG_PATH", tmp_path / "data_locations.local.yaml")
    return module


def test_download_lfp_file_writes_sidecars_and_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _lfp_module(tmp_path, monkeypatch)
    payload = b"fake-lfp-bytes"
    spec = module.LFPFileSpec(
        dataset="bwm_lfp",
        version="1.0.0",
        filename="lf_compressed_all_bwm.h5",
        url="https://example.com/lf_compressed_all_bwm.h5",
        sha1=hashlib.sha1(payload).hexdigest(),
    )
    calls = []

    def fake_download_file(url: str, destination: Path) -> None:
        calls.append(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)

    monkeypatch.setattr(module, "download_file", fake_download_file)

    assert module.download_lfp_file(spec) == 0
    assert calls == [spec.url]

    target_dir = spec.target_dir
    schema = yaml.safe_load((target_dir / "schema.yaml").read_text())
    assert schema["dataset_name"] == "bwm_lfp"
    assert schema["dataset_version"] == "1.0.0"
    assert schema["stores"]["lf_compressed"]["n_channels"] == [96, 384]
    assert schema["stores"]["lf_compressed"]["channel_count_distribution"] == {96: 4, 384: 695}
    provenance = yaml.safe_load((target_dir / "provenance.yaml").read_text())
    assert provenance["source"]["package"] == "lfpack"
    manifest = yaml.safe_load((target_dir / "manifest.json").read_text())
    assert manifest["files"] == [
        {"path": "lf_compressed_all_bwm.h5", "sha1": spec.sha1, "size_bytes": len(payload)}
    ]

    config = yaml.safe_load(module.CONFIG_PATH.read_text())
    assert config["datasets"]["bwm_lfp"] == {"root": "reports/datasets/bwm_lfp", "preferred_version": "latest"}

    # Re-running with an already-verified file must not re-download.
    assert module.download_lfp_file(spec) == 0
    assert calls == [spec.url]


def test_download_lfp_file_redownloads_on_sha1_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _lfp_module(tmp_path, monkeypatch)
    good_payload = b"fake-lfp-bytes"
    spec = module.LFPFileSpec(
        dataset="bwm_lfp",
        version="1.0.0",
        filename="lf_compressed_all_bwm.h5",
        url="https://example.com/lf_compressed_all_bwm.h5",
        sha1=hashlib.sha1(good_payload).hexdigest(),
    )
    spec.target_path.parent.mkdir(parents=True)
    spec.target_path.write_bytes(b"stale-bytes")

    monkeypatch.setattr(
        module,
        "download_file",
        lambda url, destination: destination.write_bytes(good_payload),
    )

    assert module.download_lfp_file(spec) == 0
    assert spec.target_path.read_bytes() == good_payload


def test_download_lfp_file_repairs_sidecars_without_redownloading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _lfp_module(tmp_path, monkeypatch)
    payload = b"valid-lfp-bytes"
    spec = module.LFPFileSpec(
        dataset="bwm_lfp",
        version="1.0.0",
        filename="lf_compressed_all_bwm.h5",
        url="https://example.com/lf_compressed_all_bwm.h5",
        sha1=hashlib.sha1(payload).hexdigest(),
    )
    spec.target_path.parent.mkdir(parents=True)
    spec.target_path.write_bytes(payload)
    (spec.target_dir / "schema.yaml").write_text("dataset_name: stale\n", encoding="utf-8")

    monkeypatch.setattr(
        module,
        "download_file",
        lambda url, destination: pytest.fail("a verified LFP file must not be downloaded again"),
    )

    assert module.download_lfp_file(spec) == 0
    assert yaml.safe_load((spec.target_dir / "schema.yaml").read_text())["dataset_name"] == "bwm_lfp"
    assert (spec.target_dir / "provenance.yaml").exists()
    assert (spec.target_dir / "manifest.json").exists()
