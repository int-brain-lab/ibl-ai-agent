---
name: data-ingest
description: Use this skill when ingesting a raw dataset into a standardised dataset package, converting documented source data into the layout a later agent can analyse without re-reading the source. Ingestion only, not analysis of an existing package.
---

# Data Ingest

## The idea
Ingestion happens once. Its output is a directory from which any later agent
instance can start analysing straight away, without re-reading the raw source or
repeating ingestion. This skill describes **that end point**. How to get there
from a given lab's files, whether NWB, DANDI or an in-house format, is for you
to work out.

**You lead.** Users will not know what you need, and their data will always be
missing something that shows up only when you try to convert it. Ask for what you
need, and whenever you get stuck, go back to the user with a specific question.
Never fill a gap by guessing.

Do not load this skill to analyse an existing package. Analysis reads the
package's own prose files plus `skills/ibl-analyze/` where it applies.

**Record what this skill does not cover.** When it fails you, or makes you do
work it should have prescribed, write it in `ingestion-notes.md` under "Pilot
issues": what happened, which dataset, and what the skill should have said. That
is the evidence for the next revision. Keep the evidence there and this file
general.

## The end point
One dataset at one version, in `<dataset_root>/<dataset_name>/<version>/`,
registered under `datasets:` in `data_locations.local.yaml`. The exact tree,
headings and YAML shapes are defined in `project-structure.md`. Read it before
writing anything, and do not restate it or diverge from it.

What each part must let the next agent do:

- **`README.md`**: decide from one paragraph whether it needs this dataset at
  all. Species, subject and session counts, modalities, scientific aim, key
  paper, overlap with other packages. The other files are read only if the
  answer is yes.
- **`experiment.md`**: understand what was done, as in a paper's methods
  section. It describes the experiment, not an interpretation of it.
- **`modalities.md`**: know, for each recording modality, what it is and how to
  trust it.
- **`scientific-context.md`**: know what the study was for and, above all, what
  could mislead an analysis of it. Required for every package; see below.
- **`schema.yaml` + `metadata/*.parquet` + stores**: load any table or signal
  with its units and clock known, with no need to guess.
- **`ingestion/`**: rebuild the package (`convert.py`), see what was asked,
  measured and inferred (`ingestion-log.md`), and see what is still unresolved
  (`open-questions.md`).

### What the next agent needs to know, by what the experiment contains
Use this as a checklist of information the package must contain. Ask the user for
anything the documentation does not state, and measure what neither states (see
"Facts you may measure").

- **Every dataset**: the paper, preprint, thesis chapter or methods text; what
  each source file contains; the units and clock of every quantity; how clocks
  are synchronised across devices; subject metadata (line, genotype, sex, date of
  birth, cohort); the experimental design (factors, grain, intended comparison);
  how many labs and rigs recorded the data; whether subjects fall into groups
  that are compared; the period the sessions span.
- **Coverage of the source release**: which files the release, tag or snapshot
  actually covers, **per modality**. Coverage is often partial, and different
  modalities of one dataset can come from different releases. Record each
  modality's source release in `provenance.yaml`.
- **Overlap with datasets already configured** in `data_locations.local.yaml`:
  how many subjects, sessions and recordings this dataset shares with each.
  Count it; do not assume a new dataset is new data.
- **Processing provenance per recording**, not per dataset: which pipeline,
  version and revision produced each recording's derived data, and from which
  source release. Mixed pipelines within one package are normal and must stay
  visible as columns.
- **Derived metrics carried from upstream**: for each one, its definition and
  units read from the source code that computes it. A metric's name is not its
  definition, and an inherited description is not evidence.
- **If a paper, preprint or thesis describes these data**: its region or ROI
  definitions, selection criteria, QC steps, inclusion lists and reported effect
  sizes, and its code and data repositories. What is missing from the text is
  usually in the code repository. This is information for the analyst, not a
  filter to apply; see below.
- **Trials**: whether the experiment is trial-based at all (free exploration is
  not); what defines a trial's start and end; stimulus, response and outcome;
  which task and protocol were run; whether there are blocks or priors.
- **If the user confirms the IBL task** (`ibl_choice_world`): whether the
  protocol uses biased blocks. Analysis will use `skills/ibl-analyze/`, whose
  guidance comes from BWM, so the design caveats below matter most here.
