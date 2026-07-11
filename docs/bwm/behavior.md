# BWM Behavior Dataset Specification

## Status

`bwm_behavior` is the local behavior-focused companion dataset to `bwm_ephys`.
It is intended for compact, shareable wheel / pose / trial-behavior summaries
that are easy for users and LLM agents to query from stable parquet tables.
The actual dataset directory is user-local and should be resolved through
`data_locations.local.yaml`, not assumed to live under this repository.

The build is allowed to complete as a **partial dataset** when some sessions
lack wheel or pose assets; those sessions are recorded in the availability
tables and the build report instead of aborting the whole release.

Pose naming note: per camera, the build prefers Lightning Pose over DeepLabCut
whenever both trackers' datasets are present for a session (measured coverage:
LP available for 437/437 leftCamera, 433/433 rightCamera, 253/260 bodyCamera
sessions with any tracker; only 8 camera-instances are DLC-only). The local
`bwm_behavior` schema uses tracker-agnostic `pose_*` table, column, and
compression-profile names, such as `metadata/pose_availability.parquet`,
`features/pose_trial_features.parquet`, and `pose_present`. The `tracker`
column on `metadata/pose_availability.parquet` records which tracker
(`lightningPose` or `dlc`) was actually used for each `(eid, camera)` row.

Current implemented version:
- dataset name: `bwm_behavior`
- dataset version: `1.1.0`
- final total size on disk: `3.5G`
- inspect command: `uv run ibl-ai-agent inspect-bwm-behavior`
- build command: `uv run ibl-ai-agent build-bwm-behavior`
- refresh/ensure command: `uv run ibl-ai-agent refresh-bwm-behavior`
- current compression profile: `aggressive-pose-delta-wheel-native-left60-right60-body30`

## Current implemented layout

```text
<configured bwm_behavior version directory>/
  metadata/
    sessions.parquet
    trials.parquet
    events.parquet
    wheel_availability.parquet
    pose_availability.parquet
  features/
    trial_behavior_features.parquet
    wheel_trial_features.parquet
    pose_trial_features.parquet
    event_aligned_behavior_features.parquet
    behavior_session_features.parquet
    movement_state_epochs.parquet
    quiescence_state_epochs.parquet
    behavior_state_session_features.parquet
  sessions/
    <eid>.zip
  manifest.json
  schema.yaml
  provenance.yaml
  prefetch_report.yaml
  build_report.yaml
  feature_refresh_report.yaml
  build_state.yaml
  SUMMARY.md
```

## Data model

## Metadata tables

### `metadata/sessions.parquet`
One row per session.

Current columns include:
- `eid`
- `subject`
- `date`
- `session_number`
- `lab`
- `n_trials`
- `n_included_trials`
- `wheel_present`
- `pose_present`
- `present_cameras`

### `metadata/trials.parquet`
Trial table built from the BWM aggregate trials table.

This remains close to the standard BWM trial representation and includes core
behavior/event timing columns such as:
- `choice`
- `feedbackType`
- `probabilityLeft`
- `contrastLeft`
- `contrastRight`
- `stimOn_times`
- `goCue_times`
- `firstMovement_times`
- `response_times`
- `feedback_times`
- `bwm_include`

### `metadata/events.parquet`
Long-form event table derived from the trials table.

Primary key:
- `eid`, `event_id`

### `metadata/wheel_availability.parquet`
One row per session describing wheel availability.

Current columns:
- `eid`
- `wheel_present`
- `n_samples`
- `t_start`
- `t_end`

### `metadata/pose_availability.parquet`
One row per `(eid, camera)` describing camera pose availability.

Current columns:
- `eid`
- `camera`
- `pose_present`
- `n_frames`
- `t_start`
- `t_end`
- `tracker` (`lightningPose` or `dlc`; the tracker actually used for that camera/session)

## Feature tables

### `features/trial_behavior_features.parquet`
One row per trial.

Current columns:
- `eid`
- `trial_id`
- `signed_contrast`
- `choice_label`
- `correct`
- `reaction_time`
- `movement_time`
- `stim_to_feedback_time`

### `features/wheel_trial_features.parquet`
One row per trial for wheel summaries in a fixed trial window.

Current columns:
- `eid`
- `trial_id`
- `window_spec`
- `wheel_present`
- `movement_onset_time`
- `movement_peak_time`
- `movement_direction`
- `movement_amplitude`
- `mean_velocity`
- `max_velocity`

Current window semantics:
- `window_spec = stimOn:response`

### `features/pose_trial_features.parquet`
One row per `(eid, trial_id, camera)` for compact pose summaries.

Current columns:
- `eid`
- `trial_id`
- `camera`
- `window_spec`
- `pose_present`
- `feature_mean`
- `feature_peak`

Current window semantics:
- `window_spec = stimOn:feedback`

Current meaning:
- combine the available numeric pose/feature arrays for the camera
- compute a compact magnitude-like summary in the chosen trial window

### `features/event_aligned_behavior_features.parquet`
Long-form event-aligned behavior summary table.

Current columns:
- `eid`
- `trial_id`
- `signal_name`
- `event_name`
- `window_spec`
- `baseline`
- `peak`
- `peak_latency_ms`
- `mean_response`
- `modulation_index`

Current signals:
- `wheel`
- camera-level pose summaries such as `leftCamera`, `rightCamera`, `bodyCamera`

Current semantics:
- for wheel, use wheel velocity when available, otherwise wheel position
- for pose cameras, collapse available per-frame features into a magnitude-like
  summary before event alignment
- compute compact baseline/peak/latency/modulation summaries around each event

