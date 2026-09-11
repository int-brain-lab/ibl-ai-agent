---
name: skill-maintenance
description: Use this skill when editing, refactoring, pruning, or reorganizing the repo-local IBL skill system; prefer brevity, canonical references, and continuous cleanup while preserving scientific guidance needed to answer IBL neuroscience questions.
---

# Skill Maintenance

## Use this skill when
- Editing any file under `skills/`.
- Moving rules between `AGENTS.md`, `skills/*/SKILL.md`, `skills/*/references/*.md`, or `docs/`.
- Reducing duplication, pruning stale guidance, or adding a durable correction.

## Core rule
Keep the skill system compact enough to load quickly and precise enough to answer neuroscientific questions on IBL data. Preserve scientific constraints; remove repeated prose, obsolete branches, and generic advice that Codex already knows.

## Target structure
- `AGENTS.md`: repo-wide runtime behavior.
- `skills/*/SKILL.md`: short trigger, routing, workflow, and quality gates.
- `skills/*/references/*.md`: technical details, API patterns, rubrics, and conditional deep dives.
- `docs/`: long reports, design notes, historical explanations, and experimental ask-mode contracts.

## Brevity policy
- Prefer one canonical source for each rule; other files should link to it.
- Keep `SKILL.md` files procedural, also including explanations why these procedures are necessary. Move tables, long examples, and paper details to references or docs.
- Do not duplicate BWM loading policy outside `ibl-load/references/bwm_runtime_policy.md`.
- Do not duplicate scientific metric semantics outside `ibl-analyze/references/scientific_context_and_metric_semantics.md`.
- Keep examples short and executable; remove examples that are only narrative.
- Archive or delete stale material only when a newer canonical source covers the same behavior.

## Size awareness
Treat skill context as scarce, but do not enforce hard line limits. Preserve important scientific, operational, and safety constraints even when a file must stay longer.

When a skill edit materially grows a file or the tree:
- check whether the new content duplicates a canonical owner;
- prefer a short rule plus a reference link over repeated prose;
- move historical narrative, tutorials, and long examples to `docs/`;
- run `python scripts/report_skill_sizes.py` for visibility;
- mention any meaningful size increase or consolidation opportunity in the summary.

## Refactor loop
1. Inventory files with `find`, `rg`, and `wc -l`.
2. Identify repeated rules, stale references, and oversized default-runtime files.
3. Choose the narrowest canonical target before editing.
4. Replace duplicated prose with a short rule plus a link to the canonical file.
5. Preserve source-backed scientific facts unless they are moved to a clearer canonical location.
6. Verify links, line counts, and important trigger phrases after editing.
7. Report changed files, preserved invariants, and remaining cleanup candidates.

## Quality gates
- A future agent should know which file to read next without loading the whole tree.
- Runtime paths should favor `ibl-load`, `ibl-analyze`, and `ibl-report`; maintainer notes should stay out of normal scientific answers.
- Every skill edit should leave the tree clearer, better organized, or intentionally more complete. If it grows the tree, the added value should be explicit.

## Durable Instruction Maintenance

### Feedback assimilation policy with human approval

When feedback says a prior scientific answer, script, report, or workflow was wrong, treat it as a candidate durable correction, not automatic permission to edit instructions.

This applies to feedback about scientific/IBL analysis answers, reviewed scripts, reports, or scientific-analysis workflows. Ordinary repo-maintenance requests, direct skill-edit requests, and explicit user-approved instruction changes do not need this approval flow.

Required behavior:
1. Identify the reusable failure pattern, in the broadest possible context. For example, if the user reports a problem with one analysis or metric, consider whether you can deduce a generic rule that will avoid the problem also for other analyses or metrics.
2. Draft a proposed change to the instruction files to minimize the chance of the problem recurring. Include not just a rule change, but also an explanation of why the rule is necessary.
3. Ask for explicit approval before editing durable guidance such as `AGENTS.md`, `skills/*/SKILL.md`, `skills/*/references/*.md`, or `docs/agent_corrections.md`.
4. After approval, update the narrowest file: repo-wide behavior in `AGENTS.md`, workflow behavior in `skills/*/SKILL.md`, technical caveats in `skills/*/references/*.md`, or cross-cutting history in `docs/agent_corrections.md`.
5. Report exactly what changed.

Write durable corrections as reusable invariants, not one-off artifact notes.

### Skill maintenance and anti-bloat

When editing repo-local skills or references, use this skill first. Prefer brief canonical rules, move technical detail to references, and replace duplicated prose with links to the single owner.
