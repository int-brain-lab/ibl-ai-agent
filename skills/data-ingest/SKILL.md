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

**Say what you are doing.** Assume the user has never ingested a dataset. At
each step of the route, tell them in a sentence or two what you are about to do
and why: for example, "converting 3 sessions to estimate time and disk space
before the full run", or "checking the package is complete and readable by an
agent that has never seen the source". After each check, say what it found and
what you changed.

**Record what this skill does not cover.** When it fails you, or makes you do
work it should have prescribed, ask: would this change what an agent does on a
*different* dataset?

- **Yes**: write it in `ingestion-notes.md` at the repository root, as what the
  skill should say, with what happened here as the evidence. Create the file if
  it does not exist. Give each dataset its own section,
  `## <dataset_name> <version>`, and add to an existing section rather than
  starting a second one. That file is the evidence for the next revision of
  this skill, so keep it to lessons that carry over.
- **No**, it is a quirk of this dataset or a choice made for it: log it in the
  package's `ingestion/ingestion-log.md` as **decided** or **measured**, not in
  the notes.

## The end point
One dataset at one version, in `<dataset_root>/<dataset_name>/<version>/`,
registered under `datasets:` in `data_locations.local.yaml`. The exact tree,
headings, YAML shapes and what each file is for are defined in
`project-structure.md`. Read it before writing anything, and do not restate it or
diverge from it.

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
  modality's source release in `provenance.yaml`. If the source is a large
  archive that keeps several versions of its data (IBL's ONE/Alyx, DANDI), read
  `references/archive-sources.md` before choosing any files.
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
  which task and protocol were run; whether there are blocks or priors. If it is
  trial-based, declare a `trials` table that settles: which columns define a
  trial (required) and which are optional event times; how `trial_id` is
  computed; which columns depend on a design fact and are written only once it is
  confirmed; the package's own inclusion rule; and how `events` is derived from
  `trials`, with float64 times and a deterministic order.
  `references/ibl-trials.md` works these choices through for one task; follow it
  as the model, and exactly when the task is `ibl_choice_world`.
- **Spikes / units**: see "What to establish" in `references/spike-shards.md`.
- **Two-photon / calcium**: indicator; frame rate; whether traces are raw,
  ΔF/F or deconvolved, and with what; ROI selection.
- **Video / pose**: camera frame times and clock; which tracking was run.
- **LFP and other bulk signals**: whether they are converted or referenced in
  place (rule 6).
- **Any other modality** (behavioural signals, photometry, …): what it measures,
  its units, its sampling rate or timestamps, and its clock.

### Facts you may measure
When the documentation is silent and the data can settle a fact, measure it
rather than ask a user who likely doesn't know either.

- Measure it, record the check you ran, and log it as **measured**. Never let a
  measured fact appear as something the documentation states.
- Measure, don't assume: what a release covers, how design factors are
  distributed across labs, devices, pipelines and years, whether one clock
  contains another, whether a protocol really has the block structure its name
  implies.
- Blocking facts cannot be measured this way: what a quantity means, its units,
  its time base. Those stop and ask (hard rule 1).

### Check the documentation against the data
Papers and release notes can contain mistakes about processing, so check
pipeline and version, selection rules, counts, subject attributes and date
ranges against the data's own provenance: processing logs, release descriptions,
version fields on the files. Where they disagree, record both readings in
`scientific-context.md` and put the question to the authors in
`open-questions.md`. Never resolve a disagreement silently, and never overwrite
what the data says with what the paper says.

### Check every signal and table before writing the prose
Run these checks on the full build and write what they find into
`modalities.md`. Otherwise the fresh-agent check finds them one round at a time.
Leave the data as the source has it; document, don't correct.

- **Each signal**: what a positive value means and where zero is (sign and zero
  convention); timestamps that repeat or go backwards; whether the signal covers
  the task or recording period, and by how much it falls short.
- **Each event or trial table**: events out of their expected order (a response
  before its stimulus); the count of missing values in every column.
- **Each column**: columns null for every row; dtypes widened from the source by
  missing values (an integer count stored as float).

### Cross-tabulate the design before writing the caveats
Cross-tabulate every session- and recording-level field — lab, rig, device model,
year, pipeline, software version — against the design factors. Anything that
co-varies with a factor is a confound and goes in `scientific-context.md`,
whether or not anyone mentioned it; nobody thinks to tell you about these.

### Writing `scientific-context.md`
Users rarely know the confounds of their own data, so derive them from the
design facts collected above rather than asking. Three kinds of content:

- **Caveats from the design (required).** Work through
  `references/design-caveats.md` and write the consequence for every fact that
  applies to this dataset. The table is the rubric; extend it there when a
  dataset turns up a caveat it does not cover.
- **What a paper says about selection (required where a paper exists).** Its
  region or ROI definitions, selection criteria, QC steps, inclusion lists and
  effect sizes, or where to find them (usually the authors' code, linked under
  `Papers`), noting which criteria package fields can compute. **Never apply
  them**: the package ships the source release, not the paper's analysis set.
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
2. **Measure before converting in bulk**: convert 2–3 sessions, estimate the
   full run's time, memory, output size and disk use, and get the user's
   approval before starting it.
   - Run the sample at the worker count you will use; workers contend for the
     source, so sequential timings underestimate.
   - Estimate output size from source metadata where it exists (a per-unit spike
     count, a file listing); extrapolating from the sample can miss by a third.
   - Count disk used by any local cache of the source, not only the output.
   - Keep the sample build somewhere persistent, never scratch, and log its path.
   - To change `convert.py` mid-run, write a copy, check it compiles, then move
     it over the original; later stages re-import it.
3. **Write what the package needs in `ingestion/convert.py`; reuse the repo
   only where it fits.** Readers, loaders and table builders specific to this
   dataset live in `convert.py`. Do not edit repo modules to make them fit, and
   leave BWM's untouched (rule 4). Two things are not optional: spikes go
   through `spikepack.write_blosc` only (read `references/spike-shards.md` before
   anything else if there are spikes), and any `trials` table follows the Trials
   checklist item. List each piece of code written for the package, with what
   it does, in `ingestion/ingestion-log.md`.
4. **Leave existing packages and the repo's shipped datasets (BWM included)
   untouched**: their data, schemas, builders and analysis guidance.
5. **Never overwrite or delete an existing package version** without asking. A
   fix is a new version, by the versioning rules in `project-structure.md`.
6. **The package must be readable from anywhere.** Every path a reader needs is
   relative to the package root; anything not converted is referenced by an id
   that re-resolves, never by a local path; `convert.py` takes the output and
   source-cache locations as arguments. Point every tool's writable paths
   (caches, index tables) at your own working location, never the source, which
   may be read-only.
7. **Nothing in prose without a source.** Every number in a prose file comes from
   a value you computed, and every descriptive claim names where it came from:
   the data, the documentation, or the user. Do not describe hardware, protocols
   or processing from general knowledge, however confident you are.
8. **Log everything.** Every question and its answer, every decision, and
   everything measured, inferred and skipped goes in
   `ingestion/ingestion-log.md`, tagged as such. Anything unresolved
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
6. Run the signal and table checks, then cross-tabulate the design and write
   `scientific-context.md`.
7. Write `provenance.yaml`, `SUMMARY.md` and the `ingestion/` files. Write
   `manifest.json` **last**, after the final prose edit, excluding build
   by-products such as caches and logs.
8. Register the package, creating `data_locations.local.yaml` if it does not
   exist, and verify it by resolving its name the way an analysis would.
9. Run the fresh-agent check, fix what it finds, then run it again with a new
   agent. Repeat until a check finds nothing new. If a third check still finds
   problems, stop and ask the user. Rewrite `manifest.json` after the last fix.

## The fresh-agent check
Required: a build only verifies what you told it to, so the surviving errors are
the ones you did not think of. The checker starts from the package alone: reads
`README.md`, loads a table and a store, and takes an analysis one step.

Run each check as a separate agent (for example a subagent) given only the
package path, not this conversation. It cannot be you: you know what the package
was meant to say. Each rerun uses a new agent, not one that saw earlier fixes.
Log each round's findings and fixes in `ingestion/ingestion-log.md`.

Look for: prose that disagrees with the tables; counts that no longer match; a
status line left stale; the dataset's main caveat missing from `README.md`; a
store whose indexing convention is undocumented or differs from the convention a
reader would assume from elsewhere in the repo; schema entries that parsed into a
shape you did not intend.

## Quality gates
- `README.md` alone lets a fresh agent judge whether the dataset is relevant.
- The package alone lets a fresh agent load data and start an analysis, and the
  last fresh-agent check, run after the final fix, found nothing new.
- No blocking gap was closed by inference; every question, answer and
  measurement is in `ingestion/ingestion-log.md`.
- Every number in the prose files traces to a computed value.
- The full run was estimated from 2–3 sessions in the intended mode and approved
  before it started.
- `scientific-context.md` has a caveat for every design fact that applies, names
  every field that co-varies with a design factor, and records any paper's
  selection criteria without the package applying them.
- `provenance.yaml` records each modality's source release; `README.md` and
  `scientific-context.md` state the overlap with configured datasets.
- `schema.yaml`: every table and store names a declared `time_base`, and every
  column spec has exactly the keys `dtype`, `units` and `description`. Check the
  keys of each spec, not only the column names.
- `ingestion/convert.py` reproduces the build, verified by decoded content, not
  file hashes; it takes its locations as arguments, and its code is listed in the
  log.
- Spikes were written by `spikepack.write_blosc`; any `trials` table settles
  every choice in the Trials checklist item.
- The registered name resolves to the package.
