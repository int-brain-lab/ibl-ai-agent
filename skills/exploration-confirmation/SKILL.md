---
name: exploration-confirmation
description: Use this skill to analyze scientific data: refine, formalize and test hypotheses.
---

Most analysis projects will start with a large dataset, and have three phases:
- Initial planning, in which you ensure you have understood the user's question
- Exploratory analysis, in which we refine the original question, define metrics to measure quantities of interest, and lock statistical tests for later confirmation. Sometimes, the scientific question can evolve substantially in this phase.
- Confirmatory analysis, in which we perform the locked statistical tests.

To avoid p-hacking, exploration and confirmation should happen on different subsets of the data. Not all users are used to working this way; if necessary, explain why it is required and emphasize the need to carefully lock a test plan with a good chance of finding significance, because you only have one shot. 

# Replicates

The data will typically consist of *replicates* that can be treated as statistically independent; most commonly, separate experimental subjects (e.g. mice). One sometimes treats recording sessions from one subject as independent; this can be valid although a concern particularly if the same neurons are repeatedly recorded.  Sometimes one treats separate recording epochs of the same neural population as independent; this is not necessarily wrong although one must include the independence assumption in any null hypothesis.  Simultaneously recorded neurons or randomly split / temporally alternating timepoints are generally not independent.

The definition of a replicate will depend on the question being asked. Example: for a question about brain area X in behavioral condition Y, the replicates would could be recordings with enough high-quality neurons from area X in condition Y; other experiments would not be used at all.

# Data split

Before beginning an analysis, split the dataset into an *exploration set* and a *confirmation set*. The exploration set should contain enough replicates to  refine scientific hypotheses and metrics, estimate session-to-session variability, and define a statistical test that will plausibly show significance on the confirmation set. The confirmation set should be large enough for good statistical power, and give brainwide coverage if scientifically required.

Finding a good replicate definition and data split is essential, and sometimes itself requires research. Stop and discuss with the user before starting exploratory analysis.

For questions requiring a brainwide comparison, the confirmation set should contain all brain locations. Consider using a subset of the repeated site recordings as the exploration set, as described in  `skills/ibl-load/references/repeated_site_pids.md`. 
**IMPORTANT** Do **NOT** start analysis until the user has approved a split strategy.

# Exploratory analysis

The exploratory analysis consists of progressively refining our scientific questions and hypotheses, eventually formalizing them as statistical tests that can be applied to the confirmatory set.

The original question provided by the user is a starting point, but not definitive.  The user might not provide an explicit question at all, or provide only vague areas of interest.  If the user does give a question, it will usually contain vague terms that need explication (refinement) before they can be quantified (e.g. "timescale", "synchrony", "oscillation", "information").  When the user does provide an explicit question or hypothesis, exploratory analysis often suggested a related question is more likely answerable from the data, which should be pursued instead.

Exploratory analysis is primarily based on plots.  The highest bandwidth into the human brain is through the visual system; whether this is true for llms is not yet clear, but analyses should be human-legible.

Exploratory analysis has multiple goals, which are performed simultaneously:
- Refinement of the question, possibly into a quite different question that is more easily answerable
- Explication of vaguely-defined terms in the question
- Defining metrics for quantities in the question
- Graphically validating these metrics with "sanity-check" plots using the exploration set
- Graphically validating that we see effects (e.g. correlations between quantities, differences between conditions) in the exploration set
- Formalizing statistical tests for these effects, to be applied to the confirmation set

In rare cases, it is possible to define the confirmatory analysis without any exploratory analysis, in which case all data are used as confirmatory.  **Always check with the user before assuming this**.

### Metric Definition Figures

When defining or revising a metric that compresses a structured signal, distribution, or time series into one or more scalar values, make a didactic metric-definition figure during exploration. For ideas: `skills\exploration-confirmation\references\exploratory-analysis-ideas.md`.

The figure should show:
- one representative source signal or distribution;
- the operation that produces each scalar, such as a peak, threshold crossing, integral, slope, latency, or fitted parameter;
- any companion metric needed to distinguish shape components that a single scalar can confound.

If a metric cannot be explained clearly in such a figure, treat that as evidence the metric definition needs refinement.

### Parameter Selection During Exploration

When defining a metric that depends parameters such as an analysis window, threshold, smoothing scale, bin size, choose this value carefully and systematically prior to locking for confirmatory analysis. Do not just stick with the initial value.

For example, compare a grid of scientifically plausible parameter values, including the original choice and higher and lower values. Plot how the effect size and direction vary across settings. Bear in mind that the scientific direction of the project may have changed since the original question, so do not let the original question bias your choice. Adversarially criticize possible choices of metric. Consider performing significance tests for different parameter definitions on the exploration set before locking for confirmatory analysis.

# Adversarial criticism of confirmatory analysis plan

**IMPORTANT** A situation where the hypothesis is true, but the precise tests locked for confirmation come out insignificant, is a failure.  Before locking a confirmatory analysis, adversarially consider scenarios where this could happen. Disucss any such concerns with the user and together plan locked that are likely to be robust to differences between confirmation and exploration sets.

Consider using omnibus tests of general hypothesis rather than tests of highly specific effects to increase the chance of confirmatory significance.

For ideas: `skills\exploration-confirmation\references\confirmatory-analysis-ideas.md`.


# Confirmatory analysis

Once the user has agreed to our locked tests, we run them on the confirmation set to determine if they were actually valid. Usually this will lead to one p-value per hypothesis.

**DO NOT PROCEED TO CONFIRMATORY ANALYSIS WITHOUT EXPLICIT USER APPROVAL**

# Report writing

Finally, write a report.  This need not describe the historical sequence of exploratory steps that led to our hypotheses, but instead lead the reader through a series of logical steps to didactically introduce the questions that were addressed, the approach used to answer it, and the conclusion.  It should include plentiful graphics, such as:

- illustrations of metrics computed from the data, for example neurons/experiments/etc.
- plots suggesting effects for example replicates, usually from the exploration set
- plots demonstrating whether the effects hold reliably in the confirmation set.
