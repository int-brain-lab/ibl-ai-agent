---
name: ibl-analyze
description: Use this skill for quantitative IBL analysis and scientific questions requiring metric semantics, QC, or statistical-unit guidance.
---

# IBL Analyze

## Use this skill when
- The user asks an IBL scientific question requiring quantitative analysis.
- You need metric semantics, operator selection, QC, or statistical-unit guidance.

## Required first step
For empirical analysis projects, apply the `AGENTS.md` scientific workflow. Before implementing or executing metric validation or downstream analysis, read `references/interactive_scientific_analysis.md` for its validation and feedback gates. For conceptual answers, consult the applicable scientific references without starting an analysis project.

## References
- `references/scientific_context_and_metric_semantics.md`: semantic core for direct/operationalized/proxy metrics, row grain, event anchors, and stored-versus-recomputed decisions.
- `references/scientific_caveats/README.md`: caveat index for metric proposals, adversarial checks, and metric risk notes.
- `references/operators.md`: compact operator routing.
- `references/brainbox_routing.md`: Brainbox module routing before custom code.
- `references/bwm_analysis_patterns.md`: BWM analysis-pattern routing after BWM dataset schemas are inspected.
- `references/reproducibility_qc.md`: QC, statistics, exclusions, and interpretation checks.
- `../ibl-load/references/bwm_runtime_policy.md`: BWM loading policy.
- `../ibl-load/references/brain_regions_qc.md`: region mapping and QC.
- `references/visual_latency.md`: specialized latency recipe.

## Default analysis policy
1. Define the scientific quantity, metric, row grain, event/window, QC scope, denominators, and independent statistical unit.
2. Classify each metric as direct, operationalized, or proxy.
3. Do not map natural-language terms directly onto convenient stored columns.
4. Use stored derived features only within their documented event/window/statistic/denominator definition.
5. Validate custom, ambiguous, proxy, event-aligned, trial-aligned, neural, behavioral, or state metrics with source-to-metric diagnostic plots before downstream analysis unless explicitly skipped.
6. Consult applicable caveats and choose at least one adversarial metric check when validating a metric.
7. Prefer local BWM tables and shards when their schema covers the question.
8. Use `brainbox` operators when they match the metric semantics; otherwise use question-specific NumPy/Pandas/SciPy code and state why.
9. Normalize region labels to one declared atlas mapping before grouping.
10. Use deterministic seeds for stochastic analysis.

## Stored derived feature rule
When using a stored derived feature:
- load and report event name, window specification, statistic, sign convention, and denominator fields when available;
- state whether the feature measures peak, baseline, onset, mean, modulation, or another statistic;
- filter to responsive/analyzable units when the scientific question implies a response property rather than all units;
- do not overinterpret the feature beyond its documented definition.

## BWM routing note
For Brain Wide Map questions, read `../ibl-load/references/bwm_runtime_policy.md` first and `references/bwm_analysis_patterns.md` second. Do not restate or bypass BWM loading policy here.

## Anatomy routing note
For questions involving cortical depth, laminar position, atlas slices, flatmaps, or CCF coordinate lookups, read `../ibl-anatomy/references/atlas_navigation.md`. Key entry points: `xyz_to_depth` for continuous cortical depth from CCF coords; `FlatMap` + `plot_scalar_on_flatmap` for dorsal-cortex flatmaps; `plot_points_on_slice` for probe/unit visualisation on atlas slices.
