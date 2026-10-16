# Trials: worked example (IBL task)

This file works through the trials choices in `skills/data-ingest/SKILL.md` for
the IBL task. For a package declaring `task: ibl_choice_world` it is also the
**contract**: follow it exactly, because the IBL analysis skills
(`skills/ibl-analyze/`, `skills/ibl-load/references/ibl_behavior_task.md`) read
these column names. For any other task, read it as a model of the choices to
settle, not as the columns to write.

Build the table in `ingestion/convert.py`. Do not reuse `bwm_simple._build_trials`:
it requires BWM's roster, `probabilityLeft` and `bwm_include`.

## Which columns define a trial — required
Its extent, stimulus, response and outcome, named as the loaded source names
them. If one is missing, it is an error and ingestion stops.
`eid`, `intervals_0`, `intervals_1`, `stimOn_times`, `contrastLeft`,
`contrastRight`, `choice`, `feedbackType`

`feedbackType` is required but `feedback_times` is not. What happened defines
the trial; when it happened is just an event time.

## Which columns are optional
Mostly event times. If one is missing, skip it and record the skip in both
`ingestion/ingestion-log.md` and `ingestion/open-questions.md`. These vary by
rig, protocol and release.
`probabilityLeft`, `goCue_times`, `firstMovement_times`, `response_times`,
`feedback_times`, `goCueTrigger_times`, `stimOff_times`, `rewardVolume`,
`reaction_time`

**Keep the written table's column order canonical**, dropping absent optional
columns where they sit: `session_id`, `trial_id`, `choice`, `feedbackType`,
`probabilityLeft`, `contrastLeft`, `contrastRight`, `intervals_0`,
`stimOn_times`, `goCue_times`, `firstMovement_times`, `response_times`,
`feedback_times`, `intervals_1`, then the remaining optional columns in the
order listed above. One order across packages is what makes two IBL-task
`trials` tables comparable at a glance, and after the key it is BWM's.

## How `trial_id` is computed
Per session, as a cumulative count within the session. Never read it from the
source. The table's primary key is (`session_id`, `trial_id`).

## Which columns depend on a design fact
**Write `probabilityLeft` only if biased blocks are confirmed**, either by the
documentation or by measuring the block structure in the data (log it as
measured; see "Facts you may measure" in `skills/data-ingest/SKILL.md`).
Otherwise write neither it nor any block-derived column, and say so in the
package's scientific-context.md.

## Caveats to add for this task
Add these to the package's `Caveats from the design`, alongside those from
`skills/data-ingest/references/design-caveats.md`:

| Fact | Caveat to write |
| --- | --- |
| No biased blocks | `probabilityLeft` and `skills/ibl-analyze/references/prior_and_block_semantics.md` do not apply. |
| Analysis will use `skills/ibl-analyze/` | Its QC and reproducibility guidance is BWM evidence (12 labs, one group of mice), not a general law. |

## The inclusion rule
**Never write `bwm_include`.** It is BWM's trial mask and means nothing in
another package. Declare the package's own inclusion rule in `schema.yaml`.

## The session key
Required as `eid` in the loaded source; written as `session_id`, like every
other table. Rename it in the builder.

## How `events` is derived from `trials`
`events` is derived from `trials`, never collected alongside it, and derived
**deterministically**: two builds of one package, and two packages built to the
same contract, number and order the rows identically.

Build it in `ingestion/convert.py` too, importing `EVENT_COLUMNS` from
`ibl_ai_agent/datasets/bwm_ephys.py` rather than copying it: the event names and
the trial columns they come from are the one piece of this the repo already
owns. Then, matching `bwm_ephys._build_events`:

- skip event types whose source column is absent, and drop rows whose source
  time is NaN;
- sort by `session_id`, `trial_id`, `event_time`, `event_name`, with a stable
  sort (`kind="mergesort"`);
- assign `event_id` after sorting, as a cumulative count per session;
- emit `session_id`, `event_id`, `event_name`, `event_time`, `trial_id`,
  `event_value` in that order, with `event_time` **float64** and `event_value`
  null for IBL-task events.

Do not call `bwm_ephys._build_events` itself: it casts to float32 internally,
and at t ~ 3000 s float32 spacing (~0.24 ms) is coarser than the 0.1 ms spike
quantization.
