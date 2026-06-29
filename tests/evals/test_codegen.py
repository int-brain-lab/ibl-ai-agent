# pytest tests/evals/test_codegen.py
from __future__ import annotations

import contextlib
import io
import json  # used by _load_questions
import logging
import re
import traceback
from pathlib import Path

log = logging.getLogger(__name__)

import matplotlib
matplotlib.use("Agg")
import pytest

from deepeval import assert_test as _deepeval_assert_test
from deepeval.test_case import LLMTestCase


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

from tests.evals.actor import get_actor
from tests.evals.metrics import build_metrics
from tests.evals.skill_router import get_skill_files

_QUESTIONS_DIR = Path(__file__).parent.joinpath("questions")

# Max code-generation + fix attempts before giving up
MAX_ATTEMPTS = 3

_CODEGEN_TEMPLATE = """\
You are an IBL data analysis agent. Use the following reference materials to answer the question.

{context}

Question: {question}

Think through your approach before writing code. When ready, write your solution in a \
```python code block.
Print your results to stdout — the output is captured and evaluated directly.
"""

_FIX_SUFFIX = """\


Your previous attempt (attempt {attempt}) failed with this error:
```
{tb}```
Please fix the code and try again.
"""


def _load_questions() -> list[dict]:
    questions = []
    for path in sorted(_QUESTIONS_DIR.glob("*.json")):
        for q in json.loads(path.read_text()):
            if q.get("task") == "codegen":
                questions.append(q)
    return questions


def _extract_code_block(response: str) -> str:
    """Return the last ```python block in the response, or the full response if none found."""
    blocks = re.findall(r"```python\s*(.*?)```", response, re.DOTALL)
    if blocks:
        return blocks[-1].strip()
    # Fallback: strip generic fences
    lines = response.strip().splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _build_prompt(golden: dict) -> tuple[list[str], str]:
    """Return (retrieval_context, base_prompt) for a codegen question."""
    skill_files = get_skill_files(*golden["packets"]) if golden.get("packets") else []
    retrieval_context = [p.read_text() for p in skill_files]
    context_block = "\n\n".join(
        f"--- {p.name} ---\n{text}" for p, text in zip(skill_files, retrieval_context)
    )

    # Inject the dataset schema so the model knows exactly which files exist.
    # bwm_runtime_policy.md instructs the real agent to inspect schema.yaml before planning.
    if golden.get("requires_data"):
        try:
            from ibl_ai_agent.data_locations import resolve_dataset_dir
            dataset_dir = resolve_dataset_dir(golden["requires_data"])
            schema_path = dataset_dir.joinpath("schema.yaml")
            if schema_path.exists():
                schema_text = schema_path.read_text()
                context_block += f"\n\n--- schema.yaml ({golden['requires_data']} at {dataset_dir}) ---\n{schema_text}"
        except Exception:
            pass

    prompt = _CODEGEN_TEMPLATE.format(context=context_block, question=golden["question"])
    return retrieval_context, prompt


def _exec_code(code: str, tmp_path: Path) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(code, {"FIGURE_PATH": str(tmp_path.joinpath("figure.png"))})  # noqa: S102
    output = buf.getvalue().strip()
    if not output:
        raise RuntimeError(
            "Code ran without error but printed nothing. Print your results to stdout."
        )
    return output


def _generate_and_fix(model, base_prompt: str, tmp_path: Path) -> tuple[str, str]:
    """Generate code and retry with error feedback on execution failure.

    Parameters
    ----------
    model
        DeepEval actor model.
    base_prompt
        Initial prompt (context + question + output format instructions).
    tmp_path
        Pytest tmp directory; each attempt overwrites ``generated_code.py``.

    Returns
    -------
    tuple[str, str]
        ``(final_code, answer)`` where ``answer`` is the executed result string,
        or an error string if all attempts fail.
    """
    prompt = base_prompt
    code = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response, _ = model.generate(prompt)
        code = _extract_code_block(response)
        tmp_path.joinpath(f"generated_code_attempt_{attempt}.py").write_text(code)
        try:
            answer = _exec_code(code, tmp_path)
            return code, answer
        except Exception as exc:
            tb = traceback.format_exc()
            log.debug("code that failed (attempt %d):\n%s", attempt, code)
            if attempt < MAX_ATTEMPTS:
                prompt = base_prompt + _FIX_SUFFIX.format(attempt=attempt, tb=tb)

    return code, f"CODE EXECUTION FAILED after {MAX_ATTEMPTS} attempts: {tb.splitlines()[-1]}"


GOLDENS = _load_questions()


@pytest.mark.parametrize("golden", GOLDENS, ids=[g["id"] for g in GOLDENS])
def test_codegen(golden: dict, model_cfg: dict, tmp_path: Path) -> None:
    if "requires_data" in golden:
        try:
            from ibl_ai_agent.data_locations import resolve_dataset_dir
            resolve_dataset_dir(golden["requires_data"])
        except Exception:
            pytest.skip(f"Required dataset not available: {golden['requires_data']}")

    retrieval_context, base_prompt = _build_prompt(golden)
    model, _ = get_actor(model_cfg)

    code_checks = [c for c in golden["checks"] if c.get("stage") == "code"]
    output_checks = [c for c in golden["checks"] if c.get("stage") == "output"]

    if output_checks:
        code, answer = _generate_and_fix(model, base_prompt, tmp_path)
    else:
        response, _ = model.generate(base_prompt)
        code = _extract_code_block(response)
        tmp_path.joinpath("generated_code_attempt_1.py").write_text(code)
        answer = None

    if code_checks:
        log.debug("generated code:\n%s", code)
        assert_test(
            LLMTestCase(input=golden["question"], actual_output=code, retrieval_context=retrieval_context),
            build_metrics(code_checks),
        )

    if output_checks:
        # execution output already printed by _exec_code; log full code at debug level
        log.debug("generated code:\n%s", code)
        assert_test(
            LLMTestCase(input=golden["question"], actual_output=answer, retrieval_context=retrieval_context),
            build_metrics(output_checks),
        )
