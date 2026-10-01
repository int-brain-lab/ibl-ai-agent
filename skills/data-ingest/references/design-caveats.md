# Design caveats

The rubric for the required `Caveats from the design` section of a package's
`scientific-context.md`. Work down the table: for every fact that applies to the
dataset, write the caveat into the package. These are derived from design facts,
not asked of the user, because users rarely know the confounds of their own data.

| Fact | Caveat to write |
| --- | --- |
| Subjects compared in groups (age, genotype, line) | N is the number of subjects. Recording quality (yield, stability, drift, engagement) may differ by group, so compare it before claiming a neural difference. |
| One lab or a few | Lab is not a meaningful source of variation. |
| Many labs, unbalanced across groups | Model or stratify by lab; pooling does not separate the lab effect from the group effect. |
| Groups differ in pipeline, sorter or software version | The group effect is confounded with processing. Compare within one pipeline before claiming a biological difference. |
| A field co-varies with a design factor (device model, year, rig) | Name it as a confound and give the cross-tabulation. |
| The dataset overlaps another configured package | Replications across the two are not independent. State which sessions overlap. |
| Sessions span learning or a long period | Neural and behavioural drift across sessions; don't pool naively. |
| Groups recorded in different regions or depths | Region differences can look like group differences. |
| Per-source summary metrics computed over the whole recording | They do not describe the task period. Recompute in-window before using them for selection or QC. |
| Recordings assigned to anatomy without alignment or histology QC | A localisation error cannot be told from biology. |
| Calcium traces deconvolved | They are rate estimates, not spikes. |
| Not trial-based | Trial-aligned guidance does not apply. |
| IBL task without biased blocks | `probabilityLeft` and `prior_and_block_semantics.md` do not apply. |
| Analysis will use `skills/ibl-analyze/` | Its QC and reproducibility guidance is BWM evidence (12 labs, one group of mice), not a general law. |

Add a row when a dataset turns up a caveat this table does not cover, and put the
evidence for it in `ingestion-notes.md`.
