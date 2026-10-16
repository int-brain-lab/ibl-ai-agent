## Purpose
Short default loading policy for IBL and Brain Wide Map questions.

## Policy
- Default to aggregate-first loading for general IBL data questions.
- For Brain Wide Map questions, follow `bwm_runtime_policy.md` first.
- Use `brainwidemap` as the upstream BWM fallback layer, not as the mandatory opening move.
- Treat session-level ONE objects and `brainbox` loaders as fallback paths.
- Keep generated scripts lean: no CLI flags, no config plumbing, no speculative fallback branches.
- Large reusable datasets are user-local, not repo state. Resolve configured roots with `data_locations.local.yaml` / `ibl_ai_agent.data_locations`; do not assume `reports/datasets`.

## Capability and availability discovery

Run `.venv/bin/python -m ibl_ai_agent.data_discovery` in the project environment for a
fresh overview, or add `--query <broad-term> --json` for focused source evidence.
The command discovers configured datasets through their existing schemas,
reads Parquet headers and sampled shard manifests, and extracts installed
loader docstrings/behavior constants without importing or executing the loaders.
New configured datasets and loader methods appear without updating a catalog.
Check the reported Python executable and `software_inspection` status: a system
interpreter without IBL installed cannot establish missing software capabilities.

Give a short user-facing explanation after this first pass, then inspect only
the relevant producer references, availability tables or local cache evidence.
Search is lexical and shard inspection is sampled: neither establishes absence.
With zero matches, the command returns the unfiltered overview and sets
`query_fallback`; those entries are candidates, not inferred synonyms. Source
reading/parsing failures mark software inspection incomplete and remain visible
in both JSON and readable output, even when a query filters capabilities.
Scientific meaning/units still require source documentation; discovery does not
invent missing definitions. Ignore AppleDouble `._*` files and build checkpoints.

Report the evidence scope: supported by software, packaged in a release, present
locally, listed in a cached index, live availability verified or unknown, and
derivable with analysis. Missing local files or stale index entries do not prove
remote absence. Surface detected-but-unconfigured data without silently changing
configuration. Use live metadata queries only when authorized and permitted by
offline/access rules; capability discovery alone does not authorize downloads.

## Non-BWM default decision path
Use the smallest loading surface that matches the question.

1. Session discovery or dataset availability:
   - follow local capability discovery above; for authorized server-backed discovery use `ONE.search(...)`, `one.list_datasets(...)`, or `one.list_collections(...)`
2. Behavioral trial questions for one or a few sessions:
   - prefer `one.load_object(eid, "trials", attribute=[...])`
3. Session-level neural questions for one or a few probes:
   - prefer probe-scoped ALF objects or `SpikeSortingLoader`
4. Region mapping, acronym conversion, or hierarchy questions:
   - use `BrainRegions` plus `brain_regions_qc.md`
5. Cross-session Brain Wide Map questions:
   - switch to the BWM path below rather than session-by-session loading

Rules:
- Discover first, then load.
- Restrict to needed fields and collections.
- Do not default to live Alyx or remote ONE queries unless the task explicitly needs server-backed discovery or downloads.
- If a question is answerable from a configured local dataset or already-cached files, prefer that path over remote-first examples.
- If a local dataset path is needed but not configured, stop with a setup note pointing to `docs/data_locations.md`.

## High-impact loading defaults
1. Discover first, load second.
2. Load the smallest object that answers the question.
3. Restrict object fields with `attribute=[...]` when possible.
4. Restrict dataset paths with `collection=...` for probe-scoped data.
5. Use `download_only=True` when staging files for later parsing.

## BWM default loading order
Use `bwm_runtime_policy.md` as the canonical BWM loading order. It owns:
- local `bwm_ephys` / `bwm_behavior` schema discovery;
- row-grain table selection;
- upstream helper fallback order;
- rules against invented aggregate tags and speculative fallback branches.

Use `bwm_ephys_spike_example.md` for concrete local spike-shard code, and `bwm_fallback_preflight.md` when a needed field is absent from local BWM or aggregate/unit tables.

## When To Open Extra References
- Use `bwm_paper_repo.md` only when the task specifically depends on upstream paper-helper behavior.
- Use `bwm_fallback_preflight.md` when a required field is absent from `bwm_ephys`, `bwm_units`, or `clusters.pqt`.
- Use `data_loading_live_one.md` for live ONE, Alyx, remote camera access, or server-backed discovery examples.
- Use `data_loading_fallback_examples.md` for local-cache fallback snippets or session-loader fallback examples.
