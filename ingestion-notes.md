# Data ingestion — design notes

Working notes for the generic data-ingestion skill (branch `ingestion-pilot-aging`).
Scope: let the agent analyse datasets beyond BWM, starting with IBL aging and autism.

This file holds the evidence behind the design and what was found in the pilot.
The decisions themselves are in `specs/data-ingestion.md`, the package contract is
in `project-structure.md`, and the procedure is in `skills/data-ingest/SKILL.md`.
Problems found while running the skill go in **Pilot issues** at the end.

## Spike shard format

### Decision: use `spikepack` as the shard writer for ingested datasets

Ingested datasets write spike shards via
[`spikepack`](https://github.com/int-brain-lab/spikepack) (`write_blosc`), generic
over shard key and unit-selection rule. No second shard writer is added to this repo.

Install is from git, not PyPI: `pip install spikepack` returns 404 for both
`spikepack` and `spike-pack` as of 2026-09-29. Pin to a commit SHA in an
optional `ingest` extra until a release is published.

### Decision (deferred, for devs): retire the in-repo encoder at the next BWM version bump

`ibl_ai_agent/datasets/bwm_ephys.py` still carries its own spike encoder
(`_encode_spike_times_dataset`, `SpikeShardWriter`). `spikepack` was extracted and
generalised from exactly that code, so the two are now duplicated logic that can
drift silently.

**Retire the in-repo encoder in favour of `spikepack` at the next `bwm_ephys`
version bump.** Not now: switching the BWM builder today would change the bytes of
a rebuilt release for no scientific gain.

Evidence (verified 2026-09-29 against `bwm_ephys/1.2.1`, probe
`00a824c0-e060-495f-9ebc-79c82fef4c67`, 4,508,958 spikes, read-only):

- **Decoded spike times are identical.** Re-encoding the probe with
  `spikepack.write_blosc` and reading it back with the repo's own
  `load_spike_shard` gives max absolute time error `0.0 s`; `spike_clusters` and
  `cluster_ids` compare equal.
- **The bytes differ.** `spike_times_delta_ticks.blosc` and
  `spike_clusters.blosc` are not byte-identical (5,844,048 B vs 5,844,215 B
  total). Two causes, both benign:
  - spikepack sets `delta[0] = 0` and moves the first spike's offset into
    `time_origin_ticks`; this shipped shard has `time_origin_ticks = 0` with the
    offset in `delta[0]`. Same reconstructed times, different arrays.
    **Correction (2026-09-30):** this is a difference between the *shipped release*
    and current code, not between this repo and spikepack. The current in-repo
    encoder (`_encode_spike_times_dataset`, `bwm_ephys.py:1534`) also sets
    `deltas[0] = 0` and writes the offset to `time_origin_ticks`. It would not
    produce `(0.0, 0)` for a shard whose first spike is non-zero, so the 696 shipped
    shards with that origin were written by earlier code or had their origin keys
    back-filled by `_normalize_spike_meta_dict` (`bwm_ephys.py:2155`).
  - Blosc/numcodecs version differences change compressed output for large
    arrays (`cluster_ids` and `cluster_spike_counts` were byte-identical).
- File names, array names, dtypes and codec specs (`blosc/zstd/clevel 7/shuffle`)
  all match. Container is a directory of `.blosc` files, not Zarr.

So the bump is a re-encode, not a re-derivation. Version it as such and note in
the changelog that shard bytes change while spike times do not.

Given the correction above, "switching would change the bytes" is not by itself a
reason to wait: rebuilding with the *current in-repo* encoder would also change the
bytes relative to 1.2.1. The remaining reason to defer is scope — the pilot should
not touch the BWM builder. The in-repo encoder also has the same non-tick-aligned
origin behaviour as spikepack (`origin_ticks = rint(t0 / q)` while deltas are taken
from `t - t0`), so the precision caveat below applies to any BWM rebuild too.

### Decision: shared reader, BWM writer untouched

- Move `load_spike_shard` from `bwm_ephys.py` to
  `ibl_ai_agent/datasets/spike_store.py`, re-exported from `bwm_ephys` so existing
  imports keep working.
- Leave `_encode_spike_times_dataset` and `SpikeShardWriter` in place for now
  (see deferred decision above).
- The shared reader must decode both origin layouts: BWM's offset-in-`delta[0]`
  and spikepack's offset-in-`time_origin_ticks`. Add a test that both give the
  same spike times.

**Caveat for that test — do not assert exact equality unconditionally.** The
current reader already handles both layouts (it computes
`cumsum(deltas) + time_origin_ticks`), but the two decoders disagree on origin
*precision*: `spikepack.decode_times` prefers the float `time_origin_seconds`,
while `load_spike_shard` uses the rounded `time_origin_ticks`. When the first
spike is not tick-aligned, that rounding adds up to half a tick on top of the
per-spike half-tick, so worst-case error is one tick (0.1 ms), not half.

Measured: real BWM shards give `0.0` error (their origins are already
tick-aligned); a synthetic train with a non-aligned origin gave `5.136e-05 s`,
just over the advertised half-tick of `5e-05 s`.

**Resolved: do NOT change `load_spike_shard` to prefer `time_origin_seconds`.**
That looked like the clean fix, but it is not a no-op on existing BWM data.
Checked all 699 shards in `bwm_ephys/1.2.1`: 696 have
`(time_origin_seconds, time_origin_ticks) == (0.0, 0)`, but three have a non-zero,
non-tick-aligned negative origin:

| pid | origin_seconds | ticks x q | shift | n_spikes |
| --- | --- | --- | --- | --- |
| `11a5a93e-58a9-4ed0-995e-52279ec16b98` | -6.453058282515064 | -6.4531 | 41.7 us | 9,141,760 |
| `50f1512d-dd41-4a0c-b3ab-b0564f0424d7` | -2.379225878888514 | -2.3792 | 25.9 us | 2,260,189 |
| `5a34d971-1cb3-4f0e-8dfe-e51e2313a668` | -1.367649417027253 | -1.3676 | 49.4 us | 5,960,054 |

Preferring the float origin would shift every spike time on those three probes by
up to 49.4 us. Below the 100 us quantization and scientifically negligible, but it
is a silent change to existing BWM results and breaks the "BWM behaves exactly as
before" constraint.

Instead: keep `load_spike_shard` using `time_origin_ticks` (current behaviour,
BWM unchanged), and **require the ingestion writer to emit tick-aligned origins**,
which removes the discrepancy at the source. A float cannot be an exact multiple of
100 us, so this is done by snapping every spike time to the grid before writing —
`np.rint(t * 1e6 / q) * q / 1e6` — and recording that as lossy in provenance. The
both-variants test can then assert exact equality on synthetic on-grid inputs.

### Not doing

Do not retrofit `unit_selection_rule` into existing BWM shards. Ingested datasets
echo it into each shard's `meta.json`; BWM continues to record it in
`provenance.yaml` only.

## Upstream defects found in `spikepack` 0.1.0 (commit `30ab06f`)

Worth filing against `int-brain-lab/spikepack`:

1. **`read_blosc` silently loses cluster assignments on real BWM shards.**
   `_restore` gates on `meta["has_labels"]` / `meta["has_cluster_ids"]`, which are
   spikepack-only keys absent from shipped BWM shards. Reading
   `bwm_ephys/1.2.1` returns `['meta', 'times']` — no labels, no `cluster_ids`,
   no error raised. spikepack is safe as a *writer* of our format but not
   currently safe as a *reader* of existing BWM data.
2. **Format-string mismatch.** Shipped `bwm_ephys/1.2.1` shards carry
   `"format": "ibl_agent_spike_shard_v2"`; spikepack and current `bwm_ephys.py`
   both write `"ibl_ai_agent_spike_shard_v2"`. Harmless only because
   `load_spike_shard` never checks the field — any format gate we add would
   misfire on the shipped release.
3. **`docs/reference/format.qmd` is stale.** It documents arrays as
   `event_times_delta_ticks` / `event_labels`; the code writes
   `spike_times_delta_ticks`, `spike_clusters`, `cluster_ids`,
   `cluster_spike_counts`.
4. **README install line is wrong** — `pip install spikepack` does not resolve.

## Skill layering

### Decision (deferred, for devs): lift the dataset-independent semantic core out of `ibl-analyze`

Target design: a dataset-neutral analysis skill that every dataset gets, leaving
`ibl-analyze` holding only the IBL/BWM-specific material. **Not in this piece of
work** — it edits the skill governing all BWM analysis, and the pilot does not
need it (see next decision).

Proposed seam, verified by reading every file under `skills/ibl-analyze/`:

Generic (lift):
- metric classification: direct / operationalized / proxy, and the semantic match gate
- shape-before-scalar validation
- independent statistical unit / pseudoreplication rules
- ambiguity policy (plausible definitions, stored vs recomputed vs unavailable)
- `references/reproducibility_qc.md` — general statistical hygiene, QC reporting
- `references/scientific_caveats/` — both cards are already dataset-neutral
- `references/interactive_scientific_analysis.md` — the whole lifecycle is portable

IBL-specific (leave behind):
- event anchors (`stimOn_times`, `firstMovement_times`, `feedback_times`, `goCue_times`)
- `probabilityLeft` / block-prior semantics, `references/prior_and_block_semantics.md`
- BWM region and inclusion guidance, `references/bwm_analysis_patterns.md`,
  `references/visual_latency.md`, the BWM routing rows in `references/operators.md`

When this move happens it must be pure motion — no rewording — verifiable by diff
and by `tests/test_skill_references.py`.

### Decision: pilot routes to `ibl-analyze` as it stands

Aging and autism use the same IBL task, so route them to the existing guardrails
unchanged. Audit of BWM-specific assumptions that could mislead on those datasets
is recorded below; treat it as the list to check during the pilot, not as work to do now.

## Audit: BWM assumptions in `ibl-analyze` that could mislead on aging/autism

### Routing — would send the agent to data that does not exist for these datasets
1. `SKILL.md` default policy #7: "Prefer local BWM tables and shards when their
   schema covers the question." No instruction on where to go when it does not.
2. `references/operators.md` — the Operator Map and "BWM Loading Route" name
   `bwm_ephys` / `bwm_behavior` / `bwm_query` / `bwm_units` as the surfaces for
   good units, movement/quiescence, passive, and metadata questions. Dead ends here.
3. `references/operators.md` General Rules repeats the same BWM preference.
4. `references/visual_latency.md` Inputs prefer `bwm_ephys` tables and
   `event_response_features.parquet`, with `brainwidemap.load_good_units` as the
   fallback — the fallback routes *into* the BWM release helper.
5. `references/bwm_analysis_patterns.md` is BWM throughout. Conditionally routed,
   so lower risk, but "Apply the BWM trial mask before event-aligned statistics"
   has no analogue in aging/autism and would be silently skipped or wrongly sought.

### Inclusion and QC
6. The BWM trial mask (`bwm_include` in `trials.parquet`) has no equivalent. The
   inclusion rule must become dataset-declared.
7. `references/reproducibility_qc.md`: "Prefer canonical release QC when available
   over improvised local thresholds." Aging/autism may have no canonical release QC;
   risk is the agent quietly borrows BWM's `label >= 1.0` or invents a threshold.
8. Nothing in `ibl-analyze` ever asks what the unit-selection rule *was*. Ingested
   packages declare `unit_selection_rule`; the skill needs to read it.

### Subject and design — the largest gap for these two datasets
9. `references/reproducibility_qc.md` is framed entirely on cross-**lab**
   reproducibility (12 labs, RIGOR QC, "when comparing labs or regions"). Aging and
   autism are plausibly single- or few-lab, making "lab" a degenerate variance
   dimension. Its robust/fragile lists are evidence from BWM repeated-site
   recordings, not a general law.
10. Throughout, coverage is reported as "unit / insertion / session / subject / lab":
    **subject appears only as a coverage dimension, never as an experimental factor.**
    For aging, age is a continuous between-subject covariate; for autism, genotype is
    a between-subject factor. Nothing covers between-subject designs, group
    comparisons, or age as a covariate. The statistical-unit rule correctly says
    neurons nested in a subject are not independent, but stops short of saying that
    in a between-subject design N is *subjects*, and typically small.
11. No caveat for **age or genotype confounded with recording quality** — older mice
    or a mutant line may differ in yield, stability, drift, or engagement, producing
    apparent neural group differences. This is the across-subject analogue of
    `scientific_caveats/firing_rate_nonstationarity.md` and does not exist.

### Task — conditional on a fact established at ingestion
12. `references/prior_and_block_semantics.md` and the `probabilityLeft` guidance
    assume the **biased-block full task**. `ibl-load/references/ibl_behavior_task.md`
    itself warns to distinguish the equal-probability basic task from the biased-block
    full task. Whether aging and autism use biased blocks is read from the
    documentation at ingestion and recorded as `task_protocol` in the package's
    `schema.yaml`. If they do not, all prior/block guidance is inapplicable,
    `probabilityLeft` is not written, and the package's `scientific-context.md` says
    so.
13. `ibl-load/references/bwm_release_scope.md` anchors scale at 621,733 neurons /
    699 probes / 139 mice / 12 labs. Conditionally routed, but if it lands in context
    for an aging question it anchors expectations wrongly.

### Confirmed portable
`references/interactive_scientific_analysis.md`, both `scientific_caveats/` cards,
and the shape-before-scalar / semantic-match / statistical-unit sections of
`references/scientific_context_and_metric_semantics.md` carry across unchanged.
This is the evidence for the seam above.

## Package contract: evidence not recorded elsewhere
The resolved contract items (age on `sessions`, re-resolvable references, the two
version axes, the minimum viable package, the generic `events` table with BWM
mapped reader-side, fixed headings and the discovery step) are now defined in
`project-structure.md`, with their reasons under Decisions in
`specs/data-ingestion.md`. What follows is the code evidence that shaped one of
them.

### Trials reuse is not a call-site-only change (checked 2026-09-30)
- `_build_trials` ends with `trials[ordered]`, where `ordered` always includes
  `probabilityLeft` and `bwm_include`. Either column missing raises `KeyError`,
  and both are expected to be missing for aging/autism.
- `_build_trials` also expects a pre-built trials aggregate parquet and a
  BWM-style roster, which aging/autism do not have.
- `_build_events` casts `event_time` to float32 internally. Up-casting afterwards
  cannot restore float64 precision. At t ~ 3000 s, float32 spacing is ~0.24 ms,
  coarser than the 0.1 ms spike quantization.

Resolution: Behavior → 2a in `specs/data-ingestion.md`.

## Deferred: reading NWB directly (`pynapple`)
Reading NWB directly with `pynapple` instead of converting to
shards would remove the conversion step for NWB sources. Not part of this
pilot; to be tried as a separate test later. Relevant to I1 below: if direct reading
is good enough, the NWB reader may only need to write metadata tables and reference
bulk data in place.

## Stress test: five data types

Cases tested: (a) hippocampus during free exploration, no trials; (b) two-photon
calcium imaging, no spikes, possibly deconvolved; (c) NWB from DANDI; (d) a lab's
own format with only a README; (e) IBL aging and autism.

### Structural problems — fixed in the proposal

S1. **No store for continuous timeseries.** Broke (a) position/head-direction,
    (b) fluorescence traces, (c) NWB `TimeSeries`, (d) ECoG/EMG/photometry.
    `events` holds instants and `epochs` holds labelled intervals; neither holds a
    sampled signal. BWM avoided this by reducing wheel/pose to trial-aligned
    features, which is impossible without trials.
    Fix: a `timeseries` store kind — one store per signal family, declaring its
    sources, `rate_hz` + `t0` *or* an explicit timestamps array, units, time base
    and container format; chunked for interval reads.

S2. **`spikes` store was undefined w.r.t. continuous rate estimates.** Deconvolved
    2P traces are a value per frame, not sorted event times, and must not be
    quantized into the spike shard.
    Fix: `spikes` is discrete event times only; deconvolved/continuous rate
    estimates are a `timeseries` store. Thresholding to events is an analysis step,
    never a conversion step.

S3. **Required-table list assumed electrophysiology.** `channels` is meaningless for
    2P and `units` are ROIs.
    Fix: core tables `subjects`, `sessions`, `recordings`, `events`, `epochs`
    (last two required-present, may be empty); conditional tables `units`
    (discrete sorted/segmented sources), `channels` (electrode-based only),
    `trials` (trial-based only).

S4. **Multiple clocks unrepresentable.** NWB timestamps are per-`TimeSeries` and need
    not share a clock; `time_bases:` was plural but nothing bound a table or store to one.
    Fix: every table and store names its `time_base`; `time_bases:` declares each
    base's origin, clock and alignment to the others.

S5. **No spatial reference frame.** Free-exploration position needs an arena origin,
    axes and length units; `time_bases` only covers time.
    Fix: a `reference_frames:` block alongside `time_bases:`.

S6. **Overlapping epoch vocabularies collided.** "immobile" and "in zone A" are two
    independent labelings of the same session; one flat `label` column cannot
    distinguish them.
    Fix: `epochs` gains `epoch_set`, a labeling namespace. Overlapping sets coexist.

S7. **No slot for experimental design.** Which factors are between-subject (age,
    genotype), which within, what the groups are, what comparison the experiment was
    built for. The most important fact about the aging and autism datasets had
    nowhere to live except free prose. Matches audit item 10 (subject treated as a
    coverage dimension, never as an experimental factor).
    Fix: a machine-readable `design:` block in `schema.yaml` — each factor with
    levels and grain (between-subject / within-subject / between-session) and the
    intended comparison — plus a required `experiment.md` section "Design and factors".

S8. **`unit_selection_rule` presumed spike sorting.** Sources may be ROIs.
    Fix: renamed `source_selection_rule`.

Also folded in: the "units for every column" blocking rule accepts `a.u.` and
`dimensionless` (fluorescence, dF/F), so ingestion does not stall on a non-problem.

### Verified: events/epochs represent non-IBL behaviour without schema change

`events` = named instants, optional scalar `event_value`, plus dataset-declared
extra columns (tone frequency, zone identity). `epochs` = named intervals within a
declared `epoch_set`. Reward delivery, licks, zone entries, optogenetic pulses,
sleep states, drug on/off, and nested trial-within-session structure all express
without touching the contract.

### Implementation gaps — future work, no layout change needed

I1. **NWB/DANDI reader.** NWB `Units` -> `units`, `TimeIntervals` -> `trials`/`epochs`,
    `Subject` -> `subjects`, `TimeSeries` -> `timeseries` stores, dandiset ID +
    version + asset path -> `provenance.source`. Mapping is mechanical.
    Note the tension: NWB is already self-describing, so converting it duplicates
    work. Metadata conversion is cheap; spike shards earn their keep on size.
I2. **Per-lab format readers**, for the README-only case. The blocking-gap rules
    already make the agent stop and ask rather than invent units or a time base.
I3. **Two-photon reader** (Suite2p / CaImAn): ROI tables with pixel masks, plane
    index and imaging depth; F / Fneu / dF/F / deconvolved traces as timeseries
    stores; per-plane frame times with plane offsets.
I4. **Position/tracking reader** for free exploration: position, head direction,
    speed as timeseries, in a declared spatial reference frame.
I5. **Timeseries container format.** Codec and chunking for the `timeseries` store
    (zarr vs blosc shards). The store kind is declared per store, so choosing the
    format later is not a layout change.
I6. **Generic feature builders.** `_build_event_response_features` and
    `_build_unit_features` live inside `bwm_ephys.py`. Aging and autism need
    BWM-schema features for `bwm_analysis_patterns.md` guidance to transfer; lift
    them out so any dataset can build them.
I7. **Dataset validator** (`validate-dataset`): checks a built package against
    `project-structure.md` — declared tables exist, primary keys
    unique, every column has units and a description, shard keys resolve, every
    table and store names a valid time base, referenced-in-place entries re-resolve.
    Three further checks the pilot showed a build's own checks miss: a column spec
    carrying keys other than `dtype`, `units` and `description` (item 17); the same
    quantity named differently by `schema.yaml` and store metadata, such as
    `quantization_us` vs spikepack's `time_quantization_us` (item 8); and rebuild
    comparison by decoded content rather than file hash, since compressed stores are
    not byte-reproducible (item 12).
I8. **spikepack read-side defect** — either upstream fix or a repo-side wrapper, so
    reading existing BWM shards through spikepack does not silently drop labels.

## Pilot issues
Problems found while running `skills/data-ingest/` on real data. Per-package
questions stay in that package's `ingestion/open-questions.md`. Record here what
the skill got wrong or did not cover, so its next revision has evidence: what
happened, which dataset, and what the skill should have done.

### 2026-09-30, ibl_aging (sample build only; full run not yet approved)
1. **The release tag did not cover the whole dataset.** `2025_Q3_Zang_et_al_Aging` tags
   trials, video and LFP for all 497 sessions, but spike sorting only for 64 non-BWM
   probes. The 699 BWM probes' sorting is tagged by `2024_Q2_IBL_et_al_BWM_iblsort`.
   *The skill should* tell the agent to check which files the release tag actually
   covers, per modality, and to record each modality's source tag in `provenance.yaml`.
2. **The dataset overlaps an existing package.** It is a superset of BWM (459/459
   sessions). The skill says nothing about overlap. *The skill should* ask for overlap
   with configured datasets to be measured and stated in `README.md` and
   `scientific-context.md` (non-independence of replications).
3. **New design caveat: the processing pipeline and the lab mix are confounded with the
   group.** All old-mouse additions are from one lab (36/38) and a different sorter.
   *Add a row to the caveat table:* "Groups differ in sorter/pipeline version or lab
   composition → the group effect is confounded with processing; check within pipeline."
   The "One lab or a few" row has no many-lab counterpart; add "Many labs, unbalanced
   across groups → model or stratify by lab".
4. **Biased blocks were not stated in any documentation the agent had.** The repo docs
   describe biased blocks but don't map `ephysChoiceWorld` to them. Measured from
   `probabilityLeft` instead. *The skill should* allow measuring a design fact from the
   data (logged as measured) when the documentation is silent.
5. **Environment traps.** (a) `OneLightningAI` defaults `tables_dir` to ONE's cache dir,
   which here is the read-only S3 mount, so pass `tables_dir` explicitly. (b) With a conda
   env active, `uv pip install` targets conda, not `.venv`, so use
   `--python .venv/bin/python`. (c) `uv run` re-syncs and removes the pilot spikepack
   install, so use `uv run --no-sync`. *`references/spike-shards.md` should* give the
   `--python` form and the `--no-sync` warning.
6. **Subject strain/line/genotype were absent from Alyx for all subjects.** The user
   supplied the strain from the paper. The checklist asks for these, which worked.
7. **`project-structure.md` has no place for per-probe sorter provenance.** Mixed sorters
   in one package needed `recordings.sorter` / `sorting_revision` / `sorting_release_tag`.
   Consider making these part of the `recordings` contract.
8. **spikepack `meta.json` uses `time_quantization_us`,** not `quantization_us` as in
   `schema.yaml`. Harmless, but a validator should know both names.
9. **Agent errors caught in self-review before the user saw them:** unverified
   domain claims in prose (probe models, lick detection, reward/timeout details) and
   miscounted summary statistics. *The skill should* require every number in prose files
   to come from a computed value and every descriptive claim to cite its source (data,
   documentation, user).

### 2026-10-01, ibl_aging (session 2, after the Studio was duplicated)
10. **The sample build was lost.** It was in the agent's `/tmp` scratchpad and its path was
    never logged; duplicating the Studio copied the home but not `/tmp`. *The skill should*
    require the sample build in a persistent scratch location, with its path in
    `ingestion-log.md`. Rebuilding it took ~4 min and matched the logged numbers exactly.
11. **Item 5(c) superseded.** The user's rule is now: run `.venv/bin/python` directly and
    never `uv sync`/`uv run` (not even `--no-sync`) while spikepack sits outside the lockfile.
12. **Shard bytes are not reproducible across builds.** blosc stores blocks in the order its
    threads finish them, so the same arrays give different bytes (same size). A validator
    or a rebuild check must compare decoded arrays, not `manifest.json` checksums.
13. **Parallel conversion was needed and was not covered.** A per-probe process pool (one
    ONE connection per process, results reordered) gave identical tables. Under 4-way
    S3 contention each probe ran ~1.7× slower, so 4 workers gave ~2.2× speed-up, not 4×.
    Peak RSS was up to 2.4 GB per worker, driven by loading all spikes before the unit
    filter. *The skill should* say to time the sample with the intended worker count.
14. **Publishing to S3 is the long-term target, and the skill says nothing about it.** The
    plan is to publish finished packages (data plus text files) to S3 like BWM, with the
    registry pointing there. In ibl_aging every path a reader needs is relative
    (`schema.yaml`, `manifest.json`, shard `meta.json`) and referenced stores re-resolve
    through ONE ids, so the package is location-independent. Only `ingestion/convert.py`
    hard-codes Studio paths. *The skill should* say:
    - **Paths:** everything a reader needs is relative to the package root; references go
      through re-resolvable ids, never local paths; `convert.py` takes the output and
      cache locations as arguments.
    - **Versioning:** a published version is immutable, so any later edit, prose included,
      is a new patch version; an unpublished version may still be edited in place. How
      local and published versions relate is still open (`project-structure.md`).
    - **Checksums:** write `manifest.json` last, after the final prose edit, and exclude
      build by-products (`__pycache__` went into the first manifest here). Shard bytes are
      not reproducible (item 12), so check uploads against the manifest, but check
      rebuilds by decoded content.
    - **Upload:** who uploads, the bucket layout (`<dataset>/<version>/`), verifying sizes
      and hashes after upload, and only then pointing the registry at the published copy.
15. **The paper contradicted the data, and the metadata explained why.** The published
    paper says all probes used "ibl-sorter (version 2.35.0)". On Alyx, `2.35.0` is the
    `version` field of the BWM `spikes.times` datasets, while the sorter log and the
    release tag say pykilosort/ibl-sorter 1.7.0, and the extra probes record iblsorter
    1.9.x. *The skill should* say to check each processing claim from the paper against the
    data's own provenance (logs, tag descriptions, dataset version fields), and to record
    disagreements in `scientific-context.md` and as questions for the authors, not resolve
    them silently.
16. **The registration step was a one-liner** once the user approved it; resolving through
    `resolve_dataset_dir('ibl_aging')` confirmed it. The skill could say to verify
    registration this way.
17. **The fresh-agent check found a contract bug that the build's own column check missed.**
    Unquoted commas inside YAML flow mappings (`{dtype: …, description: a, b}`) split 4
    descriptions into stray keys. The column-set check passed because it compares only
    column names. *The validator (I7) should* reject column specs with keys other than
    `dtype`/`units`/`description`, and the skill should say to quote prose in YAML. The
    fresh-agent pass was worth running: besides this bug it found a stale README status, the
    main caveat missing from the README, and undocumented shard-array indexing.

### 2026-10-01, ibl_aging, Session B (analyst test)
A full exploration → confirmation analysis of the built package ("do old mice have lower VIS firing
rates?"), then a reproduction of the paper's VISp+pm firing-rate analysis through the authors' code (items
26–29). Full report: `projects/vis_firing_rate_aging/package-test-report.md`. Gap list:
`projects/vis_firing_rate_aging/change-log.md` (20 items). Only the report is committed; the rest of `projects/`
is gitignored.
18. **The package was enough for a complete locked analysis.** The prose predicted the decisive
    confound (sorter × age × lab), the tables joined without gaps, and shards decoded about 4 s/probe
    with counts matching `units.spike_count`. Result: inconclusive (H1 p = 0.66, equivalence not shown),
    with the claim scoped to BWM-sorted mice aged about 90–460 d.
19. **Whole-recording unit metrics misled.** `firing_rate`/`presence_ratio` include the post-task period,
    rates rise about 2× during the task, and some units are absent for most of it. Task-window metrics
    had to be recomputed, and two confirmation mice with VIS units nearly silent in the analysis window
    went undetected until after the locked test. *The skill should* consider shipping per-unit
    task-window rate, presence ratio and first/last spike time.
20. **`units.drift` is described as µm/hour but measures firing rate × depth jitter** (medians about 1e5).
    The values follow brainbox's definition, so probably not a conversion error. Check against source
    `clusters.metrics.pqt` and fix the schema description (details in the gap list, item 16).
21. **Undocumented confound found by the analyst:** probe model (62/64 iblsorter probes are 3A).
    *The skill should* ask for every recording-level field to be cross-tabulated against the design
    factors and the result recorded in `scientific-context.md`.
22. **The paper's ROI, QC table, inclusion list and effect sizes were not captured.** The analyst needed
    them (visual cortex is VISp+VISpm in the paper; the equivalence bound was meant to come from the
    paper). *The skill should* ask for these to be extracted into `scientific-context.md` at ingestion,
    while the paper is open.
23. **Alignment/histology QC is not carried.** One old-mouse probe with implausible yield and region
    labels could not be judged.
24. **Doc mismatch:** the BWM spike example says `spike_clusters` are dense indices, but here they are
    cluster ids (`meta.cluster_encoding`). A reader that follows the example mislabels spikes silently.
25. **The `.venv/bin/python` rule (item 11) did not reach the analysis session:** it lives only here.
    The analyst used `uv run`; spikepack survived. Put the rule where analysis sessions read it
    (`AGENTS.md` or `skills/install/`).
26. **The package reproduces the paper's result through the authors' own code.** Feeding `ibl_aging`
    spikes and trials into the authors' public pipeline (VISp+pm firing rate; project
    `projects/paper_reproduction_aging/`) gives β 0.698/0.539 against the paper's 0.697/0.538, with the same
    permutation p (0.032; 0.045–0.047 vs 0.048). 95% of per-neuron × contrast values are identical; the rest come
    from trial-NaN differences in 4 sessions and the 100 µs spike-time snapping. All 18,755 of the paper's
    neurons are in the package, matched by `cluster_uuid`.
