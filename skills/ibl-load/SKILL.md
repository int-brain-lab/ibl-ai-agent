---
name: ibl-load
description: Discover IBL data from current local metadata, promptly explain available products, and load the smallest sufficient surface.
---

# IBL Load

## Use this skill when
- The user asks what data, derived features, or behavioral labels are available.
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

### Explain available data promptly

- For “what data do we have?” or modality/label questions, run `.venv/bin/python -m ibl_ai_agent.data_discovery --query paw --json`, replacing `paw` with a broad relevant term; omit the query for an overview. Use the project interpreter (`.venv/Scripts/python.exe` on Windows), not a bare system Python, so installed IBL products are discoverable.
- Check `software_inspection`: if incomplete, inspect the reported source errors or correct the interpreter/prerequisites before interpreting empty capabilities as evidence. Include relevant software-supported high-level products in the first explanation even when they are not stored locally; inspect this evidence before proposing to derive labels or build a classifier.
- After the first metadata pass, give the user a brief explanation of the relevant measurements, predictions and summaries, their source, what is present locally, and what remains unknown. Do this before exhaustive cache searches or analysis planning; a capability question needs no scientific project scaffold.
- Distinguish sampled shard fields, release declarations, local files, installed API support and unverified live availability. A zero-match search returns the unfiltered overview with `query_fallback` set: treat these entries as discovery candidates, not synonyms for the requested term. A missing BWM field or lexical search miss does not establish global absence; inspect related source documentation before concluding.
- Explain scientific meaning from existing producer documentation/references, not column names alone. Label unresolved meanings and predictions explicitly; do not silently map the user's term onto a related proxy. Follow `references/data_loading.md` for deeper discovery and access rules.

### Load the selected data

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
