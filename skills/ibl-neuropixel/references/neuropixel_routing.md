## Purpose
Route raw Neuropixels and SpikeGLX tasks to the right API family from the `int-brain-lab/ibl-neuropixel` repository.

## How to use this reference
1. Decide whether the task is raw preprocessing or downstream analysis.
2. If it is raw preprocessing, use the workflow classes below.
3. Consult `neuropixel_function_signatures.md` for exact callable names and signatures.
4. Only hand off to `ibl-load` or `ibl-analyze` after the output is analysis-ready.

## Routing map

### Raw SpikeGLX file access
Use when the task needs direct reads from `.ap.bin`, `.lf.bin`, `.ap.cbin`, or related SpikeGLX files.

Prefer:
- `spikeglx.Reader`

Use this for:
- opening recordings
- reading chunked time windows
- querying metadata such as sample rate or sync channels

### Lossless compression and cbin conversion
Use when the task is about reducing disk footprint or converting raw binaries to compressed chunks.

Prefer:
- `spikeglx.Reader.compress_file`

Policy:
- Use the reader-native compression workflow before custom archive logic.

### Destriping raw AP/LFP data
Use when the task is about line-noise/common-mode cleanup on raw probe traces before inspection or sorting.

Prefer:
- `ibldsp.voltage.destripe`

Use this for:
- in-memory chunk destriping
- comparing raw vs cleaned traces
- feeding cleaned chunks into viewers or downstream preprocessing

### Batch decompress + destripe workflows
Use when the task is about converting a compressed or raw file into a destriped output file on disk.

Prefer:
- `ibldsp.voltage.decompress_destripe_cbin`

Use this for:
- file-to-file destriping
- multiprocessing destriping pipelines

### Raw trace visualization
Use when the user wants to inspect raw or destriped traces interactively or via quick plots.

Prefer:
- `ibldsp.plots.voltageshow`
- `viewephys.gui.viewephys`

Policy:
- Use lightweight plotting for scripted checks.
- Use `viewephys` for interactive trace inspection when an actual viewer is needed.

### Probe-version-aware preprocessing
Use when the task mentions Neuropixels 1.0 vs 2.0 or needs explicit probe-version handling.

Prefer:
- repo-native `spikeglx` and `ibldsp` functions with explicit `neuropixel_version` or reader metadata

Policy:
- Do not hard-code NP1 assumptions into NP2 workflows.
- Carry probe version through preprocessing calls when the function supports it.

### Waveform or raw chunk extraction for downstream QC
Use when the task needs a bounded raw snippet rather than a full file transform.

Prefer:
- `spikeglx.Reader` slicing
- `brainbox.io.spikeglx.extract_waveforms` when the task is waveform-centric rather than file-centric

## Channel coordinate convention (`axial_um`)

`axial_um` is the distance in µm from the **tip** of the probe shank — the SpikeGLX convention.

| `axial_um` value | meaning |
|-----------------|---------|
| small (≈ 0) | closest to tip → **deepest** in the brain (lower region) |
| large (≈ 3840 for NP1) | far from tip → **most superficial** (upper region) |

Consequence for depth-ordered operations: sorting ascending by `axial_um` and stepping with `shift(-1)` moves from **deeper → shallower** (lower → upper). Label axes accordingly:
- rows / `from` (smaller `axial_um`) → **lower region**
- columns / `to` (larger `axial_um`) → **upper region**

## Decision rule
- If the user starts from raw files, use `ibl-neuropixel`.
- If the user starts from loaded spikes/clusters/trials, skip this skill and use `ibl-load` plus `ibl-analyze`.
- Do not turn a scientific question into a raw preprocessing workflow unless the required information is absent from analysis-ready data.
