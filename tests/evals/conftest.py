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
from pathlib import Path

_DIR = Path(__file__).parent


def _load_models() -> list[dict]:
    for path in (_DIR.joinpath("models.json"), _DIR.joinpath("models.default.json")):
        if path.exists():
            return json.loads(path.read_text())
    return [{"provider": os.getenv("ACTOR_PROVIDER", ""), "model": os.getenv("ACTOR_MODEL", "")}]


def _model_id(cfg: dict) -> str:
    parts = [cfg.get("provider", ""), cfg.get("model", "")]
    return "/".join(p for p in parts if p)


def pytest_generate_tests(metafunc) -> None:
    if "model_cfg" in metafunc.fixturenames:
        models = _load_models()
        metafunc.parametrize("model_cfg", models, ids=[_model_id(m) for m in models])
