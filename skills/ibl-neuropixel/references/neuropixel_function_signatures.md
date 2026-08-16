# IBL Neuropixel Function Signatures

Reviewed callable sheet for the functions and classes explicitly surfaced by the upstream `int-brain-lab/ibl-neuropixel` repository README and related IBL docs.

## Usage policy
- Use `neuropixel_routing.md` first to choose the right workflow.
- Use this file second to copy exact call signatures or object entrypoints.
- When updating this file, prefer the upstream repo README and package sources over informal examples elsewhere.

## `spikeglx`
- `Reader(file_bin)`
- `Reader.__getitem__(slice_or_index)`
- `Reader.compress_file(keep_original=True)`

Common reader attributes used in repo examples:
- `Reader.fs`
- `Reader.nc`
- `Reader.nsync`

## `lfpack`
- `LFPackReader(h5_file, recording=None, scale=0, bin_channels=1)` — drop-in for `spikeglx.Reader` over the compressed LFP `.h5`
- `LFPackReader.recordings(h5_file)` — list available recording ids (BWM: `pid`s)
- `LFPackReader.__getitem__(slice)` → `(n_samples, nc)` float32 volts
- reader attributes: `.fs`, `.nc`, `.ns`, `.times`, `.channels`, `.channels_full`, `.geometry`
- Optional extra (`ibl-ai-agent[lfp]`); import-check before use and offer `uv sync --extra lfp` if missing.
- Full API and BWM how-to: <https://int-brain-lab.github.io/lfpack/> (do not restate here; refresh from `help(...)`).

## `ibldsp.voltage`
- `destripe(raw, fs=None, neuropixel_version=None, ...)`
- `decompress_destripe_cbin(sr_file, output_file=None, nprocesses=None, ...)`

## `ibldsp.plots`
- `voltageshow(raw, fs=None, title=None, ...)`

## `viewephys.gui`
- `viewephys(raw, fs=None, title=None, ...)`

## `brainbox.io.spikeglx`
- `extract_waveforms(ephys_file, ts, ch, t=2.0, sr=30000, n_ch_probe=385, car=True)`

## Notes
- The exact optional parameters for `ibldsp` and viewer helpers should be refreshed from the installed package or repository source before broadening runtime use.
- The stable runtime entrypoints already documented upstream are `spikeglx.Reader`, `ibldsp.voltage.destripe`, and `ibldsp.voltage.decompress_destripe_cbin`.
