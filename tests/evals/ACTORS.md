# Actors: one interface over three model providers

`test_dataloading.py` runs every question in `questions/` against every model in
the grid. Models are reached through the `actors/` package, which gives three
different provider architectures the same interface.

## The hierarchy

```
Actor                       run(prompt) -> RunResult(reply, usage)
├── CodeExecutingActor
│   └── ClaudeSDKActor      local Claude Code agent (claude-agent-sdk)
└── ChatCompletionActor     chat endpoint + a run_python tool loop
    ├── LightningActor      OpenAI-compatible API
    └── MistralActor        Mistral SDK
```

Claude arrives as an agent with its own tools. Mistral and Lightning are chat
endpoints, so they are given one tool — `run_python` (see `actors/tools.py`) —
and a loop: call the model, run the code it asks for, feed the output back,
repeat until it answers or hits `max_turns`. Without that, a data-loading
question would be unanswerable for them and the comparison meaningless.

`executes_code` says which kind an actor is. Chat actors built with
`"tools": false` keep the interface but lose the tool, which is the no-data
baseline.

## Why not deepeval's model classes

`DeepEvalBaseLLM.generate` returns a string. It has nowhere to carry token
usage, which these evals must report, and no way to express an agent session or
a tool loop. Deepeval still owns everything above the model: `LLMTestCase`,
metrics, `assert_test`, and the pass/fail grid in `conftest.py`.

## Adding a provider

Add one `Actor` subclass and one entry in `PROVIDERS` (`actors/__init__.py`).
Chat providers implement three small methods — `_complete`, `_assistant_message`,
`_tool_message` — because providers differ in how they encode tool calls. The
loop itself is shared in `base.py`.

## Running

```bash
cp tests/evals/models.grid.json tests/evals/models.json    # 15-model grid
UV_CACHE_DIR=.uv-cache uv run --no-sync pytest tests/evals/test_dataloading.py -q -n 5
UV_CACHE_DIR=.uv-cache uv run --no-sync python tests/evals/summarize_results.py
```

Each run appends to `results/usage.csv` as it goes, so an interrupted grid keeps
its completed rows. `summarize_results.py` turns that into `results/summary.md`.

Unit tests for the actors mock every API call and need no keys or network:

```bash
UV_CACHE_DIR=.uv-cache uv run --no-sync pytest tests/evals/test_actors.py -q
```

## Reading results

`stop_reason` records how each run ended, because failures that score the same
are not the same result:

- `answered` — the model finished and gave an answer (right or wrong)
- `max_turns` — it kept working and ran out of turns
- a provider reason such as `malformed_function_call` — nothing usable came back

## Keys and models

Keys go in `.env.local` (gitignored): `MISTRAL_API_KEY`, `LITAI_API_KEY`. Claude
uses the local `claude login` session; on CI, `ANTHROPIC_API_KEY` works with the
same code.

Lightning keys are scoped: `models.list()` returns more models than a key may
call, and an unauthorised one fails at call time. `models.grid.json` lists models
verified callable on the team keys as of 2026-10-05 — note that the older
`models.default.json` lists several that are not.

## Provider quirks worth knowing

- `mistralai` 3.x exposes `mistralai.client.Mistral`, not `mistralai.Mistral`.
- Newer OpenAI-lineage models require `max_completion_tokens`; others need `max_tokens`.
- Reasoning models return `content=None` when the budget is spent on reasoning.
- Mistral tool results need the tool `name`; OpenAI ones need `type: "function"`.
