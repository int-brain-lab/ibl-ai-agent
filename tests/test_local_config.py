from __future__ import annotations

from pathlib import Path

import pytest

from ibl_ai_agent.local_config import default_project_root, load_local_config


def test_load_local_config_resolves_relative_project_root(tmp_path: Path) -> None:
    config = tmp_path / "ibl-agent.local.yaml"
    config.write_text("project_root: ../private/projects\n", encoding="utf-8")

    loaded = load_local_config(tmp_path)

    assert loaded is not None
    assert loaded.project_root == (tmp_path / "../private/projects").resolve()


def test_default_project_root_falls_back_to_repo_projects(tmp_path: Path) -> None:
    assert default_project_root(tmp_path) == tmp_path / "projects"


def test_local_config_requires_mapping(tmp_path: Path) -> None:
    config = tmp_path / "ibl-agent.local.yaml"
    config.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    with pytest.raises(ValueError, match="YAML mapping"):
        load_local_config(tmp_path)
