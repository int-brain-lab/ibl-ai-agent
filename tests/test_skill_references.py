from __future__ import annotations

import re
from pathlib import Path

import pytest


SKILLS_ROOT = Path("skills")
SKILL_DIR_NAMES = {
    path.name
    for path in SKILLS_ROOT.iterdir()
    if path.is_dir() and path.name not in {"__pycache__"}
}


def _iter_instruction_markdown_files() -> list[Path]:
    return [Path("AGENTS.md"), Path("CLAUDE.md")] + sorted(
        path
        for path in SKILLS_ROOT.rglob("*.md")
        if ".pytest_cache" not in path.parts and "__pycache__" not in path.parts
    )


def _resolve_skill_path(raw_path: str, source: Path) -> Path | None:
    if raw_path.startswith(("http://", "https://", "#")):
        return None
    if "<" in raw_path or ">" in raw_path or "*" in raw_path:
        return None
    cleaned = raw_path.split("#", 1)[0].replace("\\", "/")
    if not cleaned.endswith((".md", ".py", ".yaml")):
        return None
    if cleaned.startswith("reports/"):
        return None

    if cleaned in {
        "ibl-agent.local.yaml",
        "data_locations.local.yaml",
        "data_locations.yaml",
        "schema.yaml",
    }:
        return None
    if cleaned.startswith("./") or cleaned.startswith("../"):
        return (source.parent / cleaned).resolve()
    if cleaned.startswith(("references/", "scripts/", "assets/")):
        return (source.parent / cleaned).resolve()
    if cleaned.startswith(("skills/", "docs/")) or cleaned in {"AGENTS.md", "CLAUDE.md"}:
        return Path(cleaned).resolve()

    first_part = cleaned.split("/", 1)[0]
    if first_part in SKILL_DIR_NAMES or first_part == "meta":
        return (SKILLS_ROOT / cleaned).resolve()
    if source.parent.name == "references" and "/" not in cleaned:
        return (source.parent / cleaned).resolve()
    return None


def test_instruction_markdown_local_references_exist() -> None:
    errors: list[str] = []
    pattern = re.compile(r"`([^`]+)`|\[[^\]]+\]\(([^)]+)\)")

    for source in _iter_instruction_markdown_files():
        text = source.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            raw_path = match.group(1) or match.group(2)
            target = _resolve_skill_path(raw_path.strip(), source)
            if target is not None:
                if "\\" in raw_path:
                    errors.append(f"{source}: use forward slashes: {raw_path}")
                if not target.exists():
                    errors.append(f"{source}: missing local reference: {raw_path}")

    assert not errors, "Invalid local instruction references:\n" + "\n".join(errors)


@pytest.mark.parametrize(
    ("raw_path", "source", "expected"),
    [
        ("skills/ibl-load/SKILL.md", "AGENTS.md", "skills/ibl-load/SKILL.md"),
        ("AGENTS.md", "CLAUDE.md", "AGENTS.md"),
        (r"skills\ibl-load\SKILL.md", "AGENTS.md", "skills/ibl-load/SKILL.md"),
        (
            "docs/data_locations.md#configuration",
            "skills/ibl-load/SKILL.md",
            "docs/data_locations.md",
        ),
        (
            "references/missing.md",
            "skills/ibl-load/SKILL.md",
            "skills/ibl-load/references/missing.md",
        ),
        ("ibl-agent.local.yaml", "AGENTS.md", None),
        ("https://example.org/doc.md", "AGENTS.md", None),
        ("projects/<project_slug>/question.md", "AGENTS.md", None),
    ],
)
def test_reference_resolution(raw_path: str, source: str, expected: str | None) -> None:
    target = _resolve_skill_path(raw_path, Path(source))
    assert target == (Path(expected).resolve() if expected is not None else None)


def test_brainbox_references_are_wired_into_skill_system() -> None:
    analyze_skill = Path("skills/ibl-analyze/SKILL.md").read_text(encoding="utf-8")
    assert "brainbox_routing.md" in analyze_skill


def test_ibl_neuropixel_skill_exists_and_is_wired_into_skill_system() -> None:
    skill_path = Path("skills/ibl-neuropixel/SKILL.md")
    assert skill_path.exists()
    text = skill_path.read_text(encoding="utf-8")
    assert "neuropixel_routing.md" in text
    assert "neuropixel_function_signatures.md" in text
    assert "int-brain-lab/ibl-neuropixel" in text
