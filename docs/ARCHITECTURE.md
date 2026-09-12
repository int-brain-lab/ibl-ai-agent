# Experimental Ask Runtime Architecture

The normal scientist-facing workflow uses `AGENTS.md` and the [skill layer](skills.md).
This page describes `ibl_ai_agent/ask`, the experimental execution runtime for
skill-generated analysis plans, and its supporting CLI modules.

## Core runtime split

- Skills (`skills/*`) own planning intent and scientific workflow guidance.
- Runtime (`ibl_ai_agent/ask`) owns deterministic execution and artifact persistence.

## Ask package layers

`ibl_ai_agent/ask` is split into 3 layers:

- `domain/`: typed contracts, config/result models, phase state machine.
- `app/`: orchestration use-cases (context building, planner backend registry, notebook execution pipeline).
- `infra/`: filesystem IO and artifact persistence services.

Boundary rules:

- `domain` must not import `app` or `infra`.
- direct writes should stay in `infra` modules.
- runtime modules must not import model/planner network SDKs (`openai`, `anthropic`, etc.).

These are enforced by tests in `tests/test_ask_runtime_guard.py`.

## Ask execution model

- Runtime is execution-only: a plan payload must be provided (`--plan-file` or injected plan object).
- Planner and notebook execution each use registry-based backend interfaces.
- Phase transitions are explicit, typed, and immutable-style (`domain/state_machine.py`).

## Artifact contract

Each ask run writes to `reports/ask_runs/<run_id>/`:

- notebook: `notebooks/analysis.ipynb`
- html preview: `notebooks/analysis.html`
- manifest: `ask_manifest.yaml`
- answer: `answer.md`
- plan artifacts: `plan.yaml`, `plan_full.yaml`, `analysis_code.py`, `outputs.yaml`

`run_id` naming/path creation is centralized in `infra/artifacts.py`.

## Non-ask modules

- `ibl_ai_agent/core`: IBL access logic.
- `ibl_ai_agent/commands`: thin CLI argument mapping to runtime and core functions.