- **Spikes / units**: sorter and version, per recording; which units were kept
  and why (the source-selection rule); what QC exists; whether recordings are
  assigned to anatomy, and whether alignment or histology QC is carried; and for
  every stored per-unit summary metric, the window it covers. Metrics computed
  over the whole recording describe the whole recording. Where the experiment
  has a task period, ship the task-window equivalents alongside them.
- **Two-photon / calcium**: indicator; frame rate; whether traces are raw,
  ΔF/F or deconvolved, and with what; ROI selection. A deconvolved trace is a
  timeseries, not spike times.
- **Video / pose**: camera frame times and clock; which tracking was run.
- **LFP and other bulk signals**: whether they are converted or referenced in
  place. If referenced, record what re-resolves them, never only a local path.

### Facts you may measure
Documentation is routinely silent about a design fact the data can settle.
Measuring such a fact is not guessing, and is better than another round-trip to a
user who does not know either.

- Measure it, record the check you ran, and log it as **measured**. Never let a
  measured fact appear as something the documentation states.
- Measure, don't assume: what a release covers, how design factors are
  distributed across labs, devices, pipelines and years, whether one clock
  contains another, whether a protocol really has the block structure its name
  implies.
- Blocking facts cannot be measured this way: what a quantity means, its units,
  its time base. Those stop and ask (hard rule 1).

### Check the documentation against the data
Papers and release descriptions are wrong about processing often enough that
every processing claim has to be checked against the data's own provenance:
processing logs, release-tag descriptions, and version fields on the files
themselves.

- Check pipeline and version, selection rules, counts, subject attributes and
  date ranges.
- Where they disagree, record both readings in `scientific-context.md` and put
  the question to the authors in `open-questions.md`. Never resolve a
  disagreement silently, and never overwrite what the data says with what the
  paper says.

### Cross-tabulate the design before writing the caveats
Cross-tabulate every session- and recording-level field — lab, rig, device model,
year, pipeline, software version — against the design factors. Anything that
co-varies with a factor is a confound and goes in `scientific-context.md`,
whether or not anyone mentioned it. These are the confounds nobody thinks to tell
you, and an analyst who finds one after you did not is finding it too late.

### Writing `scientific-context.md`
Users often won't know the confounds or caveats of their own data, so don't ask
for them. Derive them from design facts the user can answer or you can measure,
which the checklist above already collects. The file has three kinds of content:

- **Caveats from the design (required).** Work through
  `references/design-caveats.md` and write the consequence for every fact that
  applies to this dataset. The table is the rubric; extend it there when a
  dataset turns up a caveat it does not cover.
- **What a paper says about selection (required where a paper exists).** Record
  its region or ROI definitions, selection criteria, QC steps, inclusion lists
  and reported effect sizes — or where to find them, usually the authors' code
  repository, linked under `Papers`. Where it is cheap to tell, say which
  criteria can be computed from package fields and which cannot. **Never apply
  them.** The package ships what the source release contains, not the paper's
  analysis set; analysts choose their own criteria and need to see the paper's
  to compare. Reproducing a published result is an analysis task, not an
  ingestion gate.
- **What the user knows (optional).** The aim and hypotheses, the associated
  papers, and any confounds the rubric can't see (a rig change midway, a cohort
  with surgery problems). Ask once. "Not stated" and "none known" are valid
  answers. Never fill them in yourself.

## Hard rules
These hold whatever route you take.

1. **Never invent a unit, a time base or any other blocking fact.** Stop and ask.
   The minimum a package cannot ship without is listed in `project-structure.md`
   ("Minimum viable package"). Measuring a fact from the data is not inventing
   it; see "Facts you may measure".
