# Repeated-Site PIDs And Local BWM Spike Shards

## Purpose

Find IBL Reproducible Ephys repeated-site probe insertion IDs and map them to local BWM spike shards.

Use this note when an analysis needs the deliberate repeated-site insertions for exploration, variance estimation, power simulation, or comparison with Brain Wide Map insertions.

## Offline Reference Files

Use the repo-local sidecar first; do not query Alyx merely to discover the repeated-site PIDs.

- `repeated_site_pids.csv`: canonical offline repeated-site insertion table.
- `repeated_site_pids_metadata.json`: provenance and summary counts for the sidecar.

The current sidecar was generated from the public OpenAlyx insertion tag query `datasets__tags__name,RepeatedSite` and then enriched from the configured local `bwm_ephys` metadata. It contains 110 unique repeated-site PIDs. In the enrichment dataset, 67 PIDs overlapped local `bwm_ephys` v1.1.0 and all 67 had local spike shards.

Available columns include:

- tag membership: `pid`, `eid`, `tag`, `probe_name`
- local session/probe metadata when the PID overlaps `bwm_ephys`: `subject`, `date`, `session_number`, `lab`
- local coverage/count fields: `n_good_units`, `n_trials`, `n_included_trials`, `n_channels`, `overlaps_bwm_ephys`, `has_local_spike_shard`
- provenance: `source_endpoint`, `alyx_resource`, `query`, `source_created_at_utc`, `source_artifact`, `bwm_ephys_dataset_version`, `bwm_ephys_source_freeze`, `bwm_ephys_good_unit_rule`

The local `bwm_ephys` insertion metadata used for enrichment did not include insertion QC outcome fields beyond unit, trial, and channel counts.

```python
from pathlib import Path

import pandas as pd

reference_dir = Path("skills") / "ibl-load" / "references"
repeated = pd.read_csv(reference_dir / "repeated_site_pids.csv", dtype={"pid": str, "eid": str})

repeated_local = repeated.loc[
    repeated["overlaps_bwm_ephys"].eq(True) & repeated["has_local_spike_shard"].eq(True)
].copy()
```

## Source-Backed Facts

- The public Reproducible Ephys release contains recordings from the repeated site, targeting posterior parietal cortex, hippocampus, and thalamus.
- The IBL documentation says the release tag is `RepeatedSite`.
- The ONE documentation shows tag-based repeated-site discovery with Alyx dataset tags such as `2022_Q2_IBL_et_al_RepeatedSite`.
- Public OpenAlyx may not expose a project named `repro_ephys`; do not rely on `one.search_insertions(project="repro_ephys")`.

Sources:

- https://docs.internationalbrainlab.org/notebooks_external/2024_data_release_repro_ephys.html
- https://docs.internationalbrainlab.org/notebooks_external/data_download.html

## Authoritative Discovery With Alyx

The local sidecar above is the default runtime path. Use Alyx only when deliberately refreshing or validating the sidecar. Prefer tag-based insertion queries on public OpenAlyx:

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

The local `bwm_ephys` dataset is insertion-sharded by `pid` under `spikes/<pid>/...`. If the sidecar needs to be checked against a different local `bwm_ephys` version, intersect repeated-site PIDs with local insertion metadata before loading spikes:

```python
import pandas as pd
from ibl_ai_agent.data_locations import resolve_dataset_dir

bwm_dir = resolve_dataset_dir("bwm_ephys")
repeated = pd.read_csv("skills/ibl-load/references/repeated_site_pids.csv", dtype=str)
insertions = pd.read_parquet(
    bwm_dir / "metadata" / "insertions.parquet",
    columns=["pid", "eid", "subject", "lab", "probe_name", "n_good_units"],
)
insertions["pid"] = insertions["pid"].astype(str)

repeated_pids = set(repeated["pid"])
repeated_local = insertions.loc[insertions["pid"].isin(repeated_pids)].copy()
repeated_local["spike_shard"] = repeated_local["pid"].map(lambda pid: bwm_dir / "spikes" / pid)
repeated_local["has_local_spike_shard"] = repeated_local["spike_shard"].map(lambda path: path.exists())
```

Then load a shard with:

```python
from ibl_ai_agent.datasets.bwm_ephys import load_spike_shard

pid = repeated_local.loc[repeated_local["has_local_spike_shard"], "pid"].iloc[0]
shard = load_spike_shard(bwm_dir / "spikes" / pid)
```

## Can This Be Done Without Alyx Queries?

Yes. Use `repeated_site_pids.csv` for normal offline work. The local BWM insertion and unit metadata contain `pid`, session metadata, and region labels, but not dataset-release tags such as `RepeatedSite`; the sidecar supplies that tag membership.

Offline/local options are:

1. Use `skills/ibl-load/references/repeated_site_pids.csv`, then optionally re-intersect with local `metadata/insertions.parquet` when using a different BWM dataset version.
2. Use a local ONE cache already loaded for the repeated-site tag, then query that cache without remote calls.
3. Approximate repeated sites by planned trajectory groups if planned trajectory metadata have been saved locally, but this is not identical to tag-defined repeated-site release membership.

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
- For normal offline work, report the sidecar row count, tag name, source query, and sidecar metadata timestamp.
- Always report how many repeated-site PIDs overlap the local `bwm_ephys` insertion roster and how many have local spike shards.
- Query Alyx only when the user asks to refresh or validate the reference sidecar.
- Do not assume `project="repro_ephys"` works on public OpenAlyx.
