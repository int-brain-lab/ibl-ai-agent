# Repeated-Site PIDs And Local BWM Spike Shards

## Purpose

Find IBL Reproducible Ephys repeated-site probe insertion IDs and map them to local BWM spike shards.

Use this note when an analysis needs the deliberate repeated-site insertions for exploration, variance estimation, power simulation, or comparison with Brain Wide Map insertions.

## Source-Backed Facts

- The public Reproducible Ephys release contains recordings from the repeated site, targeting posterior parietal cortex, hippocampus, and thalamus.
- The IBL documentation says the release tag is `RepeatedSite`.
- The ONE documentation shows tag-based repeated-site discovery with Alyx dataset tags such as `2022_Q2_IBL_et_al_RepeatedSite`.
- Public OpenAlyx may not expose a project named `repro_ephys`; do not rely on `one.search_insertions(project="repro_ephys")`.

Sources:

- https://docs.internationalbrainlab.org/notebooks_external/2024_data_release_repro_ephys.html
- https://docs.internationalbrainlab.org/notebooks_external/data_download.html

## Authoritative Discovery With Alyx

Prefer tag-based insertion queries on public OpenAlyx:

```python
from one.api import ONE

one = ONE(base_url="https://openalyx.internationalbrainlab.org", mode="remote")

tag = "RepeatedSite"  # or "2024_Q2_IBL_et_al_RepeatedSite"
query = f"datasets__tags__name,{tag}"
rows = one.alyx.rest("insertions", "list", django=query)
pids = [str(row["id"]) for row in rows]
```

Known useful tags:

- `RepeatedSite`
- `2024_Q2_IBL_et_al_RepeatedSite`
- `2022_Q2_IBL_et_al_RepeatedSite`

Use the release-specific tag when exact reproducibility to a paper/data release matters. Use `RepeatedSite` when the broad public repeated-site set is intended.

## Reference To Local BWM Data

The local `bwm_ephys` dataset is insertion-sharded by `pid` under `spikes/<pid>/...`. Intersect repeated-site PIDs with local insertion metadata before loading spikes:

```python
import pandas as pd
from ibl_agent.data_locations import resolve_dataset_dir

bwm_dir = resolve_dataset_dir("bwm_ephys")
insertions = pd.read_parquet(
    bwm_dir / "metadata" / "insertions.parquet",
    columns=["pid", "eid", "subject", "lab", "probe_name", "n_good_units"],
)
insertions["pid"] = insertions["pid"].astype(str)

repeated_local = insertions.loc[insertions["pid"].isin(set(pids))].copy()
repeated_local["spike_shard"] = repeated_local["pid"].map(lambda pid: bwm_dir / "spikes" / pid)
repeated_local["has_local_spike_shard"] = repeated_local["spike_shard"].map(lambda path: path.exists())
```

Then load a shard with:

```python
from ibl_agent.datasets.bwm_ephys import load_spike_shard

pid = repeated_local.loc[repeated_local["has_local_spike_shard"], "pid"].iloc[0]
shard = load_spike_shard(bwm_dir / "spikes" / pid)
```

## Can This Be Done Without Alyx Queries?

Not authoritatively from the current local `bwm_ephys` shard layout alone. The local BWM insertion and unit metadata contain `pid`, session metadata, and region labels, but not dataset-release tags such as `RepeatedSite`.

Offline/local options are:

1. Use a previously saved repeated-site PID sidecar, e.g. `repeated_site_pids.csv`, then intersect with local `metadata/insertions.parquet`.
2. Use a local ONE cache already loaded for the repeated-site tag, then query that cache without remote calls.
3. Approximate repeated sites by planned trajectory groups if planned trajectory metadata have been saved locally, but this is not identical to tag-defined repeated-site release membership.

Recommended durable sidecar columns:

- `pid`
- `tag`
- `source_endpoint`
- `query`
- `queried_at`
- `overlaps_bwm_ephys`
- `has_local_spike_shard`

## Planned-Trajectory Fallback

If the tag query is unavailable, repeated planned trajectories can be identified from Alyx `trajectories` rows with `provenance="Planned"` and grouped by planned coordinates and angles. This can recover the deliberate repeated trajectory family, but it is a geometric proxy, not the release tag.

```python
rows = one.alyx.rest(
    "trajectories",
    "list",
    project="ibl_neuropixel_brainwide_01",
    provenance="Planned",
)
```

After filtering to local BWM PIDs, group by rounded `x`, `y`, `z`, `depth`, `theta`, `phi`, and `roll`. Treat groups with many insertions, for example `>=5`, as repeated planned trajectories. Report this as planned-trajectory repetition, not `RepeatedSite` tag membership.

## Quality Gates

- State whether repeated-site membership came from dataset tags or planned-trajectory grouping.
- Always report the tag name when tag-based discovery is used.
- Always report how many repeated-site PIDs overlap the local `bwm_ephys` insertion roster and how many have local spike shards.
- Do not assume `project="repro_ephys"` works on public OpenAlyx.
