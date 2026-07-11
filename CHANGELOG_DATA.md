# Data Changelog

All notable changes to the IBL AI Agent datasets are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Dataset versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

---

## [bwm_behavior 2.0.0] - 2026-07-11

### Added
- Wheel data now available for ~73 previously-missing sessions (of 458).
- `tracker` column on `metadata/pose_availability.parquet` recording which tracker
  (`lightningPose` or `dlc`) was used for each `(eid, camera)` row.

### Changed
- Wheel `timestamps`/`position`/`velocity` are now on a uniform 100 Hz grid with
  filtered velocity, replacing the earlier irregular sampling. Re-validate
  wheel-based analyses.
- Pose tracker source now prefers Lightning Pose over DeepLabCut per camera
  (measured coverage: LP available for 437/437 leftCamera, 433/433 rightCamera,
  253/260 bodyCamera sessions with any tracker; only 8 camera-instances total are
  DLC-only).
- Whisker/body motion energy and left-camera pupil diameter are now sourced via
  `brainbox.io.one.SessionLoader.load_motion_energy`/`load_pupil` instead of a raw
  `.features.pqt`/`.ROIMotionEnergy.npy` glob, since both are downstream
  estimations from pose tracking.
- All `dlc_*` tables, columns, and compression-strategy names are renamed to
  tracker-agnostic `pose_*` (e.g. `metadata/dlc_availability.parquet` ->
  `metadata/pose_availability.parquet`, `balanced-dlc-delta` ->
  `balanced-pose-delta`). This is a breaking rename; there is no backward-compat
  shim. Re-validate any analysis referencing the old `dlc_*` names.

---

## [bwm_ephys 1.2.0] - 2026-06-08

### Added
- `clusters.pqt` (621 733 × 59): full-release BWM unit table covering all 699 probe
  insertions. Replaces the `2024_Q2_IBL_et_al_BWM` aggregate `clusters.pqt`.
  Includes `eid` and 25 new columns relative to the 2024_Q2 aggregate:
  - Firing statistics: `burstiness`, `memory`
  - QC / annotation: `labels`, `rawInd`, `peak_channel`, `invert_sign_peak`
  - Waveform shape: `peak_time_idx`, `peak_val`, `trough_time_idx`, `trough_val`,
    `tip_time_idx`, `tip_val`
  - Waveform timing: `peak_to_trough_duration`, `half_peak_duration`,
    `half_peak_post_time_idx`, `half_peak_pre_time_idx`,
    `half_peak_post_val`, `half_peak_pre_val`,
    `recovery_time_idx`, `recovery_val`
  - Waveform slopes / ratios: `peak_to_trough_ratio`, `peak_to_trough_ratio_log`,
    `depolarisation_slope`, `repolarisation_slope`, `recovery_slope`
- `clusters.waveforms_peak.npy` (621 733 × 128, float16): peak-channel waveform
  per unit at 30 kHz. Row-aligned with `clusters.pqt`.
- `clusters.acgs_log.npy` (621 733 × 128, float16): log-binned autocorrelogram
  per unit. Row-aligned with `clusters.pqt`.
- `acgs_log.times.npy` (128, float64): shared ACG lag-time axis in seconds.
- generation code on IBL cluster is available [here](https://github.com/int-brain-lab/sdsc-slurms/blob/main/2026-03_EA_Cells/cells.py), leveraging the [eatools](https://github.com/int-brain-lab/eatools) library.



---

## [bwm_ephys 1.1.0] - 2026-02 *(baseline)*

Initial public release of the `bwm_ephys` archive containing:
- `metadata/`: sessions, insertions, units, channels, trials, events, passive sessions/events.
- `features/`: unit features, event-response features, passive response features.
- `spikes/`: per-probe blosc-compressed spike shards (good units only).

---

[Unreleased]: https://github.com/int-brain-lab/ibl-ai-agent/compare/v0.2.0...HEAD
[bwm_ephys 1.2.0]: https://github.com/int-brain-lab/ibl-ai-agent/releases/tag/v0.2.0
[bwm_ephys 1.1.0]: https://github.com/int-brain-lab/ibl-ai-agent/releases/tag/v0.1.0
