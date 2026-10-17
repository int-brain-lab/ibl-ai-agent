from __future__ import annotations

import json
import socket
import subprocess
import sys
import zipfile
from importlib import metadata
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml

from ibl_ai_agent import data_discovery, data_locations
from ibl_ai_agent.data_locations import DataLocations, DatasetLocation, load_data_locations


@pytest.fixture
def dataset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DataLocations:
    monkeypatch.setattr(data_discovery, "BWM_DATASET_DEFAULTS", {})
    monkeypatch.setattr(data_locations, "_default_dataset_locations", dict)
    root = tmp_path / "example" / "1.0.0"
    root.mkdir(parents=True)
    (root / "schema.yaml").write_text(yaml.safe_dump({
        "dataset_version": "1.0.0",
        "tables": {"measurements": {"path": "measurements.pqt", "primary_key": ["id"]}},
        "stores": {"cameras": {"path": "sessions"}},
    }))
    pq.write_table(pa.table({"id": [1, 2], "new_measurement": [0.5, 0.8]}), root / "measurements.pqt")
    sessions = root / "sessions"
    sessions.mkdir()
    with zipfile.ZipFile(sessions / "001.zip", "w") as archive:
        archive.writestr("meta.json", json.dumps({"arrays": {
            "camera.features": {"shape": [10, 2], "dtype": "float32", "encoding": {
                "columns": ["paw_l_x", "novel_feature"],
            }},
        }}))
        archive.writestr("signals.blosc", b"not decoded by discovery")
    (sessions / "._001.zip").write_bytes(b"not a ZIP")
    return DataLocations(None, {"example": DatasetLocation("example", root)})


@pytest.fixture
def software(tmp_path: Path) -> Path:
    root = tmp_path / "installed"
    io = root / "brainbox" / "io"
    behavior = root / "brainbox" / "behavior"
    io.mkdir(parents=True)
    behavior.mkdir(parents=True)
    (io / "one.py").write_text('''raise RuntimeError("must never import loaders")
class SessionLoader:
    def load_pawstates(self):
        """Load paw state probabilities; data availability must be checked."""
        return self.one.load_object(self.eid, "leftCamera", attribute="pawstates", collection="alf/actions")
    def load_new_product(self):
        """A newly installed capability."""
''')
    (behavior / "pawstates.py").write_text('''"""Paw state processing."""
LABELS = ("still", "groom", "new_class")
COMPUTED = dangerous_function()
''')
    return root


