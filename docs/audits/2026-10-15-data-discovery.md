# Data discovery audit and proposed repository improvements

Date: 2026-10-15. Status: findings and implementation proposal; no runtime guidance, dataset builders, or data-location configuration changed.

## Finding and correction

My first answer was incomplete. I checked the local BWM package and treated its coverage as the answer to the broader question. I should also have identified the relevant session-level dataset families and distinguished their availability from their existence as supported data products. Saying that a classifier would need to be built was premature: the installed IBL software already supports loading Lightning Action paw-state predictions.

The stronger claim that these predictions are already in our local data is **not supported** by this audit. All 459 packaged behavior shard manifests were inspected: none contains paw-state predictions. No paw-state files were found under the scanned `~/Downloads/ONE` cache, and neither cached public nor cached private dataset index contains a pawstates entry. These cached indexes do not establish current server availability. No server queries or downloads were performed.

The installed paw-state tools name the classes `background`, `still`, `move`, `wheel_turn`, and `groom`; their plots display four behavioral classes. The user's word “fidget” is not a verified synonym for `move`. These are model predictions, with probabilities and ensemble statistics, rather than manually validated behavioral annotations.

## What was inspected

Three parallel reviews covered behavior/video data, ephys/other local data, and discovery guidance. The consolidated findings below were checked against actual local schemas and installed sources.

- Both configured BWM dataset schemas and every declared Parquet table's columns/types/row count.
- All 459 behavior ZIP manifests and their named arrays/feature columns; signal arrays were not decoded for numerical quality analysis.
- Full-cluster waveform/ACG array headers, local LFP HDF5 root structure, and ephys-atlas cached table metadata/PID overlap.
- File discovery under the observed `~/Downloads/ONE` cache and its public/private cached dataset indexes. This path is not registered in the local configuration. Cache index entries are records of datasets, not proof of files downloaded locally.
- Installed `ibllib 4.0.1`, `ONE-api 3.4.1`, and `brainwidemap 0.1.0` loading/pipeline code; a cached Lightning Pose file and pupil-features file were inspected for actual columns.
- Repository skills, BWM documentation, builders, and relevant tests.

This is a repository/data-product inventory, not an empirical scientific analysis. It does not establish classifier accuracy, signal usability, current remote availability, full cache completeness, or that every optional table column has populated values. Cached atlas features received a targeted review; the entire ONE cache was not semantically cataloged.

The [machine-readable inventory](2026-10-15-data-inventory.json) records all declared BWM table columns/types/counts, camera feature coverage, array shapes, and supplementary dataset facts. Machine-specific paths and counts below describe this audit snapshot.

## Paw and video products

| Information a user might request | Dataset or API | Availability found | Interpretation and limitations |
|---|---|---|---|
| Paw stillness, movement, wheel turning, grooming | `alf/lightningaction/_ibl_{left,right}Camera.pawstates.pqt`; `SessionLoader.load_pawstates()` | Supported by installed software; absent from packaged BWM shards and searched cache/indexes; live coverage unknown | Per-paw class probabilities and ensemble statistics. Labels can be derived; do not equate `move` with fidgeting without validating the intended definition. |
| Paw trajectories | `*Camera.lightningPose.pqt` or `*Camera.dlc.pqt`; locally, camera feature arrays in behavior ZIP shards | Stored locally where the corresponding side camera is present | `paw_l` / `paw_r` x, y, likelihood. Pixel coordinates, camera dependent; confidence, occlusion, and geometry matter. These are not action labels. |
| Pose uncertainty | Lightning Pose ensemble median/variance/posterior-variance columns | Present in an inspected cached raw LP file; stripped from the compact BWM v2 camera features by the upgrade code | Useful uncertainty information is available in some source products but is not preserved in the compact dataset. Exact raw schemas can vary. |
| Tongue, nose, pupil landmarks, reference tube | Camera pose keypoints | Stored in side-camera shard feature matrices | Supports focused follow-up; tongue position alone is not an extracted lick-event dataset. |
| Pupil size | `*Camera.features.pqt`; `SessionLoader.load_pupil()` | Actual inspected file contains `pupilDiameter_raw` and `pupilDiameter_smooth`; left-camera pupil can be folded into compact shards | Distinguish pupil diameter from pupil landmark coordinates; numerical usability and timestamp alignment still require checks. |
| Whisker-pad/body motion | `*Camera.ROIMotionEnergy.npy`; `SessionLoader.load_motion_energy()` | Motion-energy columns embedded in compact camera feature matrices where available | Image-change signal in an ROI; does not identify an action. This is separate from `features.pqt`. |
| Lick event times | `licks.times`; `SessionLoader.load_licks()` | Installed loader and cached index entries verified; not a dedicated declared BWM behavior table | Index presence does not establish a downloaded file or BWM session coverage. Validate event extraction separately from tongue tracks. |
| Video frames | Camera video files | Not part of the compact BWM behavior package | Pose tables do not contain video pixels. Video would be a separate, potentially larger modality. |

