# IBL AI Agent Runtime Instructions

## Scientific Workflow

For empirical scientific analysis projects, follow `skills/exploration-confirmation/SKILL.md`.
Project planning files, data splits, execution preflight, and analysis approval gates apply to these projects. Conceptual scientific answers still require applicable metric semantics and caveats, but no project scaffold or execution preflight. Repository maintenance follows `skills/skill-maintenance/SKILL.md` when changing guidance.

- Exploratory analysis to refine or change the original question in view of the data, define precise hypotheses, and provide preliminary evidence.
- Confirmatory analysis to statistically confirm hypotheses
- Report writing

## Be flexible

The user's original question might not be answerable from the data, or might not be as interesting as something else that comes up in exploration. Feel free to suggest refined or alternative questions to the user.

Scientific metrics have tunable parameters. Pick them carefully from exploratory analysis before before locking for confirmatory tests. Consider grid search or other systematic methods of choice, and explain to the user why you picked these parameters.


## Start small

Don't run big analyses, without first testing on a small scale: test code on individual cells, experiments, etc before running on all data. Perform diagnostics on metrics on a small scale within the exploration set.

Before performing a large run, estimate how long it will take based on previous small scale tests. Consult with the user before starting, presenting acceleration options such as vectorization or parallelization if available.

## Save intermediate results

Save reusable computations such as ACGs or PSTHs in the project's `artifacts/` directory (see Project directory). Keep checkpoints incremental so new computations can be added without rerunning existing ones.

Do this whenever you think the results of a computation will possibly be used again. If you find yourself running a computation twice, that is a sign you should have saved an artifact.

## Keep a running plan

Before starting an empirical analysis project, resolve its directory as described below and create `TODO.md`. Give each step a check box `[ ]`. When a step is complete make it `[X]`, also listing any code and output files generated.

The TODO should also list points when to consult the user for feedback.  Before starting execution, show this plan to the user and ask for feedback, including on how often they want to be consulted. By default, consult the user often during exploratory analysis, and certainly before proceding to confirmatory analysis.  When consulting the user provide plentiful explanatory plots.

Keep a running summary of the explicated question and term definitions in the project's `question.md`.

Both `TODO.md` and `question.md` can be dynamic: as exploration proceeds, the question and TODO items not yet performed can change. But don't change items already completed. Record changes to either file in the project's `change-log.md`.


## Communication style

Be terse. State results and decisions directly; do not restate context or recap what was just done. End-of-turn summaries: 1-2 sentences max.

## Context-window diagnostics

Only mention reading a skill or reference file when it materially changes what the user should expect (large file, changed plan) — not as a routine announcement for every read.

## Required Load Packets

Empirical IBL analysis project:
- `skills/exploration-confirmation/SKILL.md`
- `skills/ibl-analyze/SKILL.md`
- `skills/ibl-report/SKILL.md` when reporting results

Scientific code generation or review:
- `skills/scientific-coding-style/SKILL.md`

Ambiguous scientific metric:
- `skills/ibl-analyze/references/scientific_context_and_metric_semantics.md`
- applicable caveat cards under `skills/ibl-analyze/references/scientific_caveats/`

IBL data loading:
- `skills/ibl-access/SKILL.md` when endpoint, auth, or query mode matters
- `skills/ibl-load/SKILL.md`
- `skills/ibl-load/references/data_loading.md` for non-BWM loading
- `docs/data_locations.md` when local data paths are needed

Brain Wide Map question:
- `skills/ibl-load/references/bwm_runtime_policy.md`
- `skills/ibl-load/references/bwm_ephys_spike_example.md` for local spike-shard code
- `skills/ibl-analyze/references/bwm_analysis_patterns.md`

Anatomical brain atlas navigation or brain region-based visualization:
- `skills/ibl-anatomy/SKILL.md`
- `skills/ibl-anatomy/references/atlas_navigation.md`

Raw Neuropixels or SpikeGLX preprocessing:
- `skills/ibl-neuropixel/SKILL.md`
- `skills/ibl-neuropixel/references/neuropixel_routing.md`

Skill maintenance:
- `skills/skill-maintenance/SKILL.md`

## Project directory

Before starting a scientific analysis, check for an optional repo-root
`ibl-agent.local.yaml`. If present and it defines `project_root`, use that
directory as the project root. Resolve a relative `project_root` against the
repository root.

If no local config is present, use the repository-local `projects/` directory.
All scientific project outputs belong under `<project_root>/<project_slug>/`, and
nowhere else.

- `<project_root>/<project_slug>/question.md` a dynamic document containing the original question, current refined explication, definitions of terms, and definition of exploration and confirmation sets;
- `<project_root>/<project_slug>/TODO.md` for a sequential list of steps performed and planned. Change [ ] to [X] on completion and list output files generated. You can change future plans in the list but do not change descriptions of steps already performed
- `<project_root>/<project_slug>/change-log.md` a list of changes to `question.md` and `TODO.md`, with date-times
- `<project_root>/<project_slug>/artifacts` for things like intermediate npy files for later reuse
- `<project_root>/<project_slug>/exploratory-analyses` for Python files, validation diagnostics, risk notes, and outputs of exploratory analyses
- `<project_root>/<project_slug>/confirmatory-analyses` for python files and outputs of confirmatory analyses
- `<project_root>/<project_slug>/report.qmd` for Quarto source; optional `report.pdf` alongside it when requested
- `<project_root>/<project_slug>/report/` for the rendered HTML report (`report.html`) and required web assets only; keep source documents and private notes outside this publishable directory
- `<project_root>/<project_slug>/instruction-suggestions.md` for proposed durable guidance changes, when useful

## File naming

Within these directories, name python and output files numerically prefixed to indicate turn number, e.g. `000_determine_data_split.py`, `000_determine_data_split.png`, `001_view_firing_rates.py`, `001_view_firing_rates.png`, etc.

## Installation and preflight

- If the user types `install`, read `skills/install/SKILL.md`, and interactively guide the user through the installation process.
- Before execution, check the prerequisites for the activity using `skills/install/SKILL.md`. If a required item is missing, complete that setup before the dependent activity; missing rendering or publishing tools do not block local analysis.

## Runtime Rules
- Run autonomously for repository inspection, planning drafts, and code generation; do not ask the user to run shell commands manually.
- Do not connect to Alyx/ONE or external servers for default free-form questions unless the user asks for execution or live data.
- Use standard IBL APIs and local references: `one.api.ONE`, `SessionLoader`, `SpikeSortingLoader`, and `BrainRegions`.
- Keep scripts minimal: direct imports, constants, linear load -> compute -> summarize -> plot flow.
- If generation partially fails, keep the partial artifact and report what succeeded/failed.

## Brain Wide Map Defaults

For BWM questions:
- resolve local dataset roots from `data_locations.local.yaml`, a project-level `data_locations.local.yaml`, or `IBL_AGENT_DATA_LOCATIONS`;
- inspect configured `bwm_ephys` and `bwm_behavior` schemas before choosing a loading path;
- prefer the newest semantically sufficient user-local dataset surface;
- use local-dataset scripts over remote-loading scripts when fields are present;
- mention dataset path/version in methods or caveats;
- default to single-agent adversarial review before code and after code/results when feasible;
- use subagents only when the user explicitly asks for subagents, delegation, parallel reviewers, or equivalent wording such as `use strategy review rounds with adversarial subagents`.


## Scope Boundaries

- Do not require template scaffolds for free-form scientific answers.
- Browse online only when necessary for API drift, missing local references, or explicit user request.
