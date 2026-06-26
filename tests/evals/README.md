# Evals

DeepEval test suite for the IBL AI agent. Tests measure the quality of model outputs
(code generation, skill selection) against known ground truths.

## Setup

**1. API keys** in `.env.local` at the repo root (never committed):
```
ANTHROPIC_API_KEY=sk-ant-...
LITAI_API_KEY=sk-lit-...
```

**2. Model grid** in `tests/evals/models.json` (gitignored):
```json
[
  { "provider": "anthropic", "model": "claude-haiku-4-5-20251001" },
  { "provider": "litai",     "model": "lightning-ai/gpt-oss-20b" }
]
```
Falls back to `models.default.json` (single Haiku) if `models.json` is absent.

## Running the tests

```bash
# Skill selection — no local data required
uv run pytest tests/evals/tier_1_test_skill_selection.py -v

# Code generation — requires bwm_ephys dataset (auto-skipped if absent)
uv run pytest tests/evals/test_codegen.py -v

# All auto-discovered tests
uv run pytest tests/evals/ -v
```

Each test is parametrized over every model in `models.json`, producing one result
per `(model, question)` pair.

### Single model, single question

Use `-k` to filter by any part of the test ID:

```bash
# One model
uv run pytest tests/evals/test_codegen.py -v -k "haiku"

# One question
uv run pytest tests/evals/test_codegen.py -v -k "bwm-neuron-count"

# Both
uv run pytest tests/evals/test_codegen.py -v -k "haiku and bwm-neuron-count"
```

Test IDs follow the pattern `test_codegen[<provider>/<model>-<question-id>]`,
so any substring of the provider, model name, or question id works as a filter.

To push results to Confident AI, set `DEEPEVAL_API_KEY` and use
`uv run deepeval test run` instead of `uv run pytest`.

## Adding questions

Add an entry to the appropriate file in `tests/evals/questions/`:

```json
{
  "id": "my-question-id",
  "question": "...",
  "task": "codegen",
  "packets": ["scientific", "data_loading", "bwm"],
  "requires_data": "bwm_ephys",
  "checks": [
    { "metric": "HierarchyMetric", "stage": "code" },
    { "metric": "NeuronCountMetric", "stage": "output", "expected": { "CB": [7178, 53660] } }
  ]
}
```

Available `packets` are defined in `skill_router.py`. Available `metrics` are in `metrics.py`.