27. **Package fields alone reproduce the paper's selection.** The authors' QC steps on package fields match their
    Table 2 at every step except one BWM probe at the ROI step (region-label difference). Their alignment and
    Alyx-QC queries removed nothing, so items 22–23 were not blockers for this paper. The paper's neuron QC
    uses *task-window* FR/PR: only 16,637 of 18,755 pass the stored whole-recording metrics, which strengthens item 19.
28. **The authors' code repository was the missing source, and the package doesn't link it.** It holds the ROI
    table, QC table, inclusion lists and per-region effect sizes that item 22 asked for. *The skill should* ask
    for the paper's code and data repositories to be found and linked in `scientific-context.md` at ingestion,
    and their inclusion lists checked against the package (`cluster_uuid`/pid/eid).
29. **One session's trials disagree with the paper's:** KS046 `69c9a415` differs in every trial (up to 48 s) and
    in count, under the same trials revision label (2025-03-03). For the ingestion side to check.

### 2026-10-02, decision: packages write their own conversion code
30. **The stand-in allowlist is gone; `convert.py` carries whatever the package needs.**
    `skills/data-ingest/` stood in for repo helpers only where a reference file listed
    them (`references/pending-interfaces.md`, now deleted), and stopped otherwise. That
    only works for datasets whose shape a repo helper already covers, which is BWM's
    and almost nothing else. The agent now writes readers, loaders and table builders
    for the package in `ingestion/convert.py`, without editing repo modules. Three
    protections replace the allowlist: the spike shard format is fixed on
    `spikepack.write_blosc`; an IBL-task `trials` table follows the written column
    contract in `skills/data-ingest/references/ibl-trials.md`; and every piece of
    package-specific code is listed in `ingestion-log.md`. Evidence that the cost is
    affordable: `ibl_aging`'s `convert.py` wrote its trials loader and its trials and
    events builders in about 40 lines (the "Pilot stand-ins" table in its
    `ingestion-log.md` is the pattern the log entry now generalises).
    **Trade-off accepted:** each IBL-task package carries its own copy of the trials
    loader and builder, and copies can diverge — between packages, and from BWM. The
    column contract and the per-package log make a divergence visible to anyone
    comparing two packages; neither prevents one. The reason for accepting it is that
    the alternative blocks ingestion of any dataset the repo has no helper for.
    Recorded as a dated entry under Decisions in `specs/data-ingestion.md`, where it
    reverses the two earlier decisions it contradicts.

