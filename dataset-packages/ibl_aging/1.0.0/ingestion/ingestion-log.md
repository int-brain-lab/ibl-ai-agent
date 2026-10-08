# Ingestion log: ibl_aging 1.0.0

Skill: `skills/data-ingest/SKILL.md` v0 (repo `ibl-ai-agent-ingest`, branch
`ingestion-pilot-aging`, commit 1ec84e0). Date: 2026-09-30.

## Status
**Full build done 2026-10-01 (4 workers), checked; 0 conversion failures.** Registered in `data_locations.local.yaml`. 

## Environment
- Python 3.12.13, from the repo's uv `.venv`.
- `spikepack` 0.1.0 @ `30ab06ff7794d89cab17cb54338db869ad90d036`. 
  - The first `uv pip install` went into the active conda env `cloudspace`, not `.venv`.
    It was uninstalled from there and reinstalled with `--python .venv/bin/python`.
  - Plain `uv run` re-syncs and removes it, so run everything with `uv run --no-sync`.
- ONE: `ibllightning.OneLightningAI` , OpenAlyx, `cache_dir` = read-only S3 mount.
  - **Added `tables_dir=/teamspace/studios/this_studio/Downloads/ONE/openalyx_tables`**
    to the connection the user gave. Without it, `OneLightningAI` defaults `tables_dir` to
    `one.params.get_cache_dir()`, which on this machine is the read-only S3 mount. The
    user was told and did not object.

### Session 2 (2026-10-01)
The previous session's conversation was lost; its work on disk was read back.
- Checked: `.venv/bin/python` 3.12.13, spikepack 0.1.0 importable, `ibllightning` from
  `../ibl-aws/src` 
- `tables_dir` (`~/Downloads/ONE/openalyx_tables`) no longer exists; it is expected to be
  recreated on the next connection.
- Machine now: 4 CPUs, 15 GB RAM (13 GB available), 352 GB disk free. The earlier
  estimate assumed 27 GB RAM.
- **The 3-session sample build is gone.** It isn't in the package or the Studio home,
  and its location was never logged. It was most likely in the previous session's `/tmp`
  scratchpad, which duplication did not copy. Its numbers below stand as recorded, but the
  files can't be re-inspected.

## Questions asked and answers
| # | Question | Answer |
| --- | --- | --- |
| 1 | Alyx has no strain/line/genotype for any subject. What should be recorded? | "C57BL/6 - this is in the paper". Recorded as strain; line and genotype left null. |
| 2 | How should age appear in the design? | Continuous age only; no groups. |
| 3 | Which units go in the shards? | Only good units, `label == 1`. |
| 4 | How should the full build run (4 workers ~1.3 h / sequential ~4.3 h)? | Not yet. |
| 5 | (2026-10-01) Before any full build, what first: re-run sample, unasked questions, sorter options, subset option? | Re-run the 3-session sample only. |
| 6 | (2026-10-01) Full build or subset (sequential ~3.9 h / 4 workers, parallel mode to be written)? | Full build, 4 workers. |
| 7 | (2026-10-01) Register the package and run a fresh-agent check? | Yes to both. |
| 8 | (2026-10-01) Aim, hypotheses, other confounds (asked once, as the skill says)? | Use the paper (Nat. Commun., doi:10.1038/s41467-026-74227-1). Known confound: the sorter/age/lab one found in the data. Rig changes, surgery problems, cohort effects: none known beyond the paper; log anything unclear as a question for the authors. |


The aim/hypotheses/confounds question was asked once on 2026-10-01 (row 9).

## Established from the source (measured, not inferred)
- The release tag `2025_Q3_Zang_et_al_Aging` covers 497 sessions, 767 insertions and 158
  subjects, from 12 labs, 2019-11-26 to 2023-10-19. The tag's description is
  doi 10.1101/2025.08.22.671763.
- It is a superset of BWM: all 459 sessions and 699 probes of
  `2024_Q2_IBL_et_al_BWM_iblsort`, plus 38 sessions and 68 probes. (A first overlap
  check gave 0 because of a UUID/str type mismatch; corrected.)
