---
name: ibl-load
description: Use this skill when loading IBL data, preferring the smallest sufficient local or aggregate surface before session-level loaders.
---

# IBL Load

## Use this skill when
- A scientific question requires neural or behavioral data loading.
- You need to choose between configured local datasets, aggregate/release tables, ONE objects, and session-level loaders.
- You need BWM loading policy.

## References
- `references/data_loading.md`: short default loading policy.
- `references/bwm_runtime_policy.md`: canonical Brain Wide Map loading policy.
- `references/bwm_fallback_preflight.md`: required preflight when a BWM field is absent from local/aggregate tables.
- `references/brain_regions_qc.md`: spike sorting loaders, atlas mapping, region/QC handling, and plotting prep.
- `references/bwm_ephys_spike_example.md`: concrete local spike-shard example.
- `references/repeated_site_pids.md`: how to find repeated site PIDs, sometimes useful for exploratory analyses
- `../../docs/data_locations.md`: machine-local data-root configuration.

Supplemental references:
- `references/ibl_behavior_task.md`
- `references/bwm_release_scope.md`
- `references/bwm_paper_repo.md`
- `references/data_loading_live_one.md`
- `references/data_loading_fallback_examples.md`

## Workflow
1. For Brain Wide Map questions, follow `references/bwm_runtime_policy.md`.
2. For non-BWM questions, use `references/data_loading.md`.
3. Load the smallest surface that answers the question; restrict attributes, collections, and fanout.
4. Resolve large dataset roots from `data_locations.local.yaml`; do not assume reusable data live in this repo.
5. Prefer configured local datasets and cached files over remote-first examples when present and sufficient.
6. Use session-level loaders only when the requested metric cannot be obtained from local datasets, aggregate tables, or narrow ONE objects.
7. Record release freeze, local dataset path/version, data scope, and relevant cache/revision assumptions.

## Quality gates
- Reject BWM plans that bypass an adequate local `bwm_ephys` or `bwm_behavior` dataset.
- Reject table choices with the wrong row grain when a correct-grain table exists.
- Reject generic fallback branches and high-fanout `one.load_dataset(...)` loops when a stable narrow local path is available.
- Fail fast on missing critical objects.
- Emit explicit warnings for optional missing modalities.