31. **The IBL trials reference is a worked example, plus a contract for one task.**
    `references/ibl-trials.md` was the skill's one task-specific rule, which made a
    general ingestion skill read as though IBL trials were part of the package
    contract. It now sets out the choices any trial-based dataset has to settle —
    required versus optional columns, how `trial_id` is computed, which columns
    depend on a confirmed design fact, the package's own inclusion rule, the session
    key, and how `events` is derived — and works them through for the IBL task.
    `SKILL.md` carries the general checklist item; the file keeps one binding role,
    `task: ibl_choice_world`, where it is followed exactly because
    `skills/ibl-analyze/` and `skills/ibl-load/references/ibl_behavior_task.md` read
    those column names. That is the shape for any later task-specific rule: a general
    item in the skill, an optional worked example in `references/`, binding only
    where a declared task makes it binding. Two fixes went in with it — the events
    rules now state the row-ordering `bwm_ephys._build_events` uses (stable sort by
    session, trial, time, name, with `event_id` assigned after sorting), while the
    emitted columns stay in the generic `events` order of `project-structure.md`
    (`session_id`, `event_id`, `event_name`, `event_time`, `trial_id`,
    `event_value`), with float64 time and the `session_id` key — all three as the
    built `ibl_aging` package has them; and the trials column lists are no longer
    restated in `project-structure.md` or the spec. The written `trials` table
    likewise keys on `session_id`, not the source's `eid` (corrected 2026-10-02
    against the package's own `schema.yaml`).

