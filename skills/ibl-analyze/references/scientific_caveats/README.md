# Scientific Caveats

This directory stores reusable scientific background that should inform metric proposals, validation diagnostics, and metric risk notes. These files are descriptive, not fixed instructions: use them to recognize possible biological and technical interpretations, then design diagnostics appropriate to the current question and data.

## Caveat Index

| Caveat | Keep In Mind When | Core Risk |
| --- | --- | --- |
| [Firing-rate nonstationarity](./firing_rate_nonstationarity.md) | baseline/pre-stimulus rate, state metrics, correct/error or lapse analyses, movement/quiescence comparisons, regional firing-rate summaries | Apparent condition effects can reflect slow or abrupt within-session firing-rate changes whose biological versus technical interpretation is unclear. |
| [Pooled population ACG](./pooled_population_acg.md) | pooled spike-time ACGs, region/layer population timing, population timescale metrics | Pooled ACGs mix intrinsic unit autocorrelation, synchrony, firing-rate composition, rhythmicity, and task/state modulation. |

## How To Use

- Mention applicable caveats in the metric proposal or validation report.
- Use caveats to choose adversarial metric checks.
- If diagnostics reveal unexpected structure, write a free-text metric risk note rather than forcing a pass/fail label.
- Keep validation artifacts in the project's `exploratory-analyses/` directory, following `AGENTS.md`; keep only approved, durable caveat summaries here.
