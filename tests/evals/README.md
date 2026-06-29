# Evals

DeepEval test suite for the IBL AI agent. Tests measure the quality of model outputs
(code generation, skill selection) against known ground truths.

## Setup

**1. API keys** in `tests/evals/.env.local` (never committed):
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

## Dataset setup

`test_codegen.py` requires the `bwm_ephys` dataset (~5 GB). Tests that need it are
auto-skipped when the dataset is absent.

Download it with the bundled script (public S3 bucket, no credentials required):

```bash
python scripts/download_datasets.py
```

The dataset lands at `reports/datasets/bwm_ephys/` by default. To point at an
existing copy elsewhere, create `data_locations.local.yaml` at the repo root:

```yaml
datasets:
  bwm_ephys:
    root: /path/to/your/bwm_ephys
```

## Running the tests

Tests are parametrized over the model grid and spend most of their time waiting
for LLM responses, so running them in parallel with `-n auto` is recommended:

```bash
# Skill selection — no local data required
pytest tests/evals/tier_1_test_skill_selection.py -v -n auto

# Code generation — requires bwm_ephys dataset (auto-skipped if absent)
pytest tests/evals/test_codegen.py -v -n auto

# All auto-discovered tests
pytest tests/evals/ -v -n auto
```

`-n auto` spawns one worker per CPU core. Pass `-n 4` (or any integer) to cap
concurrency — useful when you want to respect API rate limits.

### Logging tiers

On failure, pytest shows two output sections:

| Section | What you see | When |
|---|---|---|
| **Captured stdout** | execution result (neuron counts, etc.) | always |
| **Captured log** | full generated code | `--log-level=DEBUG` |

```bash
# Default — see execution output on failure
pytest tests/evals/test_codegen.py -v -n auto

# Verbose — also see the full generated code and reasoning
pytest tests/evals/test_codegen.py -v -n auto --log-level=DEBUG

# Inline (single-threaded) — stream everything to the terminal live
pytest tests/evals/test_codegen.py -v -s -n0 --log-cli-level=DEBUG
```

Each test is parametrized over every model in `models.json`, producing one result
per `(model, question)` pair.

### Single model, single question

Use `-k` to filter by any part of the test ID:

```bash
# One model
pytest tests/evals/test_codegen.py -v -k "haiku"

# One question
pytest tests/evals/test_codegen.py -v -k "bwm-neuron-count"

# Both
pytest tests/evals/test_codegen.py -v -k "haiku and bwm-neuron-count"
```

Test IDs follow the pattern `test_codegen[<provider>/<model>-<question-id>]`,
so any substring of the provider, model name, or question id works as a filter.

To push results to Confident AI, set `DEEPEVAL_API_KEY` and use
`deepeval test run` instead of `pytest`.

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
