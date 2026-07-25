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
(unlike `bwm_ephys`/`bwm_behavior`) because of its size (~14 GB). Fetch and
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
- Current version: `1.0.0`
- Config key: `datasets.bwm_lfp.root`
- Compression tier: `standard` only. `lfpack` also produces an `aggressive`
  tier (higher compression, more high-frequency roll-off); it is not
  distributed to the agent.

## Layout

```text
bwm_lfp/
  1.0.0/
    lf_compressed_all_bwm.h5
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
- `384` channels per recording
- `250 Hz` sample rate (decimated from `2500 Hz`)
- per-channel brain-region annotations (`acronym`, `atlas_id`, MNI coordinates)
- per-recording saturation (ADC-clipping) QC table
- a sync-corrected session-clock time axis (`sr.times`) for aligning to
  trial events and spike times

## Reading

Use `lfpack.LFPackReader` — a drop-in for `spikeglx.Reader` that decompresses
chunks on demand and never loads the whole file into memory. Do not restate
the API here; see:
- `skills/ibl-neuropixel/references/neuropixel_routing.md` and
  `neuropixel_function_signatures.md` for agent-facing routing
- `lfpack`'s own how-to doc:
  <https://int-brain-lab.github.io/lfpack/how-to/bwm-dataset.html>

## Known Limitations

Carried over from `lfpack`'s own release notes — check these before trusting
a result:

- **Saturation events**: ADC-clipped stretches are detected and muted
  (zeroed) in the decompressed output. Check `sr.saturation_mask`/
  `sr.saturation_times()` before trusting amplitude in a window of interest.
- **Missing sync for 7 probes**: `t0_sync`/`fs_sync` could not be computed
  for 7 probes across 4 sessions due to an upstream session-level sync
  data-quality issue; `sr.t0` returns `NaN` for these
  ([lfpack#8](https://github.com/int-brain-lab/lfpack/issues/8)).
- **High-frequency roll-off**: the SVD + wavelet-packet codec trades off
  power above roughly 20-30 Hz for compression ratio, more so at the
  (undistributed) aggressive tier.
- **Low-frequency channel-to-channel mismatch**: per-channel
  impedance/analog-filter differences show up as amplitude outliers below
  ~1.5 Hz and are not corrected by the current pre-processing pipeline.

## Related

- [Dataset layering decision](../decisions/bwm_dataset_layering.md)
- [BWM dataset overview](./README.md)
