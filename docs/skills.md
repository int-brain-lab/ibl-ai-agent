# Skill Layer

The root README now introduces the project for public readers. This page keeps
the detailed skill-layer reference for users and developers who need to
understand how the agent is instructed.

The maintained user interface is the skill layer under `skills/` plus the local
runtime instructions in `AGENTS.md`. The `ibl-ai-agent` CLI and `ibl_ai_agent/ask`
runtime are useful development tools, but they are not the default
scientist-facing workflow.

## Main Skill Modules

- `skills/exploration-confirmation/`: scientific workflow, including
  exploratory analysis, confirmatory analysis, and report writing.
- `skills/ibl-access/`: IBL access modes, authentication, and query guidance.
- `skills/ibl-load/`: local and remote IBL data-loading patterns.
- `skills/ibl-analyze/`: scientific metric semantics, caveats, and analysis
  patterns.
- `skills/ibl-neuropixel/`: raw Neuropixels and SpikeGLX preprocessing guidance.
- `skills/ibl-report/`: report-writing conventions.
- `skills/skill-maintenance/`: maintenance workflow for skill files.

Illustrative empirical-analysis flow (conditional routing and required reads are
defined in [AGENTS.md](../AGENTS.md)):

```text
AGENTS.md -> exploration-confirmation -> ibl-access/ibl-load -> ibl-analyze -> ibl-report
```

These skills define how the coding agent should interpret a question, choose data-loading
patterns, write code, evaluate metrics, and report results.

## Runtime And Developer Tools

The `ibl-ai-agent` CLI exists for runtime experiments, profiling flows,
IBL access checks, and local BWM dataset maintenance.

Useful checks:

```bash
UV_CACHE_DIR=.uv-cache uv run ibl-ai-agent --help
UV_CACHE_DIR=.uv-cache uv run ibl-ai-agent doctor
UV_CACHE_DIR=.uv-cache uv run ibl-ai-agent access check --mode public
```

Developer quality gates:

```bash
UV_CACHE_DIR=.uv-cache uv run ruff check .
UV_CACHE_DIR=.uv-cache uv run pytest -q
```

More details:

- [Architecture](ARCHITECTURE.md)
- [Contributing](CONTRIBUTING.md)
- [Experimental ask/runtime notes](ask/ASK_EXPERIMENTAL.md)