The compact behavior package has 459 sessions and wheel data in all 459. Pose data are present in 444 sessions: left camera in 437, right camera in 432, body camera in 260. These counts overlap. Lightning Pose accounts for 436/437 left, 432/432 right, and 253/260 body camera records; the remaining eight camera records use DLC. Exact per-column shard coverage is in the JSON inventory.

All 437 left-camera and 432 right-camera shard manifests include both paw x-coordinate columns; this is structural coverage, not proof of valid tracking on every frame. The existing local decoding entry point is `ibl_ai_agent.datasets.bwm_behavior_compression.read_behavior_session_shard(path)`, which returns metadata and reconstructed arrays; a discovery overview should make this route visible alongside Parquet loading.

Source evidence:

- [Pose, motion-energy, pupil, and paw-state loader methods](../../.venv/lib/python3.12/site-packages/brainbox/io/one.py), especially `load_pose`, `load_motion_energy`, `load_pawstates`, and `load_pupil` around lines 1448–1585. Environment-specific evidence; a future durable catalog should record package/version and public API names rather than depend on a `.venv` path.
- [Paw-state class constants and probability processing](../../.venv/lib/python3.12/site-packages/brainbox/behavior/pawstates.py), around lines 22–56 and 118–155. Plot helpers derive labels via argmax and have their own missing-data/background assumptions; they should not be adopted as a general classifier-validation policy.
- [Lightning Action pipeline outputs](../../.venv/lib/python3.12/site-packages/ibllib/pipes/video_tasks.py), around lines 1060–1098.
- [BWM pose payload builder](../../ibl_ai_agent/datasets/bwm_behavior.py), `_prepare_pose_payload` around lines 1310–1419: loads pose, motion energy, and pupil, but never pawstates.
- [Pose upgrade filtering](../../ibl_ai_agent/datasets/bwm_behavior_upgrade.py), around lines 194–216: drops pose uncertainty/ensemble fields.

## Other valuable higher-level information

| Information | Existing local source | What a useful overview should say |
|---|---|---|
| Choice, correctness, contrast, reaction time, movement time, task duration | `bwm_behavior` trial tables and `trial_behavior_features` (295,920 rows) | Trial-level derived behavior already exists. Define each latency by event endpoints and show inclusion masks. |
| Wheel movement bouts and quiet intervals | `movement_state_epochs` (673,459 rows), `quiescence_state_epochs` (665,722), and state/session summaries | Already detected bouts and occupancy summaries; quiescence means wheel-defined quiescence, not paw/body stillness. |
| Wheel movement amplitude, direction, timing, velocity | `wheel_trial_features` (295,920 rows), native wheel arrays in session shards | Supports trial-specific movement queries without reprocessing wheel signals. Verify windows/units in provenance. |
| Event-aligned behavior summaries | `event_aligned_behavior_features` (5,062,275 rows) | Baseline, peak, mean response, peak latency, modulation around stimOn/goCue/firstMovement/response/feedback; wheel or camera-wide signals, not paw-state classifications. |
| Event-aligned neuronal responses | `bwm_ephys/features/event_response_features` (376,975 rows) | Per-unit baseline/peak firing rate, latency, modulation, event count, and window specification. Compact summaries are not full PSTHs or statistical evidence of selectivity. |
| Passive sensory responses and spontaneous periods | `metadata/passive_events` (6,959,652 rows), `features/passive_response_features` (577,566 rows) | Includes gabor, tone, noise, valve, RF-mapping frames, and protocol/period markers. Period markers are not interchangeable with stimulus onsets; a 459-row availability table does not prove every modality is present in every session. |
| Unit quality and waveform features | Good-unit `unit_features` (75,395 rows), full `clusters.pqt` (621,733 rows) | Firing rate, contamination, drift, missed-spike estimates, waveform shape and other fields exist. Column existence does not guarantee nonmissing values. Full-cluster and good-unit populations differ. |
| Waveform shapes and spike autocorrelograms | Root `clusters.waveforms_peak.npy` and `clusters.acgs_log.npy`, each 621,733 × 128; shared ACG time axis | Already stored; do not recompute unnecessarily. Arrays align with the full cluster table, not the 75,395-row good-unit table. |
| Anatomical location and channel labels | Unit/channel tables with atlas/Beryl IDs/acronyms, coordinates, depth, channel labels | Enables anatomical queries, subject to atlas mapping and localization/QC semantics. |
| Spectral, waveform, and recording-quality features | Cached `ephys_atlas_features/ea_active/2026_W32/agg_full/raw_ephys_features.pqt` | 384,768 rows, 53 columns, 1,002 PIDs; includes PSD bands, aperiodic exponent/offset, AP/LF RMS, spike/waveform features. Overlaps 695/699 BWM PIDs. Join/channel/revision compatibility is not yet validated; some metrics require definitions/units checked. |
| Compressed LFP and saturation information | `reports/datasets/bwm_lfp/lf_compressed_all_bwm.h5` | File exists (15,546,004,168 bytes), with 699 PID groups. Not registered in local YAML and no schema sidecars. Installed reader exists. Version and sync correction status are unverified, so do not assume current documented timing guarantees. |

