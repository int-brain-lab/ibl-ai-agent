from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_validator() -> ModuleType:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "validate_bwm_lfp_release.py"
    spec = importlib.util.spec_from_file_location("validate_bwm_lfp_release", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_empty_sidecars_fail_validation(tmp_path: Path) -> None:
    module = _load_validator()
    (tmp_path / "schema.yaml").write_text("{}\n", encoding="utf-8")
    (tmp_path / "provenance.yaml").write_text("{}\n", encoding="utf-8")
    (tmp_path / "manifest.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / module.DEFAULT_EXPECTED_FILENAME).write_bytes(b"not-an-hdf5")

    report = module.validate_bwm_lfp_release(tmp_path)

    assert not report.ok
    assert "schema dataset_name is bwm_lfp" in report.failures
    assert "provenance dataset_name is bwm_lfp" in report.failures
    assert f"manifest includes an entry for {module.DEFAULT_EXPECTED_FILENAME}" in report.failures