2. **Measure before converting in bulk** (the repo's "Start small" policy): take
   the raw size per session, convert 2–3 sessions **in the mode you intend to
   run**, record wall time and peak memory per worker, estimate the full run, and
   get the user's approval before starting it. Parallel workers contend for the
   source, so per-item time measured sequentially underestimates a parallel run;
   measure at the worker count you will use. Keep the sample build in a
   persistent location, never a scratch directory a restart can lose, and log its
   path in `ingestion-log.md`.
3. **Use the repo's tools; don't rewrite them.** Spikes go through
   `spikepack.write_blosc` only; see `references/spike-shards.md` (read it
   before anything else if there are spikes to convert). IBL-task trials go
   through the existing extraction, but only when the task is
   `ibl_choice_world`; see `references/ibl-trials.md`.

   If a helper you need does not exist, check `references/pending-interfaces.md`.
   **If it is listed there**, tell the user, then write a minimal stand-in inside
   `ingestion/convert.py`, mark it with a `# STAND-IN for <helper>` comment, and
   list it in `ingestion/ingestion-log.md` with what it had to do, so the entry
   becomes the specification for the real helper. **If it is not listed, stop and
   say so.** A stand-in is never the final route and never lives outside
   `convert.py`.
4. **Leave BWM untouched**: its data, schemas, builder output and analysis
   guidance.
5. **Never overwrite or delete an existing package version** without asking.
   A fix is a new version (semver, see `project-structure.md`). A **published
   version is immutable**: any later change, prose included, is a new patch
   version. An unpublished version may still be edited in place.
6. **The package must be readable from anywhere.** Every path a reader needs is
   relative to the package root; anything not converted is referenced by an id
   that re-resolves, never by a local path; `convert.py` takes the output and
   source-cache locations as arguments instead of hard-coding them. The source
   may be a read-only mount, so point every tool's writable paths (caches, index
   tables) at your own working location rather than letting them default into the
   source. A package that only works on the machine that built it cannot be
   published.
7. **Nothing in prose without a source.** Every number in a prose file comes from
   a value you computed, and every descriptive claim names where it came from:
   the data, the documentation, or the user. Do not describe hardware, protocols
   or processing from general knowledge, however confident you are.
8. **Log everything.** Every question and its answer, everything measured,
   inferred and skipped goes in `ingestion/ingestion-log.md`. Anything unresolved
   also goes in `ingestion/open-questions.md`.

## Suggested route
1. Read the source and its documentation. Draft `README.md`, `experiment.md`
   and `modalities.md` from what the documentation states.
2. Establish coverage: what the source release contains per modality, and the
   overlap with datasets already configured.
3. Work through the checklist, ask the user about the gaps, and measure what
   neither the user nor the documentation can state.
4. Measure, convert 2–3 sessions in the intended run mode, estimate, get
   approval.
5. Write `schema.yaml`, the metadata tables and the stores. Reference in place
   whatever is not converted. Quote every YAML value containing prose: an
   unquoted comma or colon inside a flow mapping silently becomes extra keys.
6. Cross-tabulate the design, then write `scientific-context.md`.
7. Write `provenance.yaml`, `SUMMARY.md` and the `ingestion/` files. Write
   `manifest.json` **last**, after the final prose edit, excluding build
   by-products such as caches and logs.
8. Register the package, and verify it by resolving its name the way an analysis
   would.
9. Run the fresh-agent check, fix what it finds, and rewrite `manifest.json` if
   anything changed.

## The fresh-agent check
Required, and worth the time: a build verifies what you told it to verify, so the
errors that survive are the ones you did not think of. Start again from the
package alone, as an agent that has never seen the source. Read `README.md`
first, then load a table and a store and take an analysis one step.

Look for: prose that disagrees with the tables; counts that no longer match; a
status line left stale; the dataset's main caveat missing from `README.md`; a
store whose indexing convention is undocumented or differs from the convention a
reader would assume from elsewhere in the repo; schema entries that parsed into a
shape you did not intend.

## Quality gates
- Reading only `README.md`, a fresh agent can judge whether the dataset is
  relevant.
- Reading the package alone, a fresh agent can load data and start an analysis
  without the raw source and without re-ingesting. This was checked, not assumed.
- No blocking gap was closed by inference. Every question, answer and measured
  fact is in `ingestion/ingestion-log.md`.
- `scientific-context.md` has a caveat for every design fact that applies, and
  names every field found to co-vary with a design factor.
- Every number in the prose files traces to a computed value.
- Every table and store names a declared `time_base`, every column has a `units`
  key, and every schema entry parses to the shape intended.
- Each modality's source release is recorded in `provenance.yaml`, and the
  overlap with configured datasets is stated in `README.md` and
  `scientific-context.md`.
- Where a paper exists, its selection and QC criteria are recorded as
  information, and nothing in the package applies them.
- The full run was estimated from 2–3 sessions in the intended mode and approved
  before it started.
- `ingestion/convert.py` reproduces the build, takes its locations as arguments,
  and calls the repo's tools rather than restating them.
- A rebuild is verified by decoded content, not by file hashes; compressed stores
  are not byte-reproducible.
- The registered name resolves to the package.
