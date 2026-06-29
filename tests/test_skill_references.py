from __future__ import annotations

import re
from pathlib import Path


SKILLS_ROOT = Path("skills")
SKILL_DIR_NAMES = {
    path.name
    for path in SKILLS_ROOT.iterdir()
    if path.is_dir() and path.name not in {"__pycache__"}
}


def _iter_skill_markdown_files() -> list[Path]:
    return sorted(
        path
        for path in SKILLS_ROOT.rglob("*.md")
        if ".pytest_cache" not in path.parts and "__pycache__" not in path.parts
    )


def _resolve_skill_path(raw_path: str, source: Path) -> Path | None:
    if raw_path.startswith(("http://", "https://", "#")):
        return None
    if "<" in raw_path or ">" in raw_path or "*" in raw_path:
        return None
    if not raw_path.endswith((".md", ".py", ".yaml")):
        return None
    if raw_path.startswith("reports/"):
        return None

    cleaned = raw_path.split("#", 1)[0]
    if cleaned in {"data_locations.local.yaml", "data_locations.yaml", "schema.yaml"}:
        return None
    if cleaned.startswith("./") or cleaned.startswith("../"):
        return (source.parent / cleaned).resolve()
    if cleaned.startswith(("references/", "scripts/", "assets/")):
        return (source.parent / cleaned).resolve()
    if cleaned.startswith("skills/") or cleaned in {"AGENTS.md"}:
        return Path(cleaned).resolve()

    first_part = cleaned.split("/", 1)[0]
    if first_part in SKILL_DIR_NAMES or first_part == "meta":
        return (SKILLS_ROOT / cleaned).resolve()
    if source.parent.name == "references" and "/" not in cleaned:
        return (source.parent / cleaned).resolve()
    return None


def test_skill_markdown_local_references_exist() -> None:
    missing: list[str] = []
    pattern = re.compile(r"`([^`]+)`|\[[^\]]+\]\(([^)]+)\)")

    for source in _iter_skill_markdown_files():
        text = source.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            raw_path = match.group(1) or match.group(2)
            target = _resolve_skill_path(raw_path.strip(), source)
            if target is not None and not target.exists():
                missing.append(f"{source}: {raw_path}")

    assert not missing, "Missing local skill references:\n" + "\n".join(missing)


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


def test_ibl_feedback_skill_uses_namespaced_trigger() -> None:
    """Guard against colliding with host-native feedback commands."""
    text = Path("skills/ibl-feedback/SKILL.md").read_text(encoding="utf-8")

    assert "/ibl-feedback" in text
    assert "Do not trigger on `/feedback` or bare `feedback`" in text
    for stale_trigger in (
        "types `/feedback` or `feedback`",
        "types `/ibl-feedback`, `feedback`",
        "typed text after `/feedback`",
    ):
        assert stale_trigger not in text
