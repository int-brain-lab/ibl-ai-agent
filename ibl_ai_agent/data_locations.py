from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Any

import yaml


DEFAULT_CONFIG_NAMES = ("data_locations.local.yaml", "data_locations.yaml")
ENV_CONFIG_PATH = "IBL_AGENT_DATA_LOCATIONS"
REPO_ROOT = Path(__file__).resolve().parents[1]

BWM_DATASET_DEFAULTS = {
    "bwm_ephys": {
        "root": REPO_ROOT / "reports" / "datasets" / "bwm_ephys",
        "size": "about 5 GB",
        "why": "local spike shards, unit/session metadata, and passive ephys tables avoid slow per-session ONE loading",
    },
    "bwm_behavior": {
        "root": REPO_ROOT / "reports" / "datasets" / "bwm_behavior",
        "size": "about 3.5 GB",
        "why": "local trial, wheel, movement-state, pose, and behavior feature tables avoid slow per-session ONE loading",
    },
}


class DataLocationError(RuntimeError):
    """Raised when a configured local data location is missing or invalid."""


@dataclass(frozen=True)
class DatasetLocation:
    name: str
    root: Path | None
    preferred_version: str = "latest"


@dataclass(frozen=True)
class DataLocations:
    config_path: Path | None
    datasets: dict[str, DatasetLocation]
    one_cache: Path | None = None

    def dataset_root(self, name: str) -> Path:
        location = self.datasets.get(name)
        if location is None or location.root is None:
            source = self.config_path or Path("data_locations.local.yaml")
            raise DataLocationError(f"Dataset {name!r} is not configured in {source}.")
        return location.root


def find_data_locations_file(start: Path | str | None = None) -> Path | None:
    env_path = os.environ.get(ENV_CONFIG_PATH, "").strip()
    if env_path:
        return Path(env_path).expanduser()

    current = Path(start or Path.cwd()).expanduser()
    if current.is_file():
        current = current.parent
    current = current.resolve()

    for directory in (current, *current.parents):
        for name in DEFAULT_CONFIG_NAMES:
            candidate = directory / name
            if candidate.exists():
                return candidate
    return None


def load_data_locations(
    config_path: Path | str | None = None,
    *,
    start: Path | str | None = None,
) -> DataLocations:
    resolved_path = Path(config_path).expanduser() if config_path is not None else find_data_locations_file(start)
    if resolved_path is None:
        return DataLocations(config_path=None, datasets=_default_dataset_locations())
    if not resolved_path.exists():
        raise DataLocationError(f"Data locations file does not exist: {resolved_path}")

    payload = yaml.safe_load(resolved_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise DataLocationError(f"Data locations file must contain a mapping: {resolved_path}")

    datasets_raw = payload.get("datasets", {})
    if not isinstance(datasets_raw, dict):
        raise DataLocationError(f"`datasets` must be a mapping in {resolved_path}")

    datasets: dict[str, DatasetLocation] = {}
    base_dir = resolved_path.parent
    for name, raw in datasets_raw.items():
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise DataLocationError(f"Dataset entry {name!r} must be a mapping in {resolved_path}")
        root = _optional_path(raw.get("root"), base_dir=base_dir)
        preferred = str(raw.get("preferred_version") or "latest")
        datasets[str(name)] = DatasetLocation(name=str(name), root=root, preferred_version=preferred)

    for name, location in _default_dataset_locations().items():
        datasets.setdefault(name, location)

    one_cache_raw = payload.get("one_cache", {})
    one_cache: Path | None = None
    if isinstance(one_cache_raw, dict):
        one_cache = _optional_path(one_cache_raw.get("root"), base_dir=base_dir)

    return DataLocations(config_path=resolved_path, datasets=datasets, one_cache=one_cache)


def find_dataset_versions(name: str, locations: DataLocations | None = None) -> list[Path]:
    locations = locations or load_data_locations()
    root = locations.dataset_root(name)
    if not root.exists():
        raise DataLocationError(f"Configured root for {name!r} does not exist: {root}")

    if (root / "schema.yaml").exists():
        return [root]

    versions = [
        path
        for path in root.iterdir()
        if path.is_dir() and _looks_like_version_dir(path.name) and (path / "schema.yaml").exists()
    ]
    return sorted(versions, key=lambda path: _version_key(path.name))


def resolve_dataset_dir(name: str, locations: DataLocations | None = None) -> Path:
    locations = locations or load_data_locations()
    location = locations.datasets.get(name)
    if location is None:
        if name in BWM_DATASET_DEFAULTS:
            raise DataLocationError(_missing_bwm_dataset_message(name, configured_root=None))
        raise DataLocationError(f"Dataset {name!r} is not configured.")

    try:
        versions = find_dataset_versions(name, locations)
    except DataLocationError as exc:
        if name in BWM_DATASET_DEFAULTS:
            raise DataLocationError(_missing_bwm_dataset_message(name, configured_root=location.root)) from exc
        raise
    if not versions:
        if name in BWM_DATASET_DEFAULTS:
            raise DataLocationError(_missing_bwm_dataset_message(name, configured_root=location.root))
        raise DataLocationError(
            f"No dataset versions with schema.yaml found for {name!r} under {location.root}."
        )

    if location.preferred_version == "latest":
        return versions[-1]

    root = locations.dataset_root(name)
    if root.name == location.preferred_version and (root / "schema.yaml").exists():
        return root

    selected = root / location.preferred_version
    if not (selected / "schema.yaml").exists():
        raise DataLocationError(
            f"Configured version {location.preferred_version!r} for {name!r} lacks schema.yaml: {selected}"
        )
    return selected


def _version_key(value: str) -> tuple[Any, ...]:
    parts = re.split(r"(\d+)", value)
    return tuple(int(part) if part.isdigit() else part for part in parts)


def _looks_like_version_dir(value: str) -> bool:
    return re.fullmatch(r"\d+(?:\.\d+)*(?:[-.][A-Za-z0-9]+)*", value) is not None


def _default_dataset_locations() -> dict[str, DatasetLocation]:
    locations: dict[str, DatasetLocation] = {}
    for name, raw in BWM_DATASET_DEFAULTS.items():
        root = raw["root"]
        if _root_has_dataset(root):
            locations[name] = DatasetLocation(name=name, root=root, preferred_version="latest")
    return locations


def _root_has_dataset(root: Path) -> bool:
    if (root / "schema.yaml").exists():
        return True
    if not root.exists():
        return False
    return any(
        path.is_dir() and _looks_like_version_dir(path.name) and (path / "schema.yaml").exists()
        for path in root.iterdir()
    )


def _missing_bwm_dataset_message(name: str, *, configured_root: Path | None) -> str:
    info = BWM_DATASET_DEFAULTS[name]
    expected = info["root"]
    root_text = configured_root if configured_root is not None else expected
    return (
        f"Local {name} dataset is not available at {root_text}. "
        f"For BWM analyses, stop before falling back to ONE/session loaders. "
        f"Ask the user whether to download/configure {name}: it is {info['size']} and is expected by default under "
        f"{_display_path(expected)}. This is needed because {info['why']}. "
        "Alternatives are: point data_locations.local.yaml at an existing dataset root, run the public downloader, "
        "or explicitly use ONE/session loaders only after explaining which required field is missing from the local dataset."
    )


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _optional_path(raw: object, *, base_dir: Path) -> Path | None:
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip() == "":
        return None
    path = Path(str(raw)).expanduser()
    if path.is_absolute():
        return path
    return base_dir / path
