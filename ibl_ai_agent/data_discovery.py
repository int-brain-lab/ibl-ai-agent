"""Describe local dataset metadata and installed loading capabilities without network access."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any

import yaml

from ibl_ai_agent.data_locations import (
    BWM_DATASET_DEFAULTS,
    DataLocationError,
    DataLocations,
    load_data_locations,
    resolve_dataset_dir,
)


def _matches(value: Any, query: str) -> bool:
    terms = re.findall(r"\w+", query.lower())
    text = json.dumps(value, default=str).lower()
    return not terms or any(term in text for term in terms)


def _table_metadata(path: Path) -> dict[str, Any]:
    """Read a Parquet footer, not its rows or signal arrays."""
    import pyarrow.parquet as pq

    parquet = pq.ParquetFile(path)
    return {
        "rows": parquet.metadata.num_rows,
        "columns": [
            {"name": field.name, "type": str(field.type)} for field in parquet.schema_arrow
        ],
    }


def _shard_metadata(store: Path, limit: int) -> dict[str, Any]:
    """Sample manifests only; a sample cannot establish absence across a release."""
    if not store.is_dir():
        return {"sampled_shards": [], "scope": "store is not a shard directory"}
    candidates = sorted(
        path for path in store.iterdir() if not path.name.startswith(".")
    )
    samples = []
    for path in candidates:
        if len(samples) >= limit:
            break
        try:
            if path.suffix == ".zip":
                with zipfile.ZipFile(path) as archive:
                    manifest = json.loads(archive.read("meta.json"))
            elif path.is_dir() and (path / "meta.json").is_file():
                manifest = json.loads((path / "meta.json").read_text())
            else:
                continue
            arrays = {
                name: {
                    "shape": spec.get("shape"),
                    "dtype": spec.get("dtype"),
                    "columns": spec.get("encoding", {}).get("columns", []),
                }
                for name, spec in manifest.get("arrays", {}).items()
            }
            samples.append({"path": str(path), "arrays": arrays})
        except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
            samples.append({"path": str(path), "error": str(exc)})
    return {
        "sampled_shards": samples,
        "scope": "sampled manifests only; unobserved fields may exist in other shards",
    }


def _array_metadata(path: Path) -> dict[str, Any]:
    """Memory-map an NPY header without reading array values."""
    import numpy as np

    array = np.load(path, mmap_mode="r", allow_pickle=False)
    return {"path": str(path), "shape": list(array.shape), "dtype": str(array.dtype)}


def _dataset_metadata(
    name: str, locations: DataLocations, *, sample_shards: int
) -> dict[str, Any]:
    root = locations.datasets[name].root
    result: dict[str, Any] = {
        "name": name, "root": str(root) if root else None, "inspection_errors": [],
    }
    if root is None:
        return {**result, "status": "unconfigured"}
    try:
        directory = resolve_dataset_dir(name, locations)
    except DataLocationError as exc:
        result.update(status="unresolved", error=str(exc))
        if root.is_dir():
            result["detected_files"] = sorted(
                path.name for path in root.iterdir() if not path.name.startswith(".")
            )
        return result
    result.update(status="present_locally", path=str(directory))
    try:
        schema = yaml.safe_load((directory / "schema.yaml").read_text())
        if not isinstance(schema, dict):
            raise TypeError("schema.yaml must contain a mapping")
        result["version"] = schema.get("dataset_version", directory.name)
        result["description"] = schema.get("description")
        result["table_count"] = len(schema.get("tables", {}))
        tables = []
        for table_name, spec in schema.get("tables", {}).items():
            path = directory / spec["path"]
            table = {"name": table_name, **spec, "present_locally": path.is_file()}
            if path.is_file():
                try:
                    table.update(_table_metadata(path))
                except (OSError, ValueError, ImportError) as exc:
                    table["inspection_error"] = str(exc)
                    result["inspection_errors"].append({"path": str(path), "error": str(exc)})
            tables.append(table)
        result["tables"] = tables
        result["stores"] = []
        for store_name, spec in schema.get("stores", {}).items():
            path = directory / spec.get("path", ".")
            store = {"name": store_name, **spec, "present_locally": path.exists()}
            store.update(_shard_metadata(path, sample_shards))
            result["inspection_errors"].extend(
                {"path": sample["path"], "error": sample["error"]}
                for sample in store["sampled_shards"] if "error" in sample
            )
            result["stores"].append(store)
        result["array_files"] = []
        for path in sorted(directory.glob("*.npy")):
            if path.name.startswith("."):
                continue
            try:
                result["array_files"].append(_array_metadata(path))
            except (OSError, ValueError, ImportError) as exc:
                result["array_files"].append({"path": str(path), "inspection_error": str(exc)})
                result["inspection_errors"].append({"path": str(path), "error": str(exc)})
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        result["inspection_error"] = str(exc)
    return result


def _source_metadata(root: Path) -> list[dict[str, Any]]:
    """Read API documentation and literal constants using AST; never import IBL loaders."""
    entries = []
    loader = root / "brainbox" / "io" / "one.py"
    sources = [loader, *sorted((root / "brainbox" / "behavior").glob("*.py"))]
    for path in sources:
        if not path.is_file() or path.name.startswith("_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeError) as exc:
            entries.append({"source": str(path), "inspection_error": str(exc)})
            continue
        if path == loader:
            loaders = []
            for node in tree.body:
                if isinstance(node, ast.FunctionDef):
                    loaders.append((f"brainbox.io.one.{node.name}", node))
                elif isinstance(node, ast.ClassDef):
                    loaders.extend(
                        (f"{node.name}.{method.name}", method)
                        for method in node.body if isinstance(method, ast.FunctionDef)
                    )
            for api, method in loaders:
                if not method.name.startswith("load_"):
                    continue
                entry = {
                    "api": api,
                    "documentation": ast.get_docstring(method) or "",
                    "source": str(path),
                    "line": method.lineno,
                    "dataset_arguments": [
                        ast.unparse(call)
                        for call in ast.walk(method)
                        if isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "load_object"
                    ],
                }
                entries.append(entry)
        else:
            constants = {}
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id.isupper():
                            try:
                                constants[target.id] = json.loads(
                                    json.dumps(ast.literal_eval(node.value), default=str)
                                )
                            except (ValueError, TypeError):
                                pass  # Computed constants are not executed by discovery.
            entry = {
                "module": f"brainbox.behavior.{path.stem}",
                "documentation": ast.get_docstring(tree) or "",
                "constants": constants,
                "functions": [
                    {"name": node.name, "documentation": ast.get_docstring(node) or ""}
                    for node in tree.body
                    if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
                ],
                "source": str(path),
            }
            entries.append(entry)
    return entries


def _filter_dataset(dataset: dict[str, Any], query: str) -> dict[str, Any]:
    """Filter metadata records while keeping dataset status and provenance visible."""
    result = dataset.copy()
    for section in ("tables", "stores", "array_files"):
        if section in dataset:
            result[section] = [item for item in dataset[section] if _matches(item, query)]
    return result


def describe_data(
    locations: DataLocations | None = None,
    *,
    query: str = "",
    sample_shards: int = 1,
    software_root: Path | None = None,
) -> dict[str, Any]:
    """Build a fresh, local-only inventory; query matching is lexical, not semantic."""
    if sample_shards < 0:
        raise ValueError("sample_shards must be nonnegative")
    locations = locations or load_data_locations()
    report: dict[str, Any] = {
        "inspected_at": datetime.now(timezone.utc).isoformat(),
        "query": query,
        "query_fallback": False,
        "query_match_count": None,
        "matching": "lexical OR across query terms; no match does not establish absence",
        "live_availability": "not checked",
        "python_executable": sys.executable,
        "config": str(locations.config_path) if locations.config_path else None,
        "one_cache_root": str(locations.one_cache) if locations.one_cache else None,
        "datasets": [
            _dataset_metadata(name, locations, sample_shards=sample_shards)
            for name in sorted(locations.datasets)
        ],
        "detected_unregistered": [
            {"name": name, "root": str(spec["root"])}
            for name, spec in BWM_DATASET_DEFAULTS.items()
            if name not in locations.datasets and spec["root"].is_dir()
        ],
    }
    try:
        distribution = metadata.distribution("ibllib")
        report["software_version"] = distribution.version
        software_root = software_root or Path(distribution.locate_file(""))
    except metadata.PackageNotFoundError:
        report["software_version"] = None
    source_entries = _source_metadata(software_root) if software_root else []
    report["software_inspection_errors"] = [
        entry for entry in source_entries if "inspection_error" in entry
    ]
    report["software_capabilities"] = [
        entry for entry in source_entries if "inspection_error" not in entry
    ]
    if not software_root or not (software_root / "brainbox/io/one.py").is_file():
        report["software_inspection"] = (
            "incomplete: IBL loader source not found; use the project interpreter or check installation"
        )
    elif report["software_inspection_errors"]:
        report["software_inspection"] = "incomplete: source files could not be read or parsed"
    else:
        report["software_inspection"] = "source available"

    if query.strip():
        datasets = [_filter_dataset(dataset, query) for dataset in report["datasets"]]
        capabilities = [entry for entry in report["software_capabilities"] if _matches(entry, query)]
        matches = len(capabilities) + sum(
            len(dataset.get(section, []))
            for dataset in datasets
            for section in ("tables", "stores", "array_files")
        )
        report["query_match_count"] = matches
        if matches:
            report["datasets"] = datasets
            report["software_capabilities"] = capabilities
        else:
            report["query_fallback"] = True
    report["software_scope"] = (
        "Source documentation and literal constants only; API support does not prove data availability. "
        "Cached indexes, videos, signal contents and remote endpoints are not inspected."
    )
    return report


def format_description(report: dict[str, Any]) -> str:
    """Render a compact first-pass overview; JSON retains the full source evidence."""
    lines = ["Local data (fresh metadata inspection; live availability not checked)"]
    if report["query_fallback"]:
        lines.insert(0, (
            f"No lexical matches for {report['query']!r}; showing the unfiltered overview. "
            "These are discovery candidates, not matches for the requested term."
        ))
    for dataset in report["datasets"]:
        lines.append(
            f"- {dataset['name']} {dataset.get('version', '')}: {dataset['status']} "
            f"({dataset.get('table_count', 0)} declared tables)"
        )
        if dataset.get("error") or dataset.get("inspection_error"):
            lines.append(f"  {dataset.get('error') or dataset['inspection_error']}")
        for error in dataset.get("inspection_errors", []):
            lines.append(f"  Inspection failed at {error['path']}: {error['error']}")
        for table in dataset.get("tables", []):
            columns = ", ".join(field["name"] for field in table.get("columns", []))
            status = "present locally" if table["present_locally"] else "missing locally"
            lines.append(f"  {table['name']} ({status}): {table.get('rows', '?')} rows; {columns}")
        for store in dataset.get("stores", []):
            lines.append(f"  store {store['name']}: {store.get('path', '.')}")
            for sample in store["sampled_shards"]:
                for name, array in sample.get("arrays", {}).items():
                    lines.append(f"  sampled {name}: {', '.join(array['columns'])}")
            lines.append(f"  {store['scope']}")
        for array in dataset.get("array_files", []):
            lines.append(f"  array {Path(array['path']).name}: {array.get('shape', '?')} {array.get('dtype', '')}")
    for found in report["detected_unregistered"]:
        lines.append(f"- Detected but unregistered: {found['name']} at {found['root']}")
    lines.append("Software-supported products (local data availability not established)")
    lines.append(f"{report['software_inspection']} (Python: {report['python_executable']})")
    for error in report["software_inspection_errors"]:
        lines.append(f"Source inspection failed at {error['source']}: {error['inspection_error']}")
    for entry in report["software_capabilities"]:
        name = entry.get("api", entry.get("module", entry["source"]))
        doc = " ".join(entry.get("documentation", "").split())
        lines.append(f"- {name}: {doc[:300]}")
        if entry.get("constants"):
            lines.append(f"  constants: {json.dumps(entry['constants'])}")
        if entry.get("functions"):
            lines.append(f"  functions: {', '.join(item['name'] for item in entry['functions'])}")
    lines.append(report["matching"])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Existing data-locations YAML")
    parser.add_argument("--query", default="", help="Optional lexical filter, e.g. paw")
    parser.add_argument("--sample-shards", type=int, default=1, help="Manifests per store (default 1)")
    parser.add_argument("--json", action="store_true", help="Return full machine-readable evidence")
    args = parser.parse_args()
    try:
        locations = load_data_locations(args.config)
        report = describe_data(locations, query=args.query, sample_shards=args.sample_shards)
    except (DataLocationError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2) if args.json else format_description(report))


if __name__ == "__main__":
    main()
