## Purpose
Route IBL analysis tasks to useful `brainbox` modules before writing custom code.

Confirm exact signatures from installed `brainbox` or official docs when needed.

## Routing Map
| Question shape | Prefer |
| --- | --- |
| PSTH, aligned rasters, baseline-versus-evoked firing, latency | `brainbox.singlecell.calculate_peths`, `bin_spikes`, `bin_spikes2D`; plotting helper `peri_event_time_histogram` |
| population prediction of choice/stimulus/movement/block | `brainbox.population.decode.get_spike_counts_in_bins`, `classify`, `regress`, `lda_project` |
| shared dynamics or inter-area coupling | `brainbox.population.cca.bin_spikes_trials`, `preprocess`, `fit_cca`, `get_correlations` |
| event responsiveness or condition selectivity | `brainbox.task.closed_loop.responsive_units`, `differentiate_units`, ROC helpers, `compute_comparison_statistics` |
| trial grouping and condition PSTHs | `brainbox.task.trials.find_trial_ids`, `filter_trials`, `get_event_aligned_raster`, `get_psth` |
| wheel movement, quiescence, velocity | `brainbox.behavior.wheel.interpolate_position`, `velocity_filtered`, `movements`, `get_movement_onset`, `traces_by_trial` |
| behavioral performance, psychometrics, reaction time | `brainbox.behavior.training.compute_performance`, `compute_psychometric`, `compute_reaction_time` |
| licks, pupil, paw speed, pose-feature motion | `brainbox.behavior.dlc.get_licks`, `get_pupil_diameter`, `get_speed`, `get_speed_for_features` |
| behavioral labels or unfamiliar data products | `../../ibl-load/SKILL.md` for fresh metadata and installed-loader discovery before selecting a metric; distinguish predictions from measurements and unresolved user terminology |
| unit QC, contamination, presence ratio, drift | `brainbox.metrics.single_units.*`, `brainbox.metrics.electrode_drift.estimate_drift` |
| ISI, synchrony, spike-train rates | `brainbox.spiking.isi.*`, `brainbox.spiking.rate.bin_spikes`, `brainbox.spiking.synchrony.*` |
| cluster/unit preparation | `brainbox.processing.get_units_bunch`, `compute_cluster_average`, `filter_units`, `bin_spikes` |
| RDM/RSM/reliability | `brainbox.neural.rdm.*`, `brainbox.neural.correlation.*` |
| passive receptive fields | `brainbox.task.passive.*`, `brainbox.rfs.rfs.sta` |

## Decision Rules
- For BWM questions, define the release/session/unit/trial subset from BWM references before choosing a `brainbox` operator.
- Use wheel/pose functions as preprocessing; do not call their outputs neural effects until the neural statistic is defined. Some helper modules still have legacy `dlc` names.
- If no operator matches the scientific metric semantics, use question-specific NumPy/Pandas/Scipy code and state why.
- Do not force a `brainbox` function just because it is available.
