## Purpose
Compact source card for upstream `int-brain-lab/paper-brain-wide-map` loading helpers.

## Source Basis
Repo inspected on 2026-03-06. Key files: `brainwidemap/bwm_loading.py`, loading examples, tests, and metadata CSVs.

## Use When
- The user asks for upstream BWM paper-helper behavior.
- A required field is absent from local `bwm_ephys` / `bwm_behavior`.
- You need canonical release roster, unit table, aggregate table, or trial mask behavior.

For normal BWM answers, prefer configured local derived datasets first.

## Exported Helpers
```python
from brainwidemap import (
    bwm_query,
    bwm_units,
    download_aggregate_tables,
    load_good_units,
    load_trials_and_mask,
    filter_sessions,
    filter_units_region,
)
```

If `brainwidemap` is required and unavailable, fail clearly. Do not invent fallback loading branches.

## Helper Roles
- `bwm_query(freeze="2023_12_bwm_release")`: insertion roster with `pid`, `eid`, `probe_name`, `session_number`, `date`, `subject`, `lab`.
- `bwm_units(...)`: canonical unit table after paper filters; do not assume waveform metrics exist.
- `download_aggregate_tables(one, type="clusters"|"trials")`: downloads paper aggregate parquet tables. Default upstream tag is `2024_Q2_IBL_et_al_BWM`.
- `load_good_units(...)`: single-insertion spikes/clusters with QC; upstream uses revision `2024-05-06`.
- `load_trials_and_mask(...)`: trials plus canonical BWM inclusion mask.

## Fixed Releases
Known freezes:
- `2022_10_initial`
- `2022_10_update`
- `2022_10_bwm_release`
- `2023_12_bwm_release`

Use a named freeze for reproducible BWM answers. Use `freeze=None` only when the task explicitly asks for current Alyx state.

## Aggregate Conventions
- Prefer `download_aggregate_tables(...)` over Alyx aggregate discovery for BWM paper questions.
- Do not invent aggregate tags.
- `clusters.pqt` is unit-grain; join to `bwm_query(...)` on `pid` for lab/session metadata.
- If recovering missing fields from probe ALF files, validate sorter collection/revision and join keys.
- The `2026_Q2_IBL_et_al_BWM` tag ships an updated `clusters.pqt` (59 columns vs 35 in 2024_Q2) with additional waveform shape metrics. Prefer bwm_ephys ≥ 1.2.0 `clusters.pqt` for local access to this table.

## Inclusion Pipeline
`bwm_units(...)` effectively combines:
1. `bwm_query(freeze=...)`
2. `download_aggregate_tables(..., type="trials")`
3. `filter_sessions(...)`
4. `download_aggregate_tables(..., type="clusters")`
5. `filter_units_region(...)`

Useful defaults include `min_qc=1.0`, Beryl region mapping, and minimum units/sessions filters.

## Regression Counts
For `2023_12_bwm_release`, upstream tests expect 139 subjects, 459 sessions, and 699 insertions.

Filtered unit counts from upstream tests:
- gray matter: 65,336 units across 266 regions;
- min 5 units per region: 63,357 units across 245 regions;
- min 2 sessions per region: 62,990 units across 210 regions.

## Anti-Patterns
- Do not use `pid2eid` or session loops for broad release summaries.
- Do not assume upstream/default aggregate `clusters.pqt` contains `lab` or waveform metrics; inspect the configured local dataset version and schema first.
- Do not call `one.load_dataset(...)` concurrently against a shared cache unless safety is known.
- Do not continue after a large post-merge missing-value count until collection/revision mismatch is ruled out.
- Do not wrap simple BWM answers in CLI-heavy scripts.
