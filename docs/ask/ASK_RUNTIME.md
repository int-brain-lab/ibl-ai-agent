# Ask Runtime

## Entry points

- API: `ibl_ai_agent.ask.run_ask(...)`
- CLI: `ibl-ai-agent ask "<question>" --plan-file <plan.yaml>`
- Preflight: `ibl-ai-agent doctor`

## Runtime flow

1. Build planning/execution contexts from question + frozen manifest.
2. Load and validate injected plan payload.
3. Build structured plan via planner backend registry.
4. Validate generated analysis code.
5. If `runtime_mode=full`, run strict preflight checks (deps, writable runtime dirs, ONE/Alyx auth).
6. Render notebook and export HTML.
7. If `runtime_mode=full` and execution is enabled, execute backend and extract typed result payload (`analysis_result.json`).
8. Persist manifest + answer + plan/artifact files.

## Reliability contracts

- Default runtime mode is `plan_only` (no preflight auth/network access, no notebook execution).
- In `runtime_mode=full`, default auth mode is strict live ONE/Alyx for free-form `ask`.
- `auto` execution backend order is `cli-local -> local -> mcp`.
- Repo-local runtime dirs are enforced via env vars:
  - `UV_CACHE_DIR=.uv-cache`
  - `IPYTHONDIR=.ipython`
  - `MPLCONFIGDIR=.mplconfig`
  - `JUPYTER_RUNTIME_DIR=.jupyter_runtime`

If notebook execution fails, runtime fails result extraction.

## Key modules

- `app/orchestrator.py`: top-level ask use-case.
- `app/planner.py`: planner backend protocol/registry.
- `app/notebook_execution.py`: notebook executor backend protocol/registry.
- `app/pipeline.py`: notebook render/execute/result extraction pipeline.
- `app/context_builder.py`: context/filters + initial phase state.
- `domain/contracts.py`: plan/manifest/result/phase schemas.
- `domain/state_machine.py`: typed phase transitions.
- `infra/artifacts.py`: run ID and artifact path lifecycle.
- `infra/persistence.py`: manifest + run file persistence.

## Invariants

- Runtime never reads/writes planning prompts from `skills/` at execution time.
- Runtime never calls planner model providers directly.
- `phase_status` is always validated against `AskPhaseMap` contract.
- Notebook backend selection is backend-registry based, not branch-only ad-hoc routing.
