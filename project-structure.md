# Ingested dataset package structure

Standardised on-disk structure written by the data-ingestion skill, so that an
agent can analyse a dataset it has never seen without re-reading the raw source.

## What a package is

A **dataset package** is one dataset, at one version, in one directory: its prose
description, its machine-readable schema, its provenance, its metadata tables, and
its bulk signal stores.

It is not a "project" in this repo's existing sense. `projects/<project_slug>/`
continues to mean an *analysis* project (`question.md`, `TODO.md`,
`exploratory-analyses/`, …) as defined in `AGENTS.md`. Dataset packages are data,
live outside the checkout, and are registered in `data_locations.local.yaml`
alongside whatever datasets are already configured there.

Prose lives **inside the package**, not in this repo: an arbitrary lab's dataset
has no repo to keep its documentation in, so the package must carry its own.
(BWM's prose sits in `docs/bwm/` and `skills/` for historical reasons and stays
there.)

## Registration

A package is registered by name, and resolved by
`ibl_ai_agent.data_locations.resolve_dataset_dir()`. Illustrative entry; the name
and path are the dataset's own:

```yaml
# data_locations.local.yaml
datasets:
  example_dataset:
    root: /path/to/datasets/example_dataset
    preferred_version: latest
```

## Directory tree

```
<dataset_root>/<dataset_name>/<version>/
├── README.md                  # one-paragraph summary; the only file needed to judge relevance
├── experiment.md              # methods section: what was done, portable and lab-neutral
├── scientific-context.md      # aim, caveats from the design, known confounds, papers
├── modalities.md              # per modality: instrument, sampling, units, timing, coverage, QC
├── schema.yaml                # machine-readable contract: tables, stores, clocks, frames, design
├── provenance.yaml            # where it came from and what was done to it
├── manifest.json              # file inventory with sizes and hashes
├── SUMMARY.md                 # generated counts
├── metadata/
│   ├── subjects.parquet       # core
│   ├── sessions.parquet       # core
│   ├── recordings.parquet     # core
│   ├── events.parquet         # core (may be empty)
│   ├── epochs.parquet         # core (may be empty)
│   ├── units.parquet          # conditional: discrete sorted or segmented sources
│   ├── channels.parquet       # conditional: electrode-based recordings
│   └── trials.parquet         # conditional: trial-based experiments
├── features/                  # optional derived summaries
├── spikes/<shard_id>/         # store, when present: discrete spike times, Blosc shards
├── <timeseries stores>/       # store, when present: continuous sampled signals
└── ingestion/
    ├── convert.py             # the conversion script(s)
    ├── ingestion-log.md       # what was done, what was inferred, what was asked
    └── open-questions.md      # unresolved items an analysis agent must know about
```

## Files

### `README.md`
Fixed headings: `Summary` | `Scope` | `When to use` | `When not to use`.
`Summary` is one paragraph: species, subject and session counts, modalities,
scientific aim. This is what the dataset-discovery step reads across all
configured datasets to choose one, so it must be sufficient on its own.

### `experiment.md`
A methods section. Portable and lab-neutral: it describes the experiment, not lab's
interpretation of it. Fixed headings:
`Subjects` | `Surgery` | `Apparatus` | `Protocol` | `Design and factors` |
`Trial structure` | `Session schedule` | `Training history`.
Headings that do not apply are kept and marked not applicable.

`Design and factors` is required and mirrors the machine-readable `design:` block
in `schema.yaml`: which factors the experiment manipulates or measures, at what
grain, and what comparison it was built for.

### `scientific-context.md`
Required. What the study was for and what could mislead an analysis of it. Fixed
headings:
`Aim` | `Caveats from the design` | `Other known confounds` | `Papers`.
`Caveats from the design` is always filled. The ingestion skill derives it from
facts in `experiment.md` and `modalities.md`, so it does not depend on the user
knowing the confounds. `Aim`, `Other known confounds` and `Papers` come from the
user or the documentation and may say "not stated" or "none known". Kept separate
from `experiment.md` so the methods stay reusable, and separate from `skills/` so
repo-wide scientific guidance is not duplicated per dataset.

### `modalities.md`
One section per recording modality, fixed headings:
`Instrument` | `Sampling` | `Units` | `Preprocessing` | `Time base` | `Coverage` | `QC`.
Prose counterpart to the `stores:` and `time_bases:` blocks in `schema.yaml`.