- **The aging tag carries no spike sorting for the 699 BWM probes.** It carries
  `iblsorter` sorting for 64 of the 68 extra probes. BWM probes use the sorting that
  `2024_Q2_IBL_et_al_BWM_iblsort` tags: `pykilosort`, revision `2024-05-06`, all 699.
- **763 of the 767 tagged probes get spike shards.** The other 4 have no `spikes.times`
  under any collection or revision on Alyx, so there is nothing to convert. Each is the
  second probe of a non-BWM session whose other probe is `iblsorter`-sorted. They are
  **not dropped**: all 767 probes stay in `recordings` (and their channels are not
  loaded), with `has_spikes = false`, `sorter = null`, and no shard or units.
  | pid | probe | session | subject |
  | --- | --- | --- | --- |
  | daadb3f1-bef2-474e-a659-72922f3fcc5b | probe00 | fe0ecca9-9279-4ce6-bbfe-8b875d30d34b | CSHL072 |
  | ee2ce090-696a-40f5-8f29-7107339bf08e | probe01 | 6f321eab-6dad-4f2e-8160-5b182f999bb6 | SWC_029 |
  | 57edc590-a53d-403c-9aab-d58ee51b6a24 | probe01 | 022dd14c-eff2-470f-863c-e019fafa53ae | CSHL068 |
  | 61bb2bcd-37b4-4bcc-8f40-8681009a511a | probe01 | f31752a8-a6bb-498b-8118-6339d3d74ecb | SWC_029 |
- **Sorter per probe** (rule in `convert.py`: BWM session → BWM sorting; otherwise the
  aging tag's sorting): 699 probes `pykilosort` revision 2024-05-06, 64 `iblsorter`,
  4 none. By session: 459 pykilosort-only (all BWM), 34 iblsorter-only, and 4 iblsorter
  plus one unsorted probe. No session mixes sorters, and no subject appears in both
  groups (139 BWM mice, 19 non-BWM mice).
- **Sorter vs age.** Session age for pykilosort/BWM is 92–461 days (median 182). For
  iblsorter/non-BWM it is 317–597 days (median 530). The point-biserial r(age,
  iblsorter) is 0.75. Share of sessions sorted with iblsorter, at or above each age:
  ≥300 d 38/73 (52 %); ≥365 d 32/44 (73 %); ≥450 d 27/30 (90 %). Only 9 BWM mice have a
  session ≥300 d, and 1 has a session ≥450 d.
- Trials: each session has exactly one default tagged `_ibl_trials.table` revision, and
  it is always the latest, so ONE's default revision resolution matches the tag.
- Trials `ALFWarning: Multiple revisions: "", "2025-03-03"` (checked 2026-10-01, ~430
  occurrences in the full build). ONE warns when the files of one object resolve to different
  revisions. BWM sessions: the tagged default table is `2025-03-03`, passed explicitly; e.g.
  `goCueTrigger_times`, `intervals_bpod` and `quiescencePeriod` exist only unrevisioned, so they
  come from `""`, while `stimOff_times` and the table resolve to 2025-03-03, the default and
  tagged file. No carried column is taken from an older file. Non-BWM sessions (tagged default
  `""`, passed as `None`): 0 of 38 have more than one revision of any trials file. Harmless.
- Biased blocks: in all 3 sampled sessions, `probabilityLeft` = 0.5 for trials 1–90, then
  0.2/0.8. `build()` also checks every session for 0.2/0.8 values before writing
  `probabilityLeft`. The repo docs describe biased blocks but don't map protocol names to
  them, so this was measured in the data.
- Clock: in the sampled probes, spike times (≈0–7200 s) contain the trial interval
  (≈13–5803 s). This is consistent with upstream sync to one session clock. The time base
  is declared from IBL ALF convention plus this check.
- The 38 extra sessions are all from mice aged 317–597 days, 36 from churchlandlab and 2
  from mrsicflogellab, all recorded in 2020.
- Probe models: 3B2 590, 3A 173, NP2.4 4.
- Ages: 92–597 days at session (median 186). The within-subject spread is at most 151
  days. Sessions per subject range from 1 to 13.
- Units checked in ibllib source (`brainbox.metrics.single_units`): `amp_median` in volts
  (compared with `50 / 1e6`); `drift` in µm/hour (`sum|Δdepth| / duration × 3600`).
  `mlapdv` is in µm from bregma (`brainbox.io.one`: atlas metres × 1e6).

## Inferred (not blocking; flagged)
- Units taken from the IBL ALF convention and not independently verified: `rewardVolume`
  in µL; wheel position in radians; pose in pixels.
- The `choice` description gives the wheel direction only; no stimulus-side mapping is
  asserted.

## Skipped
- Trials column `reaction_time`: absent upstream, so skipped and recorded.
- Trials attributes not carried: `intervals_bpod`, `quiescencePeriod`,
  `stimOnTrigger_times`, `stimOffTrigger_times`.
- `rig`: not in the Alyx session list, so null.
- Passive protocol, LFP, raw ephys, video, pose, wheel, licks, motion energy: referenced in
  place (spec decision; the timeseries format is deferred, I5).
- Spike amplitudes, depths, templates, waveforms; units with `label < 1`.

## Sample build (scratch, not this directory)
Sessions `ebce500b…` (BWM, 1 probe), `03063955…` (BWM, 2 probes) and `f45e30cf…` (extra,
`iblsorter`).
- 4/4 probes converted with 0 failures, in 14.9–27.0 s per probe (mean ≈ 18 s). Total
  223 s including the release listing and trials.
- Good units per probe: 20, 131, 291, 50. Spikes kept: 0.38 M, 2.7 M, 13.1 M, 5.3 M.
  Shards: 0.66, 4.7, 22, 7.2 MB. Metadata: 0.56 MB.
- Round trip through `bwm_ephys.load_spike_shard` compared with the original ONE spikes:
  max |Δt| = 50.0 µs (half a tick, from snapping), with clusters and cluster IDs exactly
  equal. `time_origin_ticks × 100 µs == time_origin_seconds` in every shard
  (tick-aligned).
- Bugs found and fixed on the way: mixed ISO8601 formats in Alyx `start_time`; `None`
  revisions becoming NaN and breaking ONE's revision filter (first misread as a data
  problem); `n_spikes` missing on resume.
