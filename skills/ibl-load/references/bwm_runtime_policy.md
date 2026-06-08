## Purpose
Canonical runtime policy for Brain Wide Map questions.

## Scope
Use this as the shared policy reference for:
- `ibl-load`
- `ibl-analyze`
- `scientific-analysis`

## Core policy
1. For Brain Wide Map questions, start by resolving user-local BWM dataset roots from `data_locations.local.yaml`, project-level `data_locations.local.yaml`, `IBL_AGENT_DATA_LOCATIONS`, or the repo default `reports/datasets/<dataset_name>` location, then check schemas before choosing a loading path.
2. Treat `brainwidemap` as the canonical upstream fallback layer, not as the mandatory opening move.
3. When a configured frozen derived BWM dataset is present and sufficient for the question, prefer it before remote aggregate downloads or repeated release-table joins.
   - Preferred local derived ephys surface for new work: newest semantically sufficient configured `bwm_ephys` dataset.
   - Preferred local derived behavior surface for movement, wheel, pose, and trial-behavior work: newest semantically sufficient configured `bwm_behavior` dataset.
   - Treat these datasets as user-local artifacts, not as committed repo state or canonical upstream package interfaces.
4. Start with local derived BWM datasets when their schemas cover the question, then release/freeze helpers and aggregate tables if local derived features are insufficient.
5. Use session-level loaders only when the requested metric cannot be obtained from `bwm_ephys`, canonical helpers, or aggregate fields.
6. If `brainwidemap` is required for a missing field and unavailable, fail fast with a clear error; do not generate fallback logic.
7. If `bwm_ephys` or `bwm_behavior` is semantically sufficient but unavailable, **stop before using ONE, OpenAlyx, `SessionLoader`, or `SpikeSortingLoader`**. Ask the user whether to download or configure the local dataset.

Use plain language like:

```text
I should use the local compressed <dataset_name> dataset for this task because <why it is sufficient>.
I do not find it at the expected location: reports/datasets/<dataset_name>.

I can download/configure it before analysis. The public archive is about <size> and will be stored by default under reports/datasets/<dataset_name>, with data_locations.local.yaml pointing to it.

Alternatives: point me to an existing local copy, use a different configured project data root, or explicitly allow a slower ONE/session-loader path if the local dataset lacks a required field.
```

Do not run the downloader or remote fallback until the user has made that choice.

## Local dataset discovery
Before planning a BWM analysis, load the user's data-location config. See `docs/data_locations.md`.

Resolution order:
1. explicit `IBL_AGENT_DATA_LOCATIONS`;
2. nearest `data_locations.local.yaml` found from the current working directory upward;
3. nearest `data_locations.yaml` found from the current working directory upward.
4. repo default `reports/datasets/<dataset_name>` when no config entry is present and a valid versioned dataset with `schema.yaml` exists.

Configured roots:
- `datasets.bwm_ephys.root`
- `datasets.bwm_behavior.root`

Each root may be a directory containing version folders with `schema.yaml`, or one exact dataset directory containing `schema.yaml`. Do not assume `reports/datasets` exists.

Before planning a BWM analysis, inspect available configured dataset versions under:
- `<bwm_ephys.root>/*/schema.yaml` or `<bwm_ephys.root>/schema.yaml`
- `<bwm_behavior.root>/*/schema.yaml` or `<bwm_behavior.root>/schema.yaml`

If no local data location is configured and the question can use the public BWM
derived datasets, tell the user that you are about to download them with
`UV_CACHE_DIR=.uv-cache uv run python scripts/download_datasets.py`, state that
`bwm_behavior` is about 3.5 GB and `bwm_ephys` is about 5 GB, state that the
archives will be extracted under `reports/datasets/` and configured in
`data_locations.local.yaml`, and give the user a chance to stop before running
the script. If `data_locations.local.yaml` already contains manual BWM roots, do
not overwrite it; report missing or invalid schemas instead.

Prefer the newest version whose schema directly covers the scientific quantity. Do not hard-code an older version when a newer local version exposes the same fields plus relevant extensions.

Use dataset surfaces by semantic domain:
- `bwm_ephys`: units, insertions, sessions, regions, good-unit spike shards, task event-response features, passive ephys features.
- `bwm_behavior`: trial behavior, wheel movement features, movement/quiescence state epochs, pose features, behavioral event-aligned features. Current schemas may expose pose tables with legacy `dlc_*` names.

