"""Pytest configuration for the evals test suite.

Injects the model grid via ``pytest_generate_tests`` so every test that declares
a ``model_cfg`` fixture is automatically parametrized over all configured models.

Model sources (in priority order):
1. ``tests/evals/models.json``  — user-local grid config, not committed
2. ``tests/evals/models.default.json``  — committed single-model fallback
3. ``ACTOR_PROVIDER`` / ``ACTOR_MODEL`` env vars  — single-model fallback
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv

_DIR = Path(__file__).parent

load_dotenv(_DIR.joinpath(".env.local"), override=False)

# Accumulates (model_id, question_id, outcome) across workers when using -n auto.
_results: list[tuple[str, str, str]] = []


def _load_models() -> list[dict]:
    for path in (_DIR.joinpath("models.json"), _DIR.joinpath("models.default.json")):
        if path.exists():
            return json.loads(path.read_text())
    return [{"provider": os.getenv("ACTOR_PROVIDER", ""), "model": os.getenv("ACTOR_MODEL", "")}]


def _model_id(cfg: dict) -> str:
    parts = [cfg.get("provider", ""), cfg.get("model", "")]
    return "/".join(p for p in parts if p)


def _parse_nodeid(nodeid: str) -> tuple[str, str] | None:
    """Extract (model_id, question_id) from a parametrized test node id."""
    m = re.search(r"\[(.+)-([^-\[]+)\]$", nodeid)
    if not m:
        return None
    return m.group(1), m.group(2)


def pytest_generate_tests(metafunc) -> None:
    if "model_cfg" in metafunc.fixturenames:
        models = _load_models()
        metafunc.parametrize("model_cfg", models, ids=[_model_id(m) for m in models])


def pytest_runtest_logreport(report) -> None:
    if report.when != "call":
        return
    parsed = _parse_nodeid(report.nodeid)
    if parsed is None:
        return
    model, question = parsed
    if report.passed:
        outcome = "PASS"
    elif report.skipped:
        outcome = "SKIP"
    else:
        outcome = "FAIL"
    _results.append((model, question, outcome))


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    if not _results:
        return

    models = list(dict.fromkeys(m for m, _, _ in _results))
    questions = list(dict.fromkeys(q for _, q, _ in _results))
    lookup = {(m, q): o for m, q, o in _results}

    symbols = {"PASS": "✓", "FAIL": "✗", "SKIP": "-"}
    col_w = max(len(q) for q in questions) + 2
    model_w = max(len(m) for m in models) + 2

    lines = ["\n=== eval results ==="]
    header = f"{'model':<{model_w}}" + "".join(f"{q:^{col_w}}" for q in questions)
    lines.append(header)
    lines.append("-" * len(header))
    for model in models:
        row = f"{model:<{model_w}}"
        for q in questions:
            sym = symbols.get(lookup.get((model, q), "-"), "?")
            row += f"{sym:^{col_w}}"
        lines.append(row)

    terminalreporter.write_sep("=", "eval results")
    for line in lines[1:]:
        terminalreporter.write_line(line)