def test_discovers_schema_columns_and_sampled_fields_without_loading_signals(
    dataset: DataLocations, software: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("No table rows or network connections during discovery")

    monkeypatch.setattr(pq, "read_table", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    report = data_discovery.describe_data(dataset, software_root=software)
    table = report["datasets"][0]["tables"][0]
    assert table["rows"] == 2
    assert table["columns"][1]["name"] == "new_measurement"
    store = report["datasets"][0]["stores"][0]
    assert len(store["sampled_shards"]) == 1
    assert store["sampled_shards"][0]["arrays"]["camera.features"]["columns"] == [
        "paw_l_x", "novel_feature",
    ]
    assert report["live_availability"] == "not checked"


def test_separates_supported_pawstates_from_local_data(
    dataset: DataLocations, software: Path,
) -> None:
    report = data_discovery.describe_data(dataset, query="paw", software_root=software)
    entries = report["software_capabilities"]
    loader = next(entry for entry in entries if entry.get("api") == "SessionLoader.load_pawstates")
    assert 'attribute=\'pawstates\'' in loader["dataset_arguments"][0]
    module = next(entry for entry in entries if "constants" in entry)
    assert module["constants"]["LABELS"] == ["still", "groom", "new_class"]
    assert "COMPUTED" not in module["constants"]
    assert not report["datasets"][0]["tables"]
    assert report["datasets"][0]["stores"]  # Paw trajectories exist, not paw-state predictions.


def test_new_configured_datasets_and_loader_methods_appear_without_catalog(
    dataset: DataLocations, software: Path, tmp_path: Path,
) -> None:
    root = dataset.datasets["example"].root
    config = tmp_path / "locations.yaml"
    config.write_text(yaml.safe_dump({"datasets": {"new_dataset": {"root": str(root)}}}))
    report = data_discovery.describe_data(load_data_locations(config), software_root=software)
    assert any(item["name"] == "new_dataset" for item in report["datasets"])
    assert any(entry.get("api") == "SessionLoader.load_new_product" for entry in report["software_capabilities"])


@pytest.mark.parametrize("query", ["lfp", "passive_rfmap"])
def test_discovers_module_loaders_without_importing_them(
    dataset: DataLocations, software: Path, query: str,
) -> None:
    source = software / "brainbox" / "io" / "one.py"
    source.write_text(source.read_text() + '''
def load_lfp(one, eid):
    """Load LFP recordings."""
    return one.load_object(eid, "lfp")
def load_passive_rfmap(one, eid):
    """Load passive_rfmap stimuli."""
    return one.load_object(eid, "passiveRFM")
def _load_private():
    """Internal LFP helper."""
''')
    report = data_discovery.describe_data(dataset, query=query, software_root=software)
    assert report["query_fallback"] is False
    assert report["query_match_count"] == 1
    entry, = report["software_capabilities"]
    assert entry["api"] == f"brainbox.io.one.load_{query}"
    assert entry["documentation"]
    assert entry["source"] == str(source)
    assert source.read_text().splitlines()[entry["line"] - 1].startswith(f"def load_{query}(")
    assert entry["dataset_arguments"][0].startswith("one.load_object(")
    assert entry["api"] in data_discovery.format_description(report)


def test_missing_table_and_unresolved_root_are_reported_without_hiding_other_data(
    dataset: DataLocations, software: Path, tmp_path: Path,
) -> None:
    (dataset.datasets["example"].root / "measurements.pqt").unlink()
    locations = DataLocations(None, {
        **dataset.datasets,
        "missing": DatasetLocation("missing", tmp_path / "absent"),
        "unconfigured": DatasetLocation("unconfigured", None),
    })
    report = data_discovery.describe_data(locations, software_root=software)
    assert report["datasets"][0]["tables"][0]["present_locally"] is False
    assert report["datasets"][1]["status"] == "unresolved"
    assert report["datasets"][2]["status"] == "unconfigured"
    assert "missing locally" in data_discovery.format_description(report)


@pytest.mark.parametrize("query", ["grooming", "fidget"])
def test_query_miss_returns_labelled_unfiltered_overview(
    dataset: DataLocations, software: Path, query: str,
) -> None:
    report = data_discovery.describe_data(dataset, query=query, software_root=software)
    assert report["query"] == query
    assert report["query_match_count"] == 0
    assert report["query_fallback"] is True
    assert report["datasets"][0]["tables"][0]["name"] == "measurements"
    assert any(entry.get("api") == "SessionLoader.load_pawstates" for entry in report["software_capabilities"])
    assert "does not establish absence" in report["matching"]
    rendered = data_discovery.format_description(report)
    assert "unfiltered overview" in rendered
    assert "not matches for the requested term" in rendered


def test_matching_query_stays_filtered(dataset: DataLocations, software: Path) -> None:
    report = data_discovery.describe_data(dataset, query="new_measurement", software_root=software)
    assert report["query_fallback"] is False
    assert report["query_match_count"] == 1
    assert report["datasets"][0]["tables"][0]["name"] == "measurements"
    assert not report["software_capabilities"]


def test_shard_sampling_can_be_disabled(dataset: DataLocations, software: Path) -> None:
    report = data_discovery.describe_data(dataset, sample_shards=0, software_root=software)
    assert report["datasets"][0]["stores"][0]["sampled_shards"] == []


@pytest.mark.parametrize("kind", ["table", "shard", "array"])
@pytest.mark.parametrize("query", [
    pytest.param("", id="unfiltered"),
    pytest.param("paw", id="matching"),
    pytest.param("xyzunmatched", id="fallback"),
])
def test_dataset_errors_survive_queries(
    dataset: DataLocations, software: Path, kind: str, query: str,
) -> None:
    root = dataset.datasets["example"].root
    paths = {
        "table": root / "measurements.pqt",
        "shard": root / "sessions" / "001.zip",
        "array": root / "broken.npy",
    }
    path = paths[kind]
    path.write_bytes(b"corrupt file")
    unfiltered = data_discovery.describe_data(dataset, software_root=software)
    expected = unfiltered["datasets"][0]["inspection_errors"]
    assert len(expected) == 1
    assert expected[0]["path"] == str(path)
    assert expected[0]["error"]

    report = data_discovery.describe_data(dataset, software_root=software, query=query)
    assert report["datasets"][0]["inspection_errors"] == expected
    rendered = data_discovery.format_description(report)
    assert str(path) in rendered
    assert rendered.count(expected[0]["error"]) == 1
    if query == "paw":
        # Two software matches, plus the intact camera manifest when applicable.
        assert report["query_match_count"] == (2 if kind == "shard" else 3)
        assert report["query_fallback"] is False
        assert not report["datasets"][0]["tables"]
        assert not report["datasets"][0]["array_files"]
        if kind == "shard":
            assert not report["datasets"][0]["stores"]
    elif query:
        assert report["query_match_count"] == 0
        assert report["query_fallback"] is True


@pytest.mark.parametrize("source_path", ["brainbox/io/one.py", "brainbox/behavior/pawstates.py"])
@pytest.mark.parametrize("query", ["new_measurement", "grooming"])
def test_source_parse_errors_remain_visible_in_filtered_and_fallback_output(
    dataset: DataLocations, software: Path, source_path: str, query: str,
) -> None:
    source = software / source_path
    source.write_text("def broken(:\n")
    report = data_discovery.describe_data(dataset, query=query, software_root=software)
    assert report["software_inspection"].startswith("incomplete:")
    assert len(report["software_inspection_errors"]) == 1
    error = report["software_inspection_errors"][0]
    assert error["source"] == str(source)
    assert "invalid syntax" in error["inspection_error"]
    rendered = data_discovery.format_description(report)
    assert "incomplete:" in rendered
    assert str(source) in rendered
    assert error["inspection_error"] in rendered
    assert report["query_fallback"] is (query == "grooming")
    assert report["datasets"][0]["tables"][0]["rows"] == 2


def test_missing_ibl_installation_is_reported_as_incomplete_not_absent(
    dataset: DataLocations, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "distribution", unavailable)
    report = data_discovery.describe_data(dataset)
    assert report["software_version"] is None
    assert report["software_inspection"].startswith("incomplete:")
    assert report["python_executable"] == sys.executable
    assert "incomplete:" in data_discovery.format_description(report)


def test_default_directory_without_schema_is_detected_without_registration(
    dataset: DataLocations, software: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "unregistered"
    root.mkdir()
    (root / "recording.h5").touch()
    monkeypatch.setattr(data_discovery, "BWM_DATASET_DEFAULTS", {"unregistered": {"root": root}})
    report = data_discovery.describe_data(dataset, software_root=software)
    assert report["detected_unregistered"] == [{"name": "unregistered", "root": str(root)}]
    assert not (root / "schema.yaml").exists()


def test_cli_emits_fresh_json(dataset: DataLocations, tmp_path: Path) -> None:
    config = tmp_path / "locations.yaml"
    config.write_text(yaml.safe_dump({"datasets": {"example": {
        "root": str(dataset.datasets["example"].root),
    }}}))
    result = subprocess.run(
        [sys.executable, "-m", "ibl_ai_agent.data_discovery", "--config", str(config), "--json"],
        check=True, capture_output=True, text=True,
    )
    report = json.loads(result.stdout)
    example = next(item for item in report["datasets"] if item["name"] == "example")
    assert example["tables"][0]["rows"] == 2