Important semantic problem: `pose_trial_features.feature_mean/feature_peak` average the absolute values across the camera feature matrix. They combine coordinates, likelihoods, and optional motion/pupil signals with different units. Camera event summaries use a similar aggregate. They should not be advertised as an interpretable measure of paw speed, general movement, grooming, or arousal. Named source tracks remain available for constructing a meaningful quantity after appropriate scientific planning.

Another naming trap: the builder assigns `wheel_trial_features.movement_peak_time` from `response_times`, rather than detecting a peak in wheel velocity (`bwm_behavior.py` around line 846; shard feature reconstruction repeats this assignment). Before using this field as a movement peak, verify the stored release and correct or explicitly document its semantics. The implemented `reaction_time` is firstMovement minus stimOn, and `movement_time` is response minus firstMovement; these definitions should be visible in the catalog rather than inferred from the names.

Evidence: [camera summary implementation](../../ibl_ai_agent/datasets/bwm_behavior.py), `_summarize_pose_window` around lines 1061–1074 and camera aggregation around lines 855–860; [BWM behavior specification](../bwm/behavior.md); [LFP specification](../bwm/lfp.md); actual schemas/counts in the JSON inventory.

## Why the information was missed

1. **I stopped discovery at the compressed BWM boundary.** The local-first loading rule is appropriate, but I incorrectly used it as a boundary on conceptual discovery. Absence from a derived package does not imply absence from IBL data products or installed loaders. The rule does not forbid describing a missing modality.
2. **The skill entry point targets loading, not capability questions.** [ibl-load](../../skills/ibl-load/SKILL.md) routes wheel, pose, trial and BWM loading but has no concept-to-product index for questions such as “do we have grooming labels?” My search should have included installed loader capabilities earlier.
3. **The fallback reference is ephys-focused.** [bwm_fallback_preflight.md](../../skills/ibl-load/references/bwm_fallback_preflight.md) centers on `bwm_units`/`clusters.pqt`, sorter collections and probe fields. It does not lead a behavior query to paw-state or lick products.
4. **Schemas name files and keys, not scientific meanings.** Root schemas expose tables and primary keys; camera landmarks are deeper in ZIP manifests. A generic pose-summary table conceals useful paw/tongue/pupil fields from a quick table inventory.
5. **Data-product existence, local presence, and live availability are conflated.** There is no standard presentation for “supported API, not packaged, not cached, upstream unknown.” Both unsupported absence claims and unsupported availability promises become easy.
6. **Machine discovery is incomplete.** `one_cache.root` is null although a cache exists under `~/Downloads/ONE`; LFP is physically present but unregistered. A discovery tool that only follows valid configured schemas misses useful local assets.
7. **Documentation mixes snapshots, current download targets, and design.** Local ephys is 1.2.0; the downloader and detailed ephys doc name 1.2.1, while the overview names 1.2.0. Detailed passive-row counts differ from the installed version. The detailed ephys page explicitly mixes implemented content and future design. A user needs these scopes labeled separately.

The immediate error was mine; these repository gaps made it easier to stop too early and harder to give a complete, qualified first answer.

## Proposed implementation plan

### 1. Add a small, canonical concept-to-data catalog

Create `skills/ibl-load/references/data_capabilities.md`, with user concepts and aliases, product/file/API, row grain, units/meaning, producer, availability check, provenance/version, and key caveats. Start with the families in the two tables above. Give predicted classes, raw measurements, stored summaries, and potentially derivable quantities distinct entries.

The catalog must not promise that every supported product exists for every BWM session. “Fidget” should route to paw-state/movement candidates with an explicit definition gap. Preserve probabilities, confidence, and uncertainty in the description rather than presenting action labels as ground truth. Avoid duplicating loading-order policy or full API tutorials.

### 2. Wire capability questions into the existing skills

