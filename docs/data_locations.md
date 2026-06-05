# Data Locations

Large local datasets do not belong in this repository. The repo defines dataset
schemas and loading rules; each user records where the actual data live on their
own computer.

## Local Config

For public Brain Wide Map data, the automatic setup path is:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/download_datasets.py
```

The script downloads the public `bwm_ephys` and `bwm_behavior` archives into
`reports/datasets/` and writes `data_locations.local.yaml`. Approximate download
sizes are listed in `docs/bwm/README.md`.

If the datasets are already present under `reports/datasets/`, the script skips
the download and refreshes the local config. If `data_locations.local.yaml`
already contains manually configured BWM roots, the script leaves that file
unchanged and reports any missing schemas instead of overwriting your paths.

These are analysis-ready derived datasets, not raw Neuropixels data and not a
full ONE cache. They are intended to make common BWM analyses faster, local, and
easier for coding agents to inspect. See `docs/bwm/README.md` for current
dataset contents, sizes, versions, and schema links.

For durable or shared installations, keep BWM datasets and ONE caches wherever
they fit on your computer, then point the repo to those paths with
`data_locations.local.yaml`.

For manual setup, copy `data_locations.example.yaml` to
`data_locations.local.yaml` and fill in paths for this machine.
`data_locations.local.yaml` is gitignored.

```bash
cp data_locations.example.yaml data_locations.local.yaml
```

```yaml
datasets:
  bwm_ephys:
    root: "D:/IBL data/bwm_ephys"
    preferred_version: latest

  bwm_behavior:
    root: "D:/IBL data/bwm_behavior"
    preferred_version: latest

one_cache:
  root: "C:/Users/<user>/Downloads/ONE"
```

For the default downloader location, the dataset roots commonly look like:

```yaml
datasets:
  bwm_ephys:
    root: reports/datasets/bwm_ephys
    preferred_version: latest
  bwm_behavior:
    root: reports/datasets/bwm_behavior
    preferred_version: latest
one_cache:
  root: "C:/Users/<user>/Downloads/ONE"
```

`root` may be either:
- a directory containing version folders such as `1.1.0/schema.yaml`; or
- one exact dataset directory containing `schema.yaml`.

For automatic dataset updates, prefer a version-parent root such as
`reports/datasets/bwm_ephys` with `preferred_version: latest`. If `root` points
directly at an exact version such as `reports/datasets/bwm_ephys/1.1.0`, the
downloader treats that as a manual pin and will not install a newer version into
that path.

Use a project-level override only when a project has a special data location:

```text
projects/<project_slug>/data_locations.local.yaml
```

Set `IBL_AGENT_DATA_LOCATIONS` to an explicit config path when running scripts
from another directory or when a project override should be forced.

## Runtime Rule

Agents and scripts should resolve BWM datasets through `ibl_ai_agent.data_locations`
instead of assuming `reports/datasets`.

```python
from ibl_ai_agent.data_locations import resolve_dataset_dir

bwm_ephys_dir = resolve_dataset_dir("bwm_ephys")
bwm_behavior_dir = resolve_dataset_dir("bwm_behavior")
```

If no local data location is configured, BWM agents may offer to run the public
dataset downloader before analysis. If a user has manually configured dataset
roots, agents and scripts should not overwrite them; they should report missing
or invalid schemas clearly.

## Provenance And Access

The public BWM derived datasets are built from IBL Brain Wide Map data surfaces
and are intended for local analysis in this repository. Public examples should
prefer these derived local datasets when they are semantically sufficient.

Private Alyx/ONE access is supported by development tooling, but it is not the
default path for public examples. If an analysis requires private data, the
agent should say so explicitly before attempting access.
