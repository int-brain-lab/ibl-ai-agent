## Purpose
Canonical lifecycle for interactive scientific analysis with IBL data.

Use this for scientific IBL analysis by default. The goal is to prevent the agent from guessing a convenient metric when a scientist expects careful discussion, and to make metric-validation diagnostics the normal first analysis step unless the user explicitly says not to bother.

## Core rule
Do not silently choose arbitrary metrics. First make the scientific quantity, candidate operational definitions, metric-validation diagnostics, and statistical unit explicit. Validate the metric itself before using it for downstream behavioral or neural analysis, unless the user explicitly requests a quick draft or says to skip validation.

Metric validation means showing that the metric measures the intended quantity from the raw or closest reconstructable source signal. Coverage tables, downstream correlations, psychometric splits, decoding scores, or group differences are not sufficient metric validation by themselves.

Metric validation is a user-facing gate, not only a section inside the final script or report. For custom, ambiguous, proxy, stored-derived, event-aligned, trial-aligned, neural, behavioral, or state metrics, produce the graphical validation diagnostics first, present them to the user, and wait for feedback before writing or running the main downstream analysis. Continue without waiting only when the user explicitly requests a quick draft, explicitly says to skip validation, or explicitly delegates continuation after seeing the diagnostic plan or metric risk note.

## Stepwise consent for open-ended analyses
Read-only repository inspection and planning are autonomous. A reply approving a clearly proposed next action authorizes that action; do not request the same approval again. If the proposed action was ambiguous, clarify before execution. Do not infer authorization for downloads, metric acceptance, scale-up, confirmation, or publication from a generic "proceed" unless that specific action was clearly proposed. Preserve the split, metric-validation, large-run, and confirmatory approval gates in `AGENTS.md` and `skills/exploration-confirmation/SKILL.md`.

## Artifact Routing
Before writing scientific artifacts, classify the artifact type.

- Metric/data/feature validation artifacts, adversarial metric checks, risk notes, and supporting figures: `reports/validations/<slug>/`.
- Final reviewed analysis scripts and reports for a scientist-facing question: `projects/public-analysis/`.
- Durable scientific caveats and interpretive background: `skills/ibl-analyze/references/scientific_caveats/`.
- Cross-cutting workflow corrections or historical correction notes: `docs/agent_corrections.md`.

Metric-validation pilots should not be routed to `projects/public-analysis/` unless the user explicitly asks for a reviewed final-answer artifact or the validation is inseparable from that final answer.

## Lifecycle
1. **Clarify the scientific intent.** Identify the biological motivation, hypothesis or comparison, desired scale, data scope, and expected output. Ask targeted questions when these choices would materially change the answer.
2. **Propose metrics before coding.** List plausible operational definitions. For each one, state event anchors/windows, row grain, QC/population scope, denominators, available data source, and whether it is direct, operationalized, or a proxy.
3. **Recommend a default, not a guess.** If one metric is clearly best, say why. If alternatives are scientifically different, ask the user to choose before full analysis.
4. **Validate metrics first.** Before downstream analysis, inspect a few example sessions/cells/trials/regions and make graphical diagnostics showing the source signal, event anchor/window, derived metric value, and failure modes.
5. **Present diagnostics and pause.** Show the rendered validation figures, metric values, coverage/failure modes, and a free-text metric risk note to the user. Ask whether to proceed, revise, or stop before implementing or running the main analysis.
6. **Validate answerability.** Use metric diagnostics, the metric risk note, and user feedback to decide whether the scientific question can be answered with the proposed metric(s), or whether the question, metric, data scope, or assumptions need refinement before continuing.
7. **Refine from diagnostics.** Use pilot plots to revise windows, thresholds, exclusions, or metric definitions. Do not treat the first operationalization as final if diagnostics reveal mismatch or artifacts.
8. **Scale after validation.** Apply the agreed metric and QC rules consistently. If the metric changes after seeing pilot results, state the change and why.
9. **Report across scales.** Present evidence from the smallest relevant unit, then an intermediate session/area/subject level, then the aggregate result.

## Staged Runtime Rule
Early metric-validation code must run quickly enough to support interactive scientific judgment. Do not start validation by fanning out over the full database when a small slice can test the metric mapping.