- Trials load in ≈3.4 s per session and a subject read takes ≈0.7 s.

### Sample rerun (2026-10-01, session 2)
The user chose to re-run the sample before deciding on the full build, because the first
sample was lost (see Environment, Session 2).
- Output, persistent this time: `/teamspace/studios/this_studio/scratch/ibl_aging_sample_2026-10-01/`
  (35 MB). Driver scripts were in the session scratchpad: `run_sample.py` calls
  `convert.build(OUT, eids=[...])` with the 3 full eids `ebce500b-c530-47de-8cb1-963c552703ea`,
  `03063955-2523-47bd-ae57-f7489dd40f15` and `f45e30cf-12aa-4fa0-8248-f9f885dfa9ef`;
  `check_sample.py` does the round trip. `convert.py` was not changed.
- 4/4 probes, 0 failures, 11.8–18.9 s per probe (mean 15.8 s), 217 s total. Biased blocks
  detected; `reaction_time` skipped. Good units 20/131/291/50 and spike counts identical to
  the first sample.
- Round trip through `bwm_ephys.load_spike_shard` against ONE: max |Δt| 50.0, 50.0, 50.0 and
  33.3 µs, clusters equal, all shards tick-aligned. Peak RAM was not measured.

## Full-run estimate 
**Updated 2026-10-01 from the rerun:** 763 × 15.8 s ≈ 3.35 h + trials ≈ 28 min + listing ≈
3 min ≈ **3.9 h sequential**. The machine now has 13 GB RAM available and 4 CPUs, so 4
workers at ≈2 GB each (unmeasured) would fit, but parallel mode is still unwritten.