### `schema.yaml`
The machine-readable contract: tables, stores, clocks, frames and design. (It
extends the shape of the existing `bwm_ephys` schema with per-column units,
explicit clocks, spatial frames and experimental design — history, not something
a reader needs.)

The block below is **illustrative, not a template**. It is a deliberate
composite, written to exercise most of the contract at once: an IBL task name, a
second (imaging) clock, an arena reference frame, a fluorescence store and an
age-by-genotype design. No real dataset need have that combination. The **keys**
are the contract; every **value** is an example, the dataset name, the task and
protocol strings and the design factors included. A block a dataset has no use
for — `reference_frames`, `design`, any table or store it lacks — is simply
absent; what cannot be omitted is in "Minimum viable package" below.

```yaml
dataset_name: example_dataset
dataset_version: 1.0.0
contract_version: 1               # version of this generic package contract
schema_version: 2                 # version of this dataset's own layout, as today
dataset_kind: ingested            # `bwm` for the existing BWM datasets
task: ibl_choice_world            # example value; null when not trial-based
task_protocol: _iblrig_tasks_biasedChoiceWorld   # example; exact protocol, null if unrecorded

time_bases:                       # several allowed; each store and table names one
  session_clock:
    origin: session_start
    clock: daq
    aligned_to: null
  imaging_clock:
    origin: first_frame
    clock: scanner
    aligned_to: session_clock
    method: ttl_pulse_regression

reference_frames:                 # non-time coordinate systems
  arena:
    origin: arena_centre
    axes: [x, y]
    units: cm

design:                           # what the experiment manipulates or measures
  factors:                        # example factors; declare the dataset's own
    age:      {grain: between_session, type: continuous,  units: days}
    genotype: {grain: between_subject, type: categorical, levels: [wt, ko]}
  intended_comparison: >
    Neural and behavioural differences across age, within genotype.

tables:
  sessions:
    path: metadata/sessions.parquet
    primary_key: [session_id]
    time_base: session_clock
    columns:
      session_id:         {dtype: str,     units: null,    description: ...}
      age_at_session_days: {dtype: float64, units: days,   description: ...}
  epochs:
    path: metadata/epochs.parquet
    primary_key: [session_id, epoch_set, epoch_id]
    time_base: session_clock
    columns: {...}
  # column_map: {session_id: eid}   # only where source column names differ

stores:
  spikes:
    kind: spike_shards
    path: spikes
    shard_key: recording_id
    container_format: blosc_file_shards
    shard_layout: <recording_id>/meta.json + <recording_id>/*.blosc
    arrays: [spike_times_delta_ticks, spike_clusters, cluster_ids, cluster_spike_counts]
    written_by: spikepack==<version>
    quantization_us: 100           # the writer's default; declare what was used
    time_base: session_clock
    units: seconds
  fluorescence:
    kind: timeseries
    path: fluorescence
    sources: rois
    rate_hz: 30.0
    t0: 0.0
    units: a.u.
    time_base: imaging_clock
    container_format: <chosen per store>
  lfp:
    kind: referenced_in_place
    reader: spikeglx
    units: volts
    time_base: session_clock
    resolve: {one_eid: ..., dataset: ..., collection: ..., revision: ...}  # one scheme; see Stores
```

### `provenance.yaml`
Where the data came from and what was done to it.

```yaml
source:
  url: ...
  doi: ...
  lab: ...
  contact: ...
  version: ...              # the UPSTREAM version, independent of the package version
  access: public | restricted
conversion:
  steps: [...]              # ordered, human-readable
  tools: {spikepack: ..., ibl-ai-agent: ...}
  lossy: [...]              # anything not reversible, e.g. 100 us spike quantization
  dropped: [...]            # anything in the source not carried into the package
source_selection_rule: ...  # which units/ROIs were kept, and why
ingested_at: ...
ingested_by: ...
```

`source_selection_rule` is also echoed into each spike shard's `meta.json`, so a
shard is self-describing about whether it holds all sources or a QC subset.

### `manifest.json`, `SUMMARY.md`
File inventory with sizes and hashes; generated counts of subjects, sessions,
recordings, sources and events.

### `metadata/` — core tables

All five are required to exist. **Required columns** are the table's key and the
columns that link it to the others; everything else is declared when the dataset
has it.

