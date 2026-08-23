from __future__ import annotations

import importlib.util
import json
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


def test_channel_count_distribution_is_checked(tmp_path: Path, monkeypatch) -> None:
    module = _load_validator()
    data_path = tmp_path / module.DEFAULT_EXPECTED_FILENAME
    data_path.write_bytes(b"fake-hdf5")
    (tmp_path / "schema.yaml").write_text(
        """
dataset_name: bwm_lfp
dataset_version: 1.0.0
stores:
  lf_compressed:
    path: lf_compressed_all_bwm.h5
    n_recordings: 3
    n_channels: [96, 384]
    channel_count_distribution:
      96: 1
      384: 2
    compression_tier: standard
""".lstrip(),
        encoding="utf-8",
    )
    (tmp_path / "provenance.yaml").write_text(
        """
dataset_name: bwm_lfp
dataset_version: 1.0.0
source:
  package: lfpack
""".lstrip(),
        encoding="utf-8",
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "files": [
                    {
                        "path": data_path.name,
                        "sha1": module._sha1(data_path),
                        "size_bytes": data_path.stat().st_size,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    class FakeLFPackReader:
        channel_counts = {"np2": 96, "np1-a": 384, "np1-b": 384}

        @staticmethod
        def recordings(path: str) -> list[str]:
            return list(FakeLFPackReader.channel_counts)

        def __init__(self, path: str, *, recording: str, scale: int) -> None:
            self.nc = self.channel_counts[recording]

    fake_lfpack = ModuleType("lfpack")
    fake_lfpack.LFPackReader = FakeLFPackReader
    monkeypatch.setitem(sys.modules, "lfpack", fake_lfpack)

    report = module.validate_bwm_lfp_release(
        tmp_path,
        expected_version="1.0.0",  # matches this fixture's schema/provenance, independent of DEFAULT_EXPECTED_VERSION
        expected_n_recordings=3,
        expected_channel_count_distribution={96: 1, 384: 2},
    )
    assert report.ok
    assert report.details["channel_count_distribution"] == {96: 1, 384: 2}

    FakeLFPackReader.channel_counts["np2"] = 95
    report = module.validate_bwm_lfp_release(
        tmp_path,
        expected_version="1.0.0",
        expected_n_recordings=3,
        expected_channel_count_distribution={96: 1, 384: 2},
    )
    assert not report.ok
    assert "file channel-count distribution is {96: 1, 384: 2}" in report.failures