### Repo helpers not built, and no longer required for ingestion
Kept as optional future work; the spec's change surface lists them with their
acceptance criteria. Build one when enough packages have duplicated the same code to
pay for it, not before.
- `ibl_ai_agent/datasets/one_trials.py` — per-session ONE trials loader.
- `bwm_simple._build_trials` generalised: a DataFrame or a path, `roster` optional,
  required/optional column split.
- `bwm_ephys._build_events` generalised: keyword-only `event_time_dtype` (ingestion
  would pass `np.float64`) and control of the session key and carried columns.
- `pyproject.toml` `ingest` extra pinning `spikepack` by SHA; until it exists the
  pinned commit is installed directly (`skills/data-ingest/references/spike-shards.md`).
- the dataset validator — I7 above.

## Design question for the team: committing package snapshots
Proposed during the aging pilot (2026-10-01); not agreed yet. The proposal was
written into `project-structure.md` at the time and was moved here on 2026-10-02,
since an unagreed proposal does not belong in the package contract.
`project-structure.md` says package prose lives in the package, outside the repo. For
review, the pilot copied a text snapshot of `ibl_aging 1.0.0` into
`dataset-packages/ibl_aging/1.0.0/` (11 files, 96.5 KB): `README.md`, `experiment.md`,
`modalities.md`, `scientific-context.md`, `SUMMARY.md`, `schema.yaml`, `provenance.yaml`
and `ingestion/` (`convert.py`, `schema_source.yaml`, `ingestion-log.md`,
`open-questions.md`). `manifest.json` (699 KB, mostly hashes of data files) is left out.
The package on disk (later S3) stays the source of truth.
Questions to settle:
- Do we want snapshots at all? For: review in PRs, history of prose and contract
  changes, `convert.py` under version control. Against: two copies that can drift.
- Which wins when they differ, and who syncs? The proposal: the package wins; the repo
  is copied from it, never edited directly.
- One folder per version, or only the latest? Published versions are immutable, so a
  folder per version matches; it grows with every patch.
- Should a check (CI or the validator) compare the snapshot with the published
  manifest's hashes for those files?
- `convert.py` and the log contain Studio paths (open question 15 in the package);
  acceptable in a snapshot?

Suggestion, not done: a `just` recipe (e.g. `just snapshot-package ibl_aging 1.0.0`) that
copies exactly these files from the registered package root, so the copy is never made
by hand. The pilot copied them once by hand.