**Parallel mode (2026-10-01).** `build(..., workers=N)` / `convert.py --workers N`. Each
worker is a spawned process with its own ONE connection and runs `convert_recording` for one
probe; results are collected back in `recordings` order. Tested on the 3-session sample
(`scratch/ibl_aging_sample_2026-10-01_parallel/`, script `run_parallel_test.py` in the
session scratchpad), workers=4: 0 failures, 193 s total; all 7 tables `DataFrame.equals` the
sequential sample; all shard arrays and `meta.json` decode identical. 7 of 20 `.blosc` files
differ in bytes (same size; from byte 17, the block-offset table): blosc places blocks in the
order its threads finish them. Content is identical, but **shard checksums are not
reproducible across rebuilds**, so a validator must compare decoded arrays, not
`manifest.json` hashes. Per probe 25–33 s under 4-way contention (vs 12–19 s alone);
peak RSS per worker 0.68–2.39 GB, the highest on the 13 M-spike probe (all spikes of a
probe are loaded before the good-unit filter). Revised estimate with 4 workers: ≈ 1.5 h
of probes + ≈ 31 min trials/listing ≈ **2 h**. Risk: a probe with far more spikes could
push 4 workers past the 13 GB available; an OOM kill stops the build, and a restart
skips finished shards.

Original estimate (2026-09-30):
763 probes × ≈18 s ≈ 3.8 h, plus trials 497 × 3.4 s ≈ 28 min, plus about 3 min of
listing: **≈ 4.3 h sequential**. With 4 worker processes it would take ≈ 1.2–1.5 h
(not yet implemented or tested). Shards ≈ 6.5 GB (range 4–10 GB) on 343 GB free. Peak
RAM ≈ 2 GB per worker, with 27 GB available.

## Full build (2026-10-01)
`.venv/bin/python ingestion/convert.py --workers 4`, detached (`setsid nohup`), output in
`/teamspace/studios/this_studio/scratch/ibl_aging_full_build_2026-10-01.log`.
- Started 10:55 UTC; listing and trials took until 11:29; probes 11:29–12:37. **Total ≈ 1 h 42 min**
  (estimate was ≈ 2 h).
- Peak RSS per worker up to 3.54 GB (above the 2.39 GB seen in the sample); MemAvailable never
  fell below ≈ 10 GB at the checks made. No OOM.
- Counts: 158 subjects, 497 sessions (459 BWM), 767 recordings (763 with spikes), 324,996
  trials, 2,270,519 events, 79,571 good units, 291,408 channels, 763 shards with 4.33 × 10⁹
  spikes. Shards 6.3 GB, metadata 59 MB.
- Checks: 0 `conversion_error`; shard ids equal the `has_spikes` recordings; units in shards
  equal the units table; every session has trials; every unit has a region; `label` ≥ 1
  throughout; the 4 unsorted probes are the 4 in the table above.
- Good units by sorter: `pykilosort` 75,708 on 699 probes (≈ 108/probe), `iblsorter` 3,863
  on 64 probes (≈ 60/probe). Not interpreted here: sorter is confounded with age and lab.
- One sorted probe has 0 good units: `6e61f777-90b9-4656-af30-9ed54d426c5a` (UCLA035
  probe01, session `f99ac31f…`, BWM, churchlandlab_ucla, 211 d, Alyx QC `WARNING`). All 305
  clusters have label < 1 (0: 123, 0.33: 158, 0.67: 24). Kept with `n_good_units = 0`.
- Round trip (`load_spike_shard` vs ONE) on 6 probes, namely the largest (`64aadf54…`, 52 M
  spikes), one `iblsorter` and 4 random (seed 0): max |Δt| ≤ 50 µs, clusters equal,
  tick-aligned in all.
- Monitoring note: the progress watcher used `pgrep -f "convert.py --workers 4"`, which matched
  its own command line, so it never saw the build exit. Use the PID instead.

## After the build (2026-10-01)
- **Registered** in `ibl-ai-agent-ingest/data_locations.local.yaml` (gitignored):
  `ibl_aging: {root: /teamspace/studios/this_studio/datasets/ibl_aging, preferred_version: latest}`.
  `resolve_dataset_dir('ibl_aging')` returns `.../ibl_aging/1.0.0`.
