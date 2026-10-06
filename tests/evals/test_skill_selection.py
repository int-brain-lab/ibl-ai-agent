# pytest tests/evals/tier_1_test_skill_selection.py
from __future__ import annotations

import contextlib
import io
import json
import logging
from pathlib import Path

import pytest
from deepeval import assert_test as _deepeval_assert_test
from deepeval.test_case import LLMTestCase, ToolCall

from tests.evals.actors import ActorJudge, build_actor
from tests.evals.metrics import build_metrics
from tests.evals.summarize_results import record_usage

log = logging.getLogger(__name__)


def assert_test(test_case, metrics) -> None:
    """Wrap deepeval assert_test, demoting its console table to DEBUG level."""
    buf = io.StringIO()
    exc = None
    try:
        with contextlib.redirect_stdout(buf):
            _deepeval_assert_test(test_case=test_case, metrics=metrics)
    except AssertionError as e:
        exc = e
    if report := buf.getvalue():
        log.debug("deepeval report:\n%s", report)
    if exc is not None:
        raise exc

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


def _actor_for(model_cfg: dict):
    """Build the actor for this model, with tools disabled.

    cwd is still required: Claude runs as an agent session rooted in the repo
    even when it has no dataset work to do.
    """
    return build_actor({**model_cfg, "tools": False}, cwd=REPO_ROOT)


def _select_skills(question: str, model_cfg: dict) -> tuple[list[str], object]:
    """Ask one model which skills a question needs. Returns (skills, RunResult).

    The RunResult is returned so the caller can log token usage alongside the
    data-loading runs, putting both questions in one summary table.

    Built with tools disabled: picking skills is a reading task, so the model
    needs no dataset access. Actors give every provider the same interface, so
    this runs on Claude, Mistral and Lightning alike.
    """
    actor = _actor_for(model_cfg)
    prompt = (
        f"{_load_agents_md()}\n\n"
        f"Available skills:\n{_load_skill_context()}\n\n"
        f"Question: {question}\n\n"
        "List only the skill names needed to answer this question, one per line. No explanations."
    )
    result = actor.run(prompt)
    skills = [line.strip().lstrip("- ") for line in result.reply.splitlines() if line.strip()]
    return skills, result


GOLDENS = _load_questions()


@pytest.mark.parametrize("golden", GOLDENS, ids=[g["id"] for g in GOLDENS])
def test_skill_selection(golden: dict, model_cfg: dict) -> None:
    actor = _actor_for(model_cfg)
    selected, result = _select_skills(golden["question"], model_cfg)
    print(f"\nModel selected skills: {selected}")

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
    passed = True
    try:
        assert_test(
            test_case=test_case,
            metrics=build_metrics(golden["checks"], evaluator=ActorJudge(actor)),
        )
    except AssertionError:
        passed = False
        raise
    finally:
        record_usage(actor.name, golden["id"], result.usage, passed)
