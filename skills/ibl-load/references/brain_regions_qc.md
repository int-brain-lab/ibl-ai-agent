## Purpose
Spike/cluster loading, unit QC, and region mapping contracts for IBL analyses.
For atlas navigation, coordinate lookup, hierarchy traversal, and brain-map plots see `../../ibl-anatomy/references/atlas_navigation.md`.

## Sources
Verified against Brainbox/IBL atlas docs on 2026-03-03.

## Core Loaders
```python
from brainbox.io.one import SessionLoader, SpikeSortingLoader, merge_clusters_channels
from iblatlas.regions import BrainRegions

sl = SessionLoader(one=one, eid=eid)
sl.load_trials()
sl.load_wheel()

ssl = SpikeSortingLoader(one=one, eid=eid, pname="probe00")
spikes, clusters, channels = ssl.load_spike_sorting()
clusters = merge_clusters_channels(clusters, channels)
```

Use `load_spike_sorting(good_units=True)` only when loader-side filtering is scientifically acceptable. Prefer explicit filters when the report must state the QC rule.

## QC Contract
- Ensure region/acronym and unit id are available before region-level aggregation.
- Enforce numeric dtype for rate, latency, width, and depth fields.
- State QC filter used (`is_good`, `label`, `clusters.metrics`, or loader-side good units).
- Use per-cluster amplitudes for unit filtering; do not filter units directly on raw per-spike amplitudes without aggregation.
- Missing probe data may be skipped with warning if the metric allows; missing region labels make region metrics non-computable for that session.

## Region Mapping
```python
br = BrainRegions()
beryl = br.acronym2acronym(acronyms, mapping="Beryl")
beryl_ids = br.id2id(atlas_ids, mapping="Beryl")
```

Rules:
- Declare one mapping per metric: `Allen`, `Beryl`, `Cosmos`, or their `-lr` variants.
- Use non-`-lr` mappings for hemisphere-pooled analyses and `-lr` mappings for hemisphere-sensitive analyses.
- Preserve source region labels and mapping choice in metadata.
- Unknown or unmapped labels must be counted and reported, not silently coerced.

## Region Fallback From Channels
When cluster acronyms are absent:
1. load `clusters.channels` and `channels.brainLocationIds_ccf_2017`;
2. map cluster channel ids to channel location ids;
3. convert ids with `BrainRegions.id2acronym(...)`;
4. log any deterministic reduction if channel location IDs are unexpectedly 2D.

## Depth and Hierarchy
- `clusters.depths` is microns from probe tip — do **not** use as cross-insertion cortical depth.
- For **continuous cortical depth** from CCF coords (`units.parquet` `x`, `y`, `z` in metres):
  `from iblatlas.streamlines.utils import xyz_to_depth` → `xyz_to_depth(xyz, per=False)` (µm from surface) or `per=True` (0–100 %). Returns `nan` outside Isocortex. Lookup files downloaded from S3 on first call. See `../../ibl-anatomy/references/atlas_navigation.md#iblatlas.streamlines`.
- For parent/child rollups, use `BrainRegions.ancestors(...)` and `descendants(...)`.
- For slice plotting, prepare left/right data with `iblatlas.plots.prepare_lr_data` and use matching `*-lr` mapping.

## ACG/CCG Helper
```python
from brainbox.population.decode import xcorr

ccg = xcorr(spike_times, spike_clusters)
acg = xcorr(spike_times[spike_clusters == clu_id], np.zeros(n_spikes, dtype=int))
```
