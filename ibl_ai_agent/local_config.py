from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

LOCAL_CONFIG_FILENAME = "ibl-agent.local.yaml"


@dataclass(frozen=True)
class LocalConfig:
    path: Path
    project_root: Path | None = None


def load_local_config(root: Path | None = None) -> LocalConfig | None:
    """Load optional repo-local configuration for machine/private paths."""
    base = (root or Path.cwd()).resolve()
    path = base / LOCAL_CONFIG_FILENAME
    if not path.exists():
        return None

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{LOCAL_CONFIG_FILENAME} must contain a YAML mapping")

    project_root = _optional_path(raw.get("project_root"), config_dir=path.parent)
    return LocalConfig(path=path, project_root=project_root)


def default_project_root(root: Path | None = None) -> Path:
    config = load_local_config(root)
    if config and config.project_root:
        return config.project_root
    return (root or Path.cwd()).resolve() / "projects"


def _optional_path(value: Any, *, config_dir: Path) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("project_root must be a non-empty string when provided")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = config_dir / path
    return path.resolve()
