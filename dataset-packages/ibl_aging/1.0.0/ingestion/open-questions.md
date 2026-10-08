# Open questions: ibl_aging 1.0.0

An analysis agent should read this before using the package. Updated 2026-10-01 after the
full build and after reading the paper (Zang et al., Nat. Commun. 2026,
doi:10.1038/s41467-026-74227-1).

## Blocking
None. The full build finished and was checked on 2026-10-01, and the package is registered
in this Studio's `data_locations.local.yaml`.

## For the Zang et al. authors
1. **Which sorter version is in the paper?** The paper says all sessions used "ibl-sorter
   (version 2.35.0)". In the data, BWM sorting is pykilosort/ibl-sorter 1.7.0 (sorter log,
   tag description), and `2.35.0` is only the Alyx `version` field of those datasets
   (probably ibllib). The 64 non-BWM probes record iblsorter 1.9.1 (46), 1.9.0a (2) or
   `3.2.0` (16). Did the paper use this public sorting, or a re-sort of all probes with
   one version that is not in the release? If a single-version sorting exists, can it be
   released? It would remove the sorting/age confound (`scientific-context.md`).
2. **The 16 non-BWM probes whose Alyx `version` is `3.2.0`:** which sorter version produced them?
3. **Old-mouse ages:** the paper gives 10.58–19.90 months (mean 16.58) for the 19 new mice;
   this package computes 10.41–19.61 (session ages from `dob` and session date, 30.44 d per
   month). Which age definition does the paper use?
4. **The 4 non-BWM probes with no sorting** (`daadb3f1…`, `ee2ce090…`, `57edc590…`,
   `61bb2bcd…`; full IDs in `recordings` where `has_spikes == false`): were they excluded on purpose, or are they
   still to be sorted? They are kept in `recordings` with `has_spikes = false`.
5. **Which 367 sessions / 503 insertions** make up the paper's analysis set? A list (or the
   visual-inspection and alignment outcomes) would let an analysis reproduce it exactly.
6. **Rig changes, surgery problems, cohort effects:** none are known to the user beyond the
   paper. Surgery is described in a publication the paper cites (Appendices 2 and 3), which
   was not read during ingestion.

## Not blocking, but an analysis must know
7. **The package is broader than the paper's analysis set.** It has all 497 tagged sessions
   and all `label == 1` units. The paper's session, trial and neuron criteria (firing rate
   > 1 Hz, presence ratio > 0.95, its 16 regions, first 400 trials, RT 0.08–2 s, …) are in
   `scientific-context.md` and are not applied here.
8. **Sorting is confounded with age, lab and recording year** (r = 0.75; 90 % of sessions
   ≥450 d use the non-BWM sorting). See `scientific-context.md`.
9. **Sorter version is not a column.** `recordings.sorter` is the ALF collection name
   (`pykilosort` / `iblsorter`). The versions above were read from Alyx during ingestion. A
   `sorter_version` column would need a new package version (minor).
10. **Line, genotype and cohort are not stated.** Strain C57BL/6 is from the user and the paper.
11. **Preprint vs published.** The release tag cites the preprint DOI (v1–v3 exist); the
    package cites the published paper. Which version the release matches is not stated.
12. `reaction_time` is absent upstream and not written.
13. `rig` is null, so rig effects cannot be modelled from this package.
14. Units taken from ALF convention and not verified against a spec: `rewardVolume` (µL),
    wheel position (rad), pose (px).