Default staged execution:
- **single example:** first run one session, insertion, cell, region, or short trial slice that can render source-signal diagnostics quickly;
- **small pilot:** then run a few representative sessions/examples to test variability, failure modes, and code robustness;
- **scale-up:** only after the metric, diagnostics, and runtime look acceptable, run the full dataset or broad BWM/database analysis.

Validation scripts should make this staging explicit with constants such as `MAX_SESSIONS`, `MAX_EXAMPLES`, or `PILOT_EIDS`, or by writing reusable intermediate metric tables. If a validation step is expected to take more than a short interactive wait, state the expected scope/cost before running it and prefer a smaller pilot first.

## Clarification triggers
Ask the user or produce a narrow metric proposal when the answer depends on:
- ambiguous scientific terms such as response, latency, modulation, selectivity, engagement, movement, quiescence, strongest region, good unit, or reliability;
- event anchor/window choices;
- trial, neuron, insertion, session, subject, lab, or region as the output grain;
- QC thresholds or inclusion/exclusion scope;
- whether a stored derived feature is acceptable or recomputation is needed;
- the statistical unit of independent variability;
- live data access, long-running computation, or large fan-out downloads.

## Metric proposal checklist
For each proposed metric, state:
- scientific quantity being approximated;
- direct / operationalized / proxy classification;
- event anchor, baseline window, response/search window, and sign convention when applicable;
- input grain and output row grain;
- QC filters, denominators, and missing-modality handling;
- whether it uses aggregates, trial/session files, spikes/events, wheel/pose, or recomputation;
- applicable scientific caveats from `scientific_caveats/` and why they matter here;
- independent unit for statistical inference and what would be pseudoreplication.

## Metric-Validation Diagnostic Expectations
For scientific analyses, metric-validation figures should show enough raw or reconstructed structure that a scientist can judge whether the metric is valid before it is used in downstream analysis. This is required by default for custom, ambiguous, proxy, stored-derived, event-aligned, trial-aligned, state, response, behavioral, or neural metrics. Skip only when the user explicitly asks not to bother, or when producing a clearly labeled quick draft.

Prefer:
- example aligned traces or rasters with baseline/response windows marked;
- example behavioral traces for movement/quiescence metrics;
- per-example metric values linked to the underlying traces;
- distributions that reveal censoring, edge effects, missing values, and pathological cases;
- side-by-side typical and problematic examples when available;
- quality flags, likelihoods, frame/event counts, denominators, and missing-modality coverage when available;
- obvious confound checks, such as movement contamination for arousal or pupil proxies.

Metric validation should include at least one adversarial check chosen to reveal how the metric could mislead scientifically. Choose this check from the active scientific caveats and the current data shape rather than from a fixed recipe. Examples include time-in-session for firing-rate/state metrics, movement contamination for arousal/video proxies, edge censoring for latency metrics, or label leakage for decoding.

Metric validation should end with a short free-text metric risk note:
- what the diagnostics support;
- what they do not rule out;
- plausible biological and technical interpretations of unexpected structure;
- what would most change the interpretation;
- whether downstream scale-up should proceed, pause, or be revised.

Do not count downstream outcome plots as metric validation by themselves. Psychometric curves split by a proxy, proxy/RT scatter plots, decoding performance, and group differences can follow validation, but they do not demonstrate that the metric itself measures the intended biological quantity.

Do not treat a final combined analysis figure as satisfying this gate if the user has not already seen and accepted the metric-validation diagnostics. The first executed artifact should be a pilot/validation artifact unless the user explicitly chose a quick draft or skip-validation path.

## Quick-draft exception
If the user explicitly asks for a quick script, draft implementation, non-executed scaffold, or says to skip validation, proceed without waiting for approval, but keep assumptions visible in constants, Methods, Caveats, and TODOs. Never present a quick-draft or unvalidated metric as a validated scientific choice.

## Statistical unit rule
Before statistical tests or population-level claims, state the independent unit of variability. Trials nested in a session, neurons nested in an insertion/session/subject, and regions summarized from the same sessions are not automatically independent population samples.
