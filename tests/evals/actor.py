"""Actor model factory for DeepEval evaluations.

The "actor" is the model under test — the one generating the actual output
that DeepEval then evaluates. This is distinct from the evaluator model, which
grades outputs inside LLM-based metrics (configured separately via OPENAI_API_KEY
or by passing ``model=`` to a metric).

Model sources (in priority order):
1. ``model_cfg`` dict passed directly (used by the grid parametrize fixture)
2. ``ACTOR_PROVIDER`` / ``ACTOR_MODEL`` env vars (CLI / single-model runs)

``model_cfg`` dict fields:
    provider  one of "anthropic", "litai", or "" (deepeval default / OpenAI)
    model     model name string; provider-specific default used when absent

Examples
--------
ACTOR_PROVIDER=anthropic ACTOR_MODEL=claude-sonnet-4-6 uv run pytest tests/evals/
ACTOR_PROVIDER=litai ACTOR_MODEL=lightning-ai/gpt-oss-20b uv run pytest tests/evals/
"""

from __future__ import annotations

import os

from deepeval.models.base_model import DeepEvalBaseLLM


_DEFAULTS = {
    "anthropic": "claude-sonnet-4-6",
    "litai": "lightning-ai/gpt-oss-20b",
}


def get_actor(model_cfg: dict | None = None) -> tuple[DeepEvalBaseLLM, None]:
    """Return ``(model, None)`` — a drop-in replacement for ``initialize_model()``.

    Parameters
    ----------
    model_cfg
        Optional dict with ``provider`` and ``model`` keys.  When provided it
        takes precedence over environment variables.
    """
    cfg = model_cfg or {}
    provider = (cfg.get("provider") or os.getenv("ACTOR_PROVIDER", "")).lower()
    model_name = cfg.get("model") or os.getenv("ACTOR_MODEL", "")

    if provider == "anthropic":
        from deepeval.models.llms.anthropic_model import AnthropicModel
        return AnthropicModel(
            model=model_name or _DEFAULTS["anthropic"],
            generation_kwargs={"max_tokens": 8192, "timeout": 120},
        ), None

    if provider == "litai":
        from deepeval.models import GPTModel
        model_id = model_name or _DEFAULTS["litai"]
        # Newer OpenAI models reject max_tokens; non-OpenAI models may not support max_completion_tokens
        token_kwarg = "max_completion_tokens" if model_id.startswith("openai/") else "max_tokens"
        return GPTModel(
            model=model_id,
            api_key=os.getenv("LITAI_API_KEY"),
            base_url="https://lightning.ai/api/v1/",
            generation_kwargs={token_kwarg: 8192, "timeout": 120},
        ), None

    # Default: deepeval's built-in model (reads OPENAI_API_KEY)
    from deepeval.metrics.utils import initialize_model
    return initialize_model()