- `skills/ibl-load/SKILL.md`: add availability/inventory questions to triggers, link the catalog, and require stating scope before declaring a field unavailable.
- `skills/ibl-load/references/data_loading.md`: add a short discovery workflow: concept catalog → configured schema/embedded manifests → configured or known cache metadata → installed source verification → authorized live metadata lookup if needed.
- `skills/ibl-load/references/bwm_runtime_policy.md`: one short clarification that local-first execution does not restrict conceptual product discovery. Keep all BWM loading-order policy here.
- `skills/ibl-analyze/references/brainbox_routing.md`: link paw/action/lick/pupil queries to the catalog and loader route; keep scientific caveats in their existing canonical location.
- `docs/bwm/README.md` and `docs/bwm/behavior.md`: add a compact “questions this package answers” overview and explicit exclusions (pawstates, raw video, retained versus discarded LP uncertainty); link the canonical catalog rather than reproducing it.
- `docs/bwm/ephys.md`: label local snapshot versus release target and separate future design from shipped surfaces. Explain full-cluster versus good-unit alignment near feature routes.

Include a short semantics review of misleading existing fields (`movement_peak_time`, generic camera magnitudes) before promoting them in the overview. Renaming or recomputing release data would be a separately reviewed, versioned change.

### 3. Follow-on option: add a lightweight local inventory command

Propose a small utility in `ibl_ai_agent` with a CLI entry point that emits JSON plus a human-readable summary. It should resolve configured roots, inspect declared Parquet schemas and array/ZIP metadata, identify detected-but-unregistered datasets, and optionally inspect a supplied ONE cache. Default to metadata-only; no automatic remote calls, authentication, downloads, or signal decoding.

Each capability should report independent evidence fields: `supported_by_software`, `packaged_in_release`, `present_locally`, `listed_in_cached_index`, `live_availability`, and `derivable_with_analysis`. These are separate dimensions, not one mutually exclusive status. Add paths, package/dataset versions, inspection timestamp, counts/denominators, row keys, and missing/unknown reasons. An old cached index must never become proof of remote absence.

Ignore AppleDouble `._*` files and build-checkpoint directories in dataset discovery. They coexist with the actual Parquet files and are not corrupted copies of the data. Surface missing sidecars/configuration without silently registering or redownloading anything. For this machine, suggest configuring the existing cache and validating/registering the LFP file after provenance checks.

### 4. Verify live paw-state coverage before extending a dataset

As a separate, explicitly scoped follow-up, query dataset metadata for the BWM roster and `alf/lightningaction/*Camera.pawstates.pqt`, distinguishing public/private access and left/right camera coverage. Start with a small metadata query, estimate the cost of full discovery, and preserve the availability result. No need to redownload existing BWM data to establish this.

If predictions exist and the user wants them locally, propose a versioned companion/extension that stores timestamps, per-paw probability columns, ensemble statistics, producer/model revision, missingness, and any optional derived labels. Verify background handling, class-column order, probability validity, and clock alignment on a small example before scaling. Do not replace the source probabilities with unqualified hard labels. If source predictions are unavailable, explain that separately before proposing model execution or new annotation.

### 5. Add regression checks for discovery behavior

- Use synthetic local fixtures to test schema + camera-manifest inventory, an installed-supported but missing product, stale cache indexes, detected unregistered files, AppleDouble exclusion, and the no-network default.
- Check that paw/groom/still queries resolve to both trajectories and action probabilities, with local availability kept separate; `move` is never silently renamed to fidget.
- Check wheel quiescence is never described as whole-animal stillness, and generic camera summaries are not described as paw speed.
- Extend [skill-reference checks](../../tests/test_skill_references.py) to verify catalog routing and valid links. These structural checks supplement the semantic fixtures; they do not by themselves prove good answers.
- Run the focused inventory/routing tests, existing affected dataset tests if builder code changes, link checks, and `scripts/report_skill_sizes.py` for any skill edits. Keep static catalog text compact and machine-specific inventories separate.

Recommended first implementation: the behavior/video portion of steps 1–2 plus focused discovery checks from step 5. Extend the catalog to the other audited families and resolve the documentation discrepancies in a second phase; step 3 is an optional tooling phase that makes inventories reproducible. Do step 4 only to resolve live availability, with a builder extension conditional on its result. There is no reason to redownload the current behavior archive for this discovery fix.

## Example of the first answer this should enable

> The related products are paw trajectories (`lightningPose`/`dlc`), paw-action probabilities (`pawstates`, with still/move/wheel_turn/groom classes), wheel movement intervals, motion energy, and pupil/lick measurements. Our local BWM package contains the trajectories and wheel/motion signals, but not paw-action predictions. The installed software supports pawstates; I have not verified live coverage for these BWM sessions. Wheel quiescence does not establish paw stillness, and “fidget” needs a definition before mapping it to the model's `move` class.

## Delivery and remaining limits

Delivered: this consolidated review and a machine-readable local inventory. No instruction edits, production tooling changes, configuration changes, remote lookups, or downloads have been made. This proposal is reviewable independently of whether pawstates are ultimately published for the BWM roster.