| Table | Grain | Required columns | Typical columns, declared when present |
| --- | --- | --- | --- |
| `subjects` | one row per subject | `subject_id` | `line`, `genotype`, `sex`, `dob`, `cohort` |
| `sessions` | one row per session | `session_id`, `subject_id`, `date` | `protocol`, `lab`, `rig`, `duration`, `age_at_session_days` |
| `recordings` | one row per recording device instance | `recording_id`, `session_id` | device, target, sync source |
| `events` | one row per named instant | `session_id`, `event_id`, `event_name`, `event_time` (float64 s), nullable `trial_id`, nullable `event_value` | declared extra columns |
| `epochs` | one row per labelled interval | `session_id`, `epoch_set`, `epoch_id`, `label`, `start_time`, `stop_time` | declared extra columns |

`events` and `epochs` may be empty. These are column lists, not a column order;
where an emitted order matters it is fixed by the table's own contract, as
`skills/data-ingest/references/ibl-trials.md` fixes it for the IBL task.

Where age is a design factor, `age_at_session_days` goes on `sessions`, not
`subjects`, because it varies by session.

`epoch_set` is a labeling namespace, so independent and overlapping labelings of
the same session — locomotion state, zone occupancy, sleep stage, drug on/off —
coexist without ambiguity.

Together these carry non-trial-based behaviour without schema change: named
instants with an optional scalar value and declared extra columns, and named
intervals within a namespace.

### `metadata/` — conditional tables

- `units` — when discrete sources are sorted or segmented (spike-sorted units,
  imaging ROIs). Columns are dataset-declared.
- `channels` — electrode-based recordings only.
- `trials` — trial-based experiments only.

### `features/`
Optional: a package may ship none. Where a dataset does want BWM-compatible
analysis patterns to transfer, the recommended shape is BWM's `unit_features` /
`event_response_features` schema.

### Stores

| Kind | Holds | Notes |
| --- | --- | --- |
| `spike_shards` | discrete, sorted event times | Written with `spikepack.write_blosc`; same on-disk format as `bwm_ephys` |
| `timeseries` | regularly sampled or explicitly timestamped continuous signals | One store per signal family: position, fluorescence, LFP, photometry, pose |
| `referenced_in_place` | bulk data not converted | Records what re-resolves the file, never only a local path |

`spike_shards` holds discrete event times only. A deconvolved calcium trace is a
value per frame and belongs in a `timeseries` store; thresholding it into event
times is an analysis step, never a conversion step.

`spikepack.write_blosc` and the shard format are fixed. `quantization_us` is not:
it is the grid the times are snapped to, 100 is the writer's default, and the
store declares whatever was used. Ingestion writes **tick-aligned spike origins**,
so the `time_origin_ticks` and `time_origin_seconds` decoders agree exactly. See
`ingestion-notes.md`.

`referenced_in_place` records a **re-resolvable identifier scheme**, never a local
path: whatever a reader needs to fetch the file again from the archive it came
from. The `resolve:` block above is one such scheme, the ONE/Alyx form
(`one_eid`, `dataset`, `collection`, `revision`). A DANDI source would key on
dandiset id, version and asset path instead; no repo code resolves that form yet.
Declare the keys the scheme needs, and say in `modalities.md` what resolves them.

### `ingestion/`
`convert.py` makes the build reproducible. `ingestion-log.md` records what was
done, what was inferred and what was asked. `open-questions.md` records unresolved
items — an analysis agent reads it before using the package.

## Minimum viable package

Ingestion **cannot ship** a package without: `dataset_name`, `dataset_version`, a
`README.md` summary, a `scientific-context.md` with its design caveats, a declared
time base, units for every column of every declared table and store, `subjects`
and `sessions`, `provenance.source`, and for every declared store either
materialised data or a re-resolvable reference.

Ingestion **may ship** without, recording an entry in `open-questions.md`:
`features/`, per-column prose beyond units, exact conversion detail for
upstream-derived fields, optional modalities.

The skill never invents a unit or a time base to close a blocking gap. It stops
and asks. `a.u.` and `dimensionless` are valid units for quantities. Every column
still declares a `units` key; `units: null` is valid only for identifier,
categorical, boolean and string columns (as for `session_id` above).

## Versioning

`<dataset_name>/<version>/` is the **package** version, semver.
`provenance.source.version` records the **upstream** version, independently.

- patch — prose or metadata fixes, no data change
- minor — tables, columns or modalities added without breaking existing readers
- major — schema-breaking change

A change of upstream `source.version` forces at least a minor bump.