- **Paper read** (nature.com article page, through a fetch tool that summarises; the key
  passages were fetched a second time verbatim). Used for the aim, the paper's
  session/trial/neuron criteria, the visualisation-only age split, and the confounds it
  discusses (training duration, movement, neural yield). The DOI is built from the article
  URL; the data-availability statement was not read.
- **Sorter version, measured (read-only Alyx):** tag `2024_Q2_IBL_et_al_BWM_iblsort`
  description "Spike sorting output with ibl-sorter 1.7.0 for BWM"; BWM probe log
  `_ibl_log.info_pykilosort.log` (rev 2024-05-06) "Starting Pykilosort version 1.7.0";
  dataset `version` field of the 699 BWM `spikes.times`: 2.35.0 (698), 2.35.2 (1), created
  2024-05; of the 64 non-BWM `spikes.times`: iblsorter_1.9.1 (46), iblsorter_1.9.0a (2),
  3.2.0 (16), created 2024-09 to 2025-01. The non-BWM probes have no sorter log. **Inferred,
  not verified:** the `version` field is the registering ibllib version, so the paper's
  "2.35.0" is that number, not the sorter's. Recorded as "Paper vs data" in
  `scientific-context.md` and as an author question.
- **Cohort vs paper:** non-BWM 19 mice, 11 M (paper 19, 11 M); session ages 10.41–19.61
  months at 30.44 d/month (paper 10.58–19.90). BWM 139 mice, 94 M, 3.02–15.15 months
  (paper, after QC: 130, 89 M, 3.10–15.13).
- **Prose edited after the build:** `scientific-context.md` (rewritten from the paper),
  `README.md` (key paper), `modalities.md` (sorter versions), `open-questions.md`.
  1.0.0 is not published, so it was edited in place rather than bumped (`project-structure.md`
  versioning applies once published).
- `ingestion/__pycache__/` (created when the parallel test imported `convert.py`) was
  deleted; it had been listed in the first `manifest.json`. `convert.py`'s manifest step
  now skips `__pycache__`.

## Fresh-agent check (2026-10-01)
A new agent, with no conversation context and read-only rules, used only the registered
package (not `ingestion-log.md` or `convert.py`). Results: README relevance PASS; loading
PASS (tables from the `schema.yaml` paths; one `iblsorter` and one BWM shard; a VIS unit's
spike count 0–0.2 s after `stimOn_times`; IDs and clock clear without guessing); contract
mostly PASS (every column has `units`, declared columns equal parquet columns, counts
match). Issues found, each verified and then fixed:
- **`schema.yaml` had 4 truncated descriptions** (`sessions.start_time`,
  `recordings.conversion_error`, `units.depth_um`, `channels.raw_index`): unquoted commas
  inside YAML flow mappings in `schema_source.yaml` split each description into a stray
  key. Fixed by quoting them in the source. `schema.yaml` was regenerated with the same
  check-and-dump as `write_contract`, from the built tables, without a rebuild
  (`provenance.yaml` unchanged). Only those 4 columns, `time_bases` and `stores` changed.
- Added `time_bases.session_clock.origin_definition` (it was only a YAML comment) and
  `stores.spikes.array_notes`. `cluster_spike_counts` is indexed by `cluster_id`, verified on
  3 shards.
- README: registration status corrected; sorting/age caveat added under "When to use".
- `experiment.md`: biased blocks measured in all 497 sessions (verified), not "every sampled session".
- `modalities.md`: 105 trials in 30 sessions have NaN `stimOn_times` (verified); `epochs`
  is empty by design.
- `scientific-context.md`: VIS coverage by source (3,093 units / 84 mice BWM; 267 / 16
  non-BWM; 15 mice with VIS units at ≥365 d; verified); findings-list framing clarified.
- `SUMMARY.md` and `convert.py`'s summary step: added channels (291,408) and epochs (0).
- `open-questions.md` item 4 points to `recordings` instead of the log.
- Not changed: no published-paper DOI field in `provenance.source` (the contract has one
  `doi`, which holds the release tag's preprint DOI; see open question 11).