If one answer needs both ephys and behavior surfaces, join only through stable keys such as `eid`, `trial_id`, `pid`, and `cluster_id`, and state the join grain in Methods.

## Table-surface selection
Choose the table whose row grain matches the question:
- insertion-level questions: `metadata/insertions.parquet`
- session-level questions: `metadata/sessions.parquet`
- unit-level questions: `metadata/units.parquet` plus `features/unit_features.parquet`
- task event-response unit metrics: `features/event_response_features.parquet`
- passive ephys response metrics: newest sufficient `bwm_ephys` passive tables
- behavioral/wheel state metrics: newest sufficient `bwm_behavior` trial, wheel, movement/quiescence state, and event-aligned behavior tables

Do not derive insertion/session counts from lower-grain tables unless the matching higher-grain table is missing or insufficient. If lower-grain derivation is used, document the omitted zero-row entities.

## Preferred loading order
1. newest configured local `bwm_ephys` and/or `bwm_behavior` dataset when present and sufficient
2. `ibl_ai_agent.datasets.bwm_ephys.load_spike_shard(...)` plus `metadata/*.parquet` for local good-unit spike/event analyses
3. `bwm_query(...)` for the release roster and probe/session metadata
4. `bwm_units(...)` for the canonical unit table
5. `download_aggregate_tables(...)` for release-wide parquet tables
6. `load_good_units(...)` and `load_trials_and_mask(...)` only when `bwm_ephys` is missing a required field or the user explicitly wants upstream helpers
7. `SessionLoader` / `SpikeSortingLoader` only for narrow missing-field fallbacks

## Aggregate-first rule
- Broad cross-region and cross-session questions should start from release-scale tables.
- Do not jump to `SessionLoader` / `SpikeSortingLoader` for convenience.
- If raw/session loading is used, state which missing field forced that decision.
- If `bwm_ephys` already contains the needed fields, prefer it over reconstructing the same joins from upstream aggregates.
- For local spike analyses, prefer `metadata/units.parquet` plus `spikes/<pid>/...` over `load_good_units(...)` when the local shard schema covers the task.
- Treat `bwm_ephys` spike shards as already filtered to good units for each insertion.

## Fallback rule
- Do not generate generic Alyx aggregate or local-dataframe fallback branches for BWM questions.
- Do not generate per-probe `one.load_dataset(...)` loops when the local ONE cache path is deterministic from `bwm_query(...)` metadata.
- If a narrow fallback metric already exists in the local cache as probe files, prefer direct local reads over repeated dataset resolution.

## Reproducibility rule
- Record release freeze, aggregate table path/tag, and any revision assumptions.
- Do not invent unverified aggregate tags or dataset names.
- When using `bwm_ephys`, record the configured dataset path/version and note that it packages release metadata with good-unit local spike shards and derived features.

## Local rebuild note
- `bwm_ephys` is the preferred local ephys dataset for new BWM work. Rebuild it with:
  - `uv run ibl-ai-agent build-bwm-ephys-dataset`
- Use an output root outside this repository for durable local data, then record that root in `data_locations.local.yaml`.
- Default cache root for ONE data is often `~/Downloads/ONE`; record non-default cache roots in `data_locations.local.yaml`.
- Rebuild the intended local dataset before inventing alternate BWM loading branches.

## Code-generation rule
- Keep scripts lean: no CLI flags, no config layers, no notebook-first scaffolding.
- Prefer a linear load -> compute -> summarize -> plot structure.
- Include the original question in the top-level docstring for standalone scripts.

## Quality gates
- Reject plans that bypass adequate configured local BWM dataset paths for BWM questions.
- Reject plans that ignore `bwm_behavior` when the requested quantity depends on wheel, movement state, quiescence, pose, or trial-behavior features that are present locally.
- Reject plans that answer a literal movement-versus-quiescence question with movement-onset event-response features when local behavior-state epoch tables are present.
- Reject plans that use a table with the wrong row grain when a correct-grain table is present.
- Reject plans that use raw/session loading without first checking aggregate/release helpers.
- Reject fallback branches that exist only because session loaders are more familiar.
