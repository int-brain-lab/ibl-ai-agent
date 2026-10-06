# Experimental Ask Runtime

This document collects the `ask` runtime and CLI material that used to live in the top-level README.

The `ibl_ai_agent/ask` stack is experimental. The main intended interface for this repository is still Codex + local skills, not direct CLI usage.

## Quickstart

### 1. Install

```bash
UV_CACHE_DIR=.uv-cache uv sync --extra ibl --extra notebook
```

### 2. Verify runtime health

```bash
UV_CACHE_DIR=.uv-cache uv run ibl-ai-agent doctor
```

### 3. Ask one scientific question in Codex

Open Codex in this repository and enter only your question, for example:

```text
Across PO, LP, and LGv, which region shows the shortest visual response latency and how does median firing rate differ between regions?
```

### 4. Open generated notebooks

```bash
just jupyter
```

Open:

```text
http://127.0.0.1:8888/lab/tree/reports/ask_runs/<run_id>/notebooks/analysis.ipynb?token=<token>
```

`run_id` is printed by `ibl-ai-agent ask`; `token` is printed by `just jupyter`.

## How It Works

1. Codex interprets the question and builds a question-specific plan from local skills.
2. `ibl-ai-agent ask` defaults to `plan_only`: it renders the notebook/code without executing cells.
3. The runtime saves artifacts under `reports/ask_runs/<run_id>/`.
4. Codex returns a concise scientific answer plus methods/caveats and notebook link.

Execution entrypoint (default draft mode):

```bash
UV_CACHE_DIR=.uv-cache uv run ibl-ai-agent ask --plan-file /tmp/plan.yaml "your question"
```

Execute notebook only when explicitly requested:

```bash
UV_CACHE_DIR=.uv-cache uv run ibl-ai-agent ask --runtime-mode full --execute-notebook --plan-file /tmp/plan.yaml "your question"
```

## Expected Outputs

Each run writes:

- `reports/ask_runs/<run_id>/notebooks/analysis.ipynb`
- `reports/ask_runs/<run_id>/notebooks/analysis.html`
- `reports/ask_runs/<run_id>/analysis_code.py`
- `reports/ask_runs/<run_id>/plan.yaml`
- `reports/ask_runs/<run_id>/plan_full.yaml`
- `reports/ask_runs/<run_id>/ask_manifest.yaml`
- `reports/ask_runs/<run_id>/answer.md`
- `reports/ask_runs/<run_id>/analysis_result.json` (typically absent in `plan_only` mode)

Success checklist:

- prose answer is present,
- notebook is editable in JupyterLab,
- analysis code is question-specific and auditable,
- methods/caveats are explicit.

## Advanced Ask Usage

Generate notebook without executing (fast draft mode):

```bash
just ask-draft-open "Across PO, LP, and LGv, which region shows the shortest visual response latency and how does median firing rate differ between regions?"
```

Create and validate a plan payload manually:

```bash
UV_CACHE_DIR=.uv-cache uv run ibl-ai-agent plan create "your question" --out /tmp/plan.yaml
UV_CACHE_DIR=.uv-cache uv run ibl-ai-agent plan validate --plan-file /tmp/plan.yaml
UV_CACHE_DIR=.uv-cache uv run ibl-ai-agent ask --plan-file /tmp/plan.yaml --execution-backend auto "your question"
```

Execution backend controls:

- `--runtime-mode plan_only|full` (default `plan_only`)
- `--no-sessions-hint` to ignore manifest `sessions.yaml` and force EID discovery
- `--execution-backend cli-local|local|mcp|auto` (`auto` tries `cli-local -> local -> mcp`)
- `IBL_AGENT_NOTEBOOK_EXEC_CMD="jupyter nbconvert --to notebook --execute --inplace {notebook}"`
- `IBL_AGENT_JUPYTER_MCP_EXEC_CMD="jupyter-mcp-server execute-notebook {notebook}"`
- `IBL_AGENT_JUPYTER_BASE_URL` (default `http://127.0.0.1:8888`)
- `IBL_AGENT_JUPYTER_ROOT` (default repository root)
- `IBL_AGENT_DISABLE_SESSIONS_HINTS=1` to hard-disable frozen sessions hints
- `IPYTHONDIR=.ipython`, `MPLCONFIGDIR=.mplconfig`, `JUPYTER_RUNTIME_DIR=.jupyter_runtime`

## Troubleshooting

`Codex asks for permission before running uv`

Use workspace-local cache to avoid writes to `~/.cache/uv`:

```bash
UV_CACHE_DIR=.uv-cache uv run ibl-ai-agent ask --plan-file /tmp/plan.yaml "your question"
```

`Notebook dependencies missing`

Install notebook extras:

```bash
UV_CACHE_DIR=.uv-cache uv sync --extra notebook
```

`ONE/Alyx authentication failed`

`ask` defaults to strict live auth mode:

```bash
UV_CACHE_DIR=.uv-cache uv run ibl-ai-agent access check --mode public --interactive
```

Full execution requires live ONE/Alyx access.

`Data-offline mode blocks ask`

`IBL_AGENT_DATA_OFFLINE=1` disables remote Alyx/ONE access, including the `access check` command. Use the default `plan_only` mode, or clear the flag.

`Notebook URL opens the wrong Jupyter server`

Set `IBL_AGENT_JUPYTER_BASE_URL` and retry.

`Notebook path is wrong under /lab/tree/...`

Set `IBL_AGENT_JUPYTER_ROOT` and retry.

## Related Docs

- [Ask runtime contract](./ASK_RUNTIME.md)
- [Architecture](../ARCHITECTURE.md)
- [Data locations](../data_locations.md)
