# uv run pytest tests/evals/tier_1_test_skill_selection.py
from __future__ import annotations

import json
from pathlib import Path

import pytest
from deepeval import assert_test
from deepeval.test_case import LLMTestCase, ToolCall

from tests.evals.actor import get_actor
from tests.evals.metrics import build_metrics

REPO_ROOT = Path(__file__).parent.parent.parent
_QUESTIONS_DIR = Path(__file__).parent.joinpath("questions")
_SKILLS_DIRS = [REPO_ROOT.joinpath("skills"), Path.home().joinpath(".claude", "skills")]


def _load_questions() -> list[dict]:
    questions = []
    for path in sorted(_QUESTIONS_DIR.glob("*.json")):
        for q in json.loads(path.read_text()):
            if q.get("task") == "skill_selection":
                questions.append(q)
    return questions


def _load_agents_md() -> str:
    return REPO_ROOT.joinpath("AGENTS.md").read_text(encoding="utf-8")


def _load_skill_context() -> str:
    lines = []
    for skills_dir in _SKILLS_DIRS:
        for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
            for line in skill_md.read_text(encoding="utf-8").splitlines():
                if line.startswith("description:"):
                    lines.append(f"- {skill_md.parent.name}: {line.removeprefix('description:').strip()}")
                    break
    return "\n".join(lines)


def _select_skills(question: str, model_cfg: dict) -> list[str]:
    model, _ = get_actor(model_cfg)
    prompt = (
        f"{_load_agents_md()}\n\n"
        f"Available skills:\n{_load_skill_context()}\n\n"
        f"Question: {question}\n\n"
        "List only the skill names needed to answer this question, one per line. No explanations."
    )
    response, _ = model.generate(prompt)
    return [line.strip().lstrip("- ") for line in response.splitlines() if line.strip()]


GOLDENS = _load_questions()


@pytest.mark.parametrize("golden", GOLDENS, ids=[g["id"] for g in GOLDENS])
def test_skill_selection(golden: dict, model_cfg: dict) -> None:
    selected = _select_skills(golden["question"], model_cfg)

    # Attach expected_skills to each ToolCorrectnessMetric check before building
    checks_with_tools = []
    for check in golden["checks"]:
        if check["metric"] == "ToolCorrectnessMetric":
            checks_with_tools.append(check)

    expected_tools = [
        ToolCall(name=skill)
        for check in checks_with_tools
        for skill in check.get("expected_skills", [])
    ]

    test_case = LLMTestCase(
        input=golden["question"],
        actual_output="",
        tools_called=[ToolCall(name=s) for s in selected],
        expected_tools=expected_tools,
    )
    assert_test(test_case=test_case, metrics=build_metrics(golden["checks"]))
