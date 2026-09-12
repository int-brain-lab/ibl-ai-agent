---
name: install
description: Use this skill to set up this repository or check prerequisites for analysis, data loading, report rendering, or publishing.
---

# Preflight and Installation

- Before the first execution of an activity, check its prerequisites: usable Python, `uv`, the project environment and required imports for analysis; configured local data or IBL access for loading; Quarto for report rendering; `git`, `gh`, and `gh auth status` for GitHub Pages publishing. Recheck if the environment changes or a check fails.
- If a required item is missing, stop the dependent activity and complete setup interactively. Do not require rendering or publishing tools for local analysis, or an execution environment for conceptual answers and read-only repository inspection.
- Instead, interactively lead the user through installation of missing items, using `uv` unless explicitly instructed otherwise. Explain the missing item(s) and offer to install or configure them. Use tool calls to run the needed commands after approval when network, package installation, or external authentication is required.
- Prefer `uv` for project setup. If `uv` is missing but Python is available, offer to install `uv`, then run `UV_CACHE_DIR=.uv-cache uv sync --extra ibl --extra notebook --extra dev`. Use `uv run ...` for project commands once the environment exists. Do not recommend plain `pip install -e .` as the primary setup path because `brainwidemap` is resolved through `uv` sources.
- For some packages such as Quarto, `git`, and `gh`, automatic installation may be difficult. In such cases, guide the user to appropriate websites to manually download and install what is needed.
- For BWM data discovery, download authorization, and the downloader's `reports/datasets/` destination, follow `../ibl-load/references/bwm_runtime_policy.md`. An existing dataset elsewhere can be configured in `data_locations.local.yaml`; do not promise a custom downloader destination.
