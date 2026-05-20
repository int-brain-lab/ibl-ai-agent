---
name: install
description: Use this skill if the required tools are not yet installed.
---

# Preflight and Installation

- Before the first research, report-writing, or publishing task in a fresh checkout, run a small setup preflight yourself. Check for a usable Python, `uv`, a project environment, core Python imports, Quarto for report rendering, Git/GitHub CLI for publishing, GitHub authentication when publishing is requested, and configured IBL data access or local dataset paths when data loading is needed.
- If any of these items are missing **STOP**, do not proceed to scientific analyses. Even if the user has asked a scientific question, do not proceed with scientific analyses unless the user gives you explicit instructions to do so rather than completing installation.
- Instead, interactively lead the user through installation of missing items, using `uv` unless explicitly instructed otherwise. Explain the missing item(s) and offer to install or configure them. Use tool calls to run the needed commands after approval when network, package installation, or external authentication is required.
- Prefer `uv` for project setup. If `uv` is missing but Python is available, offer to install `uv`, then run `UV_CACHE_DIR=.uv-cache uv sync --extra ibl --extra notebook --extra dev`. Use `uv run ...` for project commands once the environment exists. Do not recommend plain `pip install -e .` as the primary setup path because `brainwidemap` is resolved through `uv` sources.
- Verify Quarto, `git`, `gh`, and `gh auth status`, which are required for report publication.
- For some packages such as Quarto, `git`, and `gh`, automatic installation may be difficult. In such cases, guide the user to appropriate websites to manually download and install what is needed.
- If the BWM data shards are not yet present, ask the user if these files are already present on disk. If so, edit `data_locations.local.yaml` to point to them; if not, ask the user for a location to download to (defaulting to `data/` within the current repo), download, then edit `data_locations.local.yaml`.
