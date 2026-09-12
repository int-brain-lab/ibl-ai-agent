# Scientific Workflow

This page preserves the detailed scientist-facing workflow that previously lived
in the root README. The root README is now the public landing page; this file is
the reference for how an interactive analysis should proceed.

## Default Interaction

Most users interact with this repository through a coding agent, usually the
Codex CLI, rather than by calling the `ibl-ai-agent` CLI directly.

Open the agent in the repository root, ask one focused scientific question,
review the exploratory plan, and let the agent create a project under the
configured project root (default `projects/`).

Example questions:

```text
Across PO, LP, and LGv, which region shows the shortest visual response latency?
```

```text
How does the number of good neurons per probe insertion vary between labs?
```

Good prompts are focused: name the region, metric, comparison, data scope, or
event alignment you care about. Avoid bundling several unrelated scientific
questions into one prompt.

## Audience

This workflow is intended for:

- IBL scientists asking exploratory or confirmatory data-analysis questions.
- Researchers who want short, inspectable Python analyses rather than opaque
  notebook sprawl.
- Developers maintaining Codex skills, profile tasks, or dataset-building
  support for IBL workflows.

## How It Works

For an empirical scientific analysis project, the agent is expected to follow an
exploration-confirmation workflow:

1. Explicate the question, terms, candidate metrics, data scope, event anchors,
   row grain, quality-control rules, and independent statistical unit.
2. Write or update project state in `question.md`, `TODO.md`, and
   `change-log.md`.
3. Start small by testing loading, metrics, and diagnostics on individual cells,
   sessions, probes, or other small units before scaling up.
4. Run exploratory analysis to refine the question and choose tunable metric
   parameters.
5. Validate ambiguous, custom, proxy, event-aligned, trial-aligned, neural,
   behavioral, or state metrics with inspectable diagnostic plots.
6. Ask for user feedback before locking the question, metric, data scope, or
   confirmatory plan.
7. Run confirmatory analysis only after exploratory choices are fixed.
8. Write a report with methods, caveats, figures, statistical results, and links
   to generated artifacts.

Conceptual answers use applicable scientific semantics and caveats without
requiring project files, data splits, or execution preflight. Required tools are
checked for the activity being performed, as defined in
[the installation skill](../skills/install/SKILL.md).

## Typical Agent Session

Start the coding agent from the repository root and ask the scientific question
directly. The agent reads `AGENTS.md` and the relevant skill files to decide how to
load data, define metrics, run analyses, and report results.

Typical interaction:

1. You ask a focused question.
2. The agent writes a project plan and question summary.
3. You review the plan and adjust scope or definitions if needed.
4. The agent performs exploratory diagnostics and shows plots.
5. You approve or revise the confirmatory plan.
6. The agent runs the confirmatory analysis and writes the final report.

For Brain Wide Map questions, local derived datasets are preferred when they are
configured and semantically sufficient. If they are missing and no manual data
location has been configured, the agent should offer the public BWM download,
state its size and fixed `reports/datasets/` destination, and obtain authorization.
An already approved download does not need another approval. An existing copy
elsewhere can be configured instead. See the
[BWM policy](../skills/ibl-load/references/bwm_runtime_policy.md).

If you want independent multi-agent review, ask explicitly, for example:

```text
use strategy review rounds with adversarial subagents
```

## Project Outputs

Scientific work should be saved under one project directory. Resolve
`project_root` from the optional repo-root `ibl-agent.local.yaml`, relative to
the repository if needed; otherwise use `projects/`. The canonical contract is
in [AGENTS.md](../AGENTS.md).

```text
<project_root>/<project_slug>/
  question.md
  TODO.md
  change-log.md
  artifacts/
  exploratory-analyses/
  confirmatory-analyses/
  report.qmd
  report.pdf  # optional; PDF-only requests omit report/
  report/
    report.html
    ...required web assets...
  instruction-suggestions.md  # optional, private guidance proposals
```

Use these files for persistent scientific state:

- `question.md`: original question, refined question, term definitions, data
  scope, exploration set, and confirmation set.
- `TODO.md`: running plan with checkboxes, generated files, and points where the
  user should be consulted.
- `change-log.md`: dated changes to `question.md` and `TODO.md`.
- `artifacts/`: reusable intermediate arrays, tables, cached metrics, and other
  checkpoints.
- `exploratory-analyses/`: scripts, figures, and outputs used to refine the
  question or metrics.
- `confirmatory-analyses/`: locked analysis scripts and statistical outputs.
- `report.qmd`: Quarto source, outside the publishable directory.
- `report.pdf`: optional additional output, or the sole rendered output for an
  explicit PDF-only request. GitHub Pages publication requires an HTML report.
- `report/`: rendered HTML and required web assets only. The publisher collects
  web files recursively from this directory; do not pass the whole project.
- `instruction-suggestions.md`: optional proposals for durable guidance changes.

The [experimental ask runtime](ask/ASK_RUNTIME.md) uses `reports/ask_runs/` when
explicitly requested; it is separate from the scientific project workflow.

## Current Boundaries

- Use a coding agent that can read `AGENTS.md` and referenced files, edit files,
  run shell commands, and pause for scientific feedback. Provider access and
  payment requirements depend on the chosen agent.
- Use local BWM datasets and local ONE caches when configured and sufficient.
- Use the CLI for diagnostics, development, profiles, and dataset maintenance.
- Do not commit large datasets to the repository.
- Treat `ibl_ai_agent/ask` and `reports/ask_runs/` as experimental unless you are
  working specifically on that runtime path.
- Other coding agents may work with minor modifications, but that path is less
  tested.
