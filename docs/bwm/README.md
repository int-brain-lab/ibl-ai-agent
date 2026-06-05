# BWM Dataset Overview

This is the quick entry point for Brain Wide Map dataset layouts used by this
repo. The actual large datasets should live outside the repository and be
located through `data_locations.local.yaml`; see `../data_locations.md`.

## Why These Datasets Exist

The original BWM data surfaces are comprehensive, but common agentic analyses
need a smaller, explicit, local representation with stable schemas and fast
loading paths. These derived datasets are intended to:

- keep public example analyses local and reproducible;
- reduce repeated remote queries and heavyweight loading;
- expose the tables and spike shards an agent is expected to inspect;
- make dataset version, schema, and caveats easier to report.

## `bwm_behavior`

Local behavior companion dataset for wheel, pose, trials, and event-aligned
behavior summaries.

- Config key: `datasets.bwm_behavior.root`
- Version: `1.1.0`
- Current compression profile: `aggressive-dlc-delta-wheel-native-left60-right60-body30`
- Approx. size on disk: `3.5G`
- Approx. file count: `476`
- Sessions: `459`
- Trials: `295,920`
- Session shards written: `458`
- Wheel sessions written: `396`
- Pose sessions written: `453`
- Main contents:
  - `metadata/sessions.parquet`
  - `metadata/trials.parquet`
  - `metadata/events.parquet`
  - `metadata/wheel_availability.parquet`
  - `metadata/dlc_availability.parquet`
  - `features/trial_behavior_features.parquet`
  - `features/wheel_trial_features.parquet`
  - `features/dlc_trial_features.parquet`
  - `features/event_aligned_behavior_features.parquet`
  - `features/behavior_session_features.parquet`
  - `features/movement_state_epochs.parquet`
  - `features/quiescence_state_epochs.parquet`
  - `features/behavior_state_session_features.parquet`

Pose naming note: current upstream IBL BWM releases include Lightning Pose
estimates where available. The local schema still keeps legacy `dlc_*` table,
column, and compression-profile names for compatibility with existing artifacts.

Best detailed references:
- [Dataset spec](./behavior.md)

## `bwm_ephys`

Local ephys-centered BWM dataset with spikes, units, insertions, channels,
trials, events, and passive-response context.

- Config key: `datasets.bwm_ephys.root`
- Version: `1.2.0`
- Public archive size: `6.03G`
- Previous `1.1.0` local-table/spike footprint: `5.3G`
- Approx. file count: `4,215`
- Mice: `139`
- Sessions: `459`
- Insertions: `699`
- Units: `75,395`
- Full cluster rows in root `clusters.pqt`: `621,733`
- Channels: `267,264`
- Trials: `295,920`
- Events: `2,066,041`
- Spike store: `4,152,659,397` spikes written
- Main contents:
  - `metadata/sessions.parquet`
  - `metadata/insertions.parquet`
  - `metadata/units.parquet`
  - `metadata/channels.parquet`
  - `metadata/trials.parquet`
  - `metadata/events.parquet`
  - `metadata/passive_sessions.parquet`
  - `metadata/passive_events.parquet`
  - `features/unit_features.parquet`
  - `features/event_response_features.parquet`
  - `features/passive_response_features.parquet`
  - `spikes/<pid>/...` blosc shard directories
  - `clusters.pqt` full cluster table
  - `clusters.waveforms_peak.npy` peak-channel unit waveforms
  - `clusters.acgs_log.npy` log-binned autocorrelograms
  - `acgs_log.times.npy` shared ACG lag-time axis

Compatibility note: the `metadata/units.parquet`, `features/*`, and `spikes/*`
surfaces keep the good-unit local analysis contract from `1.1.0`. The new
root-level `clusters.pqt` and companion arrays expose the full cluster table and
cell-level waveform/ACG surfaces for analyses that need them.

Best detailed references:
- [Dataset spec](./ephys.md)

## Proposed Compact Query Layer

Many broad questions about where task, movement, pose, or behavioral
information is represented in the brain should not require downloading or
scanning both full source-like datasets. A proposed `bwm_neurobehavior` layer
would precompute compact per-unit PETH features, behavior cross-correlations,
QC/location fields, and ephys-atlas-style features.

Design references:
- [Dataset layering decision](../decisions/bwm_dataset_layering.md)

## Supporting Docs

See the detailed behavior and ephys pages in this directory for schema notes and
loading guidance.