### `features/behavior_session_features.parquet`
One row per session.

Current columns:
- `eid`
- `n_trials`
- `n_included_trials`
- `performance`
- `median_reaction_time`
- `median_movement_time`
- `wheel_present`
- `pose_present`

### `features/movement_state_epochs.parquet`
One row per detected wheel movement epoch.

Current columns:
- `eid`
- `movement_id`
- `t_start`
- `t_end`
- `duration_s`
- `peak_amplitude`
- `peak_velocity_time`
- `source_signal`
- `detector_name`
- `detector_version`

Current semantics:
- movement epochs are derived from the existing IBL wheel detector rather than a
  repo-local custom movement heuristic
- the detector provenance is stored explicitly in the output rows

### `features/quiescence_state_epochs.parquet`
One row per quiescent interval derived as the complement of movement epochs.

Current columns:
- `eid`
- `quiescence_id`
- `t_start`
- `t_end`
- `duration_s`
- `derived_from`
- `min_duration_s`

Current semantics:
- quiescence is derived from the same canonical wheel movement detector
- only intervals meeting the configured minimum duration are retained

### `features/behavior_state_session_features.parquet`
One row per session describing wheel-defined state occupancy.

Current columns:
- `eid`
- `wheel_present`
- `movement_epoch_count`
- `quiescence_epoch_count`
- `fraction_time_moving`
- `fraction_time_quiescent`
- `median_movement_duration`
- `median_quiescence_duration`

## Secondary session shard store

The dataset still includes a packed per-session signal store under:
- `sessions/<eid>.zip`

This is a secondary/raw-ish layer used for richer follow-up work. It is not the
main interface for most reviewed behavior questions.

Current shard format is:
- `ibl_ai_agent_behavior_session_shard_v2`

Current shard contents may include:
- wheel timestamps / position / velocity
- per-camera timestamps / feature matrices
- metadata describing present cameras and column names

State derivation note:
- wheel state tables are derived with the canonical IBL wheel preprocessing and
  movement detector during feature generation
- the released dataset does not store a dense derived `1000 Hz` wheel grid just
  to preserve those states

Current release profile:
- wheel retained at native sample times
- `leftCamera` retained near native `60 Hz`
- `rightCamera` downsampled to `60 Hz`
- `bodyCamera` retained at `30 Hz`
- aggressive delta/quantized value encoding for wheel and pose arrays

Motion-energy and pupil note:
- whisker/body motion energy (`whiskerMotionEnergy`, `bodyMotionEnergy`) and left-camera
  pupil diameter are downstream estimations from pose tracking, so they are sourced via
  `brainbox.io.one.SessionLoader.load_motion_energy`/`load_pupil` alongside the pose
  keypoints (rather than a raw `.features.pqt`/`.ROIMotionEnergy.npy` glob) and folded
  into each camera's `features` matrix when their timestamps line up with the pose
  timestamps
- when a source's timestamps do not line up with the camera's pose timestamps, that
  source is recorded in the shard's per-camera `skipped_sources` list instead of being
  silently dropped

## Storage choices

### Primary analysis surface
- parquet metadata tables
- parquet feature tables

### Secondary signal surface
- zipped semantic session shards (`zip_semantic_shards_v2`)

This is intentional:
- table-first for most queries
- compact signal store retained for follow-up analyses

## Recommended usage

Use `bwm_behavior` when the question is primarily about:
- wheel dynamics
- pose / pupil / face-motion summaries
- behavior-first trial/session analyses
- event-aligned behavior summaries

Use `bwm_ephys` when the question is primarily about:
- neural activity
- spikes
- unit features
- neural event responses
- decoding from population spike counts

For multimodal analyses, join on:
- `eid`
- `trial_id`
- canonical `event_name`
- explicit `window_spec`

## Current maintenance commands

Build from cache (and optional prefetch):

```bash
UV_CACHE_DIR=.uv-cache uv run ibl-ai-agent build-bwm-behavior --output-root <external-data-root>
```

Build the `1.1.0` release directly:

```bash
UV_CACHE_DIR=.uv-cache uv run ibl-ai-agent build-bwm-behavior --target-version 1.1.0 --output-root <external-data-root>
```

Write or refresh only the release tar/checksum for an already-built dataset:

```bash
UV_CACHE_DIR=.uv-cache uv run ibl-ai-agent write-bwm-release-archive --dataset-root <bwm_behavior-version-dir>
```

The latest release archive is written to:

```text
reports/releases/bwm_behavior/1.1.0/bwm_behavior-1.1.0.tar
```

What the tar is for:
- a single-file portable distribution artifact for publishing or copying the full built dataset
- a deterministic packaging format with a sibling SHA-256 checksum file
- not the primary query surface for local analyses, which should keep using the unpacked `metadata/`, `features/`, and `sessions/` layout

The build/upgrade path also writes the release archive automatically, but the
tar-only command above avoids recomputing the dataset when only packaging is
needed.

Refresh/ensure derived feature tables in place:

```bash
UV_CACHE_DIR=.uv-cache uv run ibl-ai-agent refresh-bwm-behavior
```

Inspect the built dataset and cache coverage:

```bash
UV_CACHE_DIR=.uv-cache uv run ibl-ai-agent inspect-bwm-behavior
```

## What is deliberately not in v1

Not yet implemented as primary products:
- giant raw wheel stores as the main interface
- giant pose frame/keypoint stores as the main interface
- full trial × time dense behavior tensors

Those may be added later, but the current design prioritizes compact,
shareable, analysis-ready summaries.

## Related docs

- `docs/bwm/ephys.md`
- `docs/decisions/bwm_dataset_layering.md`
