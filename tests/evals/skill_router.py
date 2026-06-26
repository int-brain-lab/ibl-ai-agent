"""Skill file router for DeepEval evals.

Maps the Required Load Packet names from AGENTS.md to skill file paths,
so individual tests declare packet names rather than file paths.

Usage
-----
from tests.evals.skill_router import get_skill_files

SKILL_FILES = get_skill_files("scientific", "data_loading", "bwm", "anatomy")
RETRIEVAL_CONTEXT = [p.read_text() for p in SKILL_FILES]
"""

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
_S = REPO_ROOT.joinpath("skills")
_D = REPO_ROOT.joinpath("docs")

# Keys mirror the Required Load Packets section of AGENTS.md.
PACKETS: dict[str, list[Path]] = {
    # Plain IBL scientific question
    "scientific": [
        _S.joinpath("exploration-confirmation", "SKILL.md"),
        _S.joinpath("scientific-coding-style", "SKILL.md"),
        _S.joinpath("ibl-analyze", "SKILL.md"),
    ],
    # Final report writing
    "reporting": [
        _S.joinpath("ibl-report", "SKILL.md"),
    ],
    # IBL data loading (base)
    "data_loading": [
        _S.joinpath("ibl-load", "SKILL.md"),
        _S.joinpath("ibl-load", "references", "data_loading.md"),
    ],
    # Local dataset path resolution
    "data_locations": [
        _D.joinpath("data_locations.md"),
    ],
    # Brain Wide Map question
    "bwm": [
        _S.joinpath("ibl-load", "references", "bwm_runtime_policy.md"),
        _S.joinpath("ibl-load", "references", "bwm_ephys_spike_example.md"),
        _S.joinpath("ibl-analyze", "references", "bwm_analysis_patterns.md"),
    ],
    # Region filtering, QC, and atlas mapping for spike-sorting outputs
    "region_qc": [
        _S.joinpath("ibl-load", "references", "brain_regions_qc.md"),
    ],
    # Anatomical atlas navigation, hierarchy traversal, brain-map plots
    "anatomy": [
        _S.joinpath("ibl-anatomy", "SKILL.md"),
        _S.joinpath("ibl-anatomy", "references", "atlas_navigation.md"),
    ],
    # Ambiguous scientific metric definitions
    "metric_semantics": [
        _S.joinpath("ibl-analyze", "references", "scientific_context_and_metric_semantics.md"),
    ],
    # ONE / Alyx authentication and session search
    "access": [
        _S.joinpath("ibl-access", "SKILL.md"),
        _S.joinpath("ibl-access", "references", "one_auth.md"),
        _S.joinpath("ibl-access", "references", "session_search.md"),
    ],
    # Raw Neuropixels / SpikeGLX preprocessing
    "neuropixel": [
        _S.joinpath("ibl-neuropixel", "SKILL.md"),
        _S.joinpath("ibl-neuropixel", "references", "neuropixel_routing.md"),
    ],
}


def get_skill_files(*packets: str) -> list[Path]:
    """Return deduplicated ordered skill file paths for the given packet names.

    Parameters
    ----------
    *packets
        One or more packet names matching keys in ``PACKETS``.

    Returns
    -------
    list[Path]
        Deduplicated skill file paths in declaration order.

    Raises
    ------
    KeyError
        If an unknown packet name is passed.
    """
    seen: set[Path] = set()
    result: list[Path] = []
    for packet in packets:
        for path in PACKETS[packet]:
            if path not in seen:
                seen.add(path)
                result.append(path)
    return result
