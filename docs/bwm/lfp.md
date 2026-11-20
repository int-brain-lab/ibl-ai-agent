# BWM LFP Dataset Specification

## Status

`bwm_lfp` is a local, compressed companion to `bwm_ephys`/`bwm_behavior`
covering the raw LFP band. Unlike those two datasets, it is not built by this
repo: the archive is produced upstream by the `lfpack` package (in the
`ephys-atlas` repo) and this repo only downloads, verifies, and registers it
so it can be discovered through the same `data_locations.local.yaml` /
`ibl_ai_agent.data_locations.resolve_dataset_dir` path as every other BWM
dataset.

It is opt-in: `scripts/download_datasets.py` does not fetch it by default
(unlike `bwm_ephys`/`bwm_behavior`) because of its size (~20 GB). Fetch and
register it with:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/download_datasets.py --lfp
```

Reading the file needs the `lfp` extra, not installed by default:

```bash
UV_CACHE_DIR=.uv-cache uv sync --extra lfp
```

Dataset identity:
- Name: `bwm_lfp`
- Current version: `1.2.0`. From 1.2.0 on, the version is the `lfpack`
  version that wrote the file (`sr.provenance["lfpack_version"]`); 1.0.0 and
  1.1.0 predate this rule.
- Config key: `datasets.bwm_lfp.root`
- Compression tier: `default` (α = 7) only, recorded as `standard`. The `small`
  and `fine` tiers are not distributed to the agent.

## Layout

```text
bwm_lfp/
  1.2.0/
    lf_compressed_v05_a07_default_all_bwm.h5
    schema.yaml
    provenance.yaml
    manifest.json
```

`schema.yaml`, `provenance.yaml`, and `manifest.json` are authored by
`scripts/download_datasets.py` at download time (see
`_write_lfp_sidecars`) — `lfpack` ships only the `.h5` file itself. This
keeps `bwm_lfp` schema-discoverable the same way as the other BWM datasets,
even though nothing here is built by this repo's own pipeline.

## Contents

One HDF5 file holding all `699` BWM probe recordings, keyed by `pid`:
- `384` channels for `695` recordings and `96` channels for four NP2.4 recordings from subject `NR_0029`
- `250 Hz` sample rate (decimated from `2500 Hz`)
- per-channel brain-region annotations (`acronym`, `atlas_id`, MNI coordinates)
- per-recording saturation (ADC-clipping) QC table
- a sync-corrected session-clock time axis (`sr.times`), non-linear (knot-based)
  where the registered sync itself has real non-linear structure, falling back to
  an affine outside the knot range

## Reading

Use `lfpack.LFPackReader` — a drop-in for `spikeglx.Reader` that decompresses
chunks on demand and never loads the whole file into memory. Do not restate
the API here; see:
- `skills/ibl-neuropixel/references/neuropixel_routing.md` and
  `neuropixel_function_signatures.md` for agent-facing routing
- `lfpack`'s own how-to doc:
  <https://int-brain-lab.github.io/lfpack/how-to/bwm-dataset.html>

## Known Limitations

See the [Limitations](https://int-brain-lab.github.io/lfpack/how-to/bwm-dataset.html#limitations)
of the lfpack page (saturation, high frequencies, CSD, low frequencies). Specific to the agent:

- **Saturation**: decompressed values in muted stretches are not exactly zero; use
  `sr.saturation_mask`/`sr.saturation_times()`, never sample values.
- **Sync**: the 11 probes fixed in `bwm_lfp` 1.1.0
  ([lfpack#8](https://github.com/int-brain-lab/lfpack/issues/8)) stay fixed; align with `sr.times`
  only when `sr.t0` is finite.

## Related

- [Dataset layering decision](../decisions/bwm_dataset_layering.md)
- [BWM dataset overview](./README.md)
