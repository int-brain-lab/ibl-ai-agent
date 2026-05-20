# IBL AI Agent

**IBL AI Agent** helps you use a coding agent such as [**OpenAI Codex**](https://openai.com/codex/) or [**Claude Code**](https://www.anthropic.com/product/claude-code) to interactively analyze [**International Brain Laboratory (IBL)**](https://www.internationalbrainlab.com/) data.

To use it: clone this repository, start your coding agent inside the main directory, and ask the agent a scientific question about IBL data. The repository gives the agent the instructions it needs to write analysis code, make plots, write a report, and publish it on the web.  It is designed for collaborative, scientist-in-the-loop operation: you work with the AI to explore data, refine hypotheses, and perform final confirmatory analysis. 

## What This Is

- A **work in progress**.
- An invitation for feedback, issues, pull requests, and collaborators.
- A repository designed for agentic coding workflows, **requiring an agentic coding subscription**.
- A **scientist-in-the-loop** workflow, not a one-shot answer generator.
- A way to ask focused questions about IBL data.
- A compressed representation of the **IBL Brain Wide Map (BWM) data** that makes BWM analyses easier and quicker.

The agent is primarily tested with OpenAI Codex using GPT5-5. Claude Code has also been tested but less extensively.

## What This Is Not

- It does not guarantee correct scientific answers.
- **You are responsible** for any scientific results produced. The agent does not replace your judgment. Before believing any conclusions, you still need to inspect the code, plots, statistics, and assumptions.
- It is not meant to run unattended. The best workflow is interactive: explore data and refine hypotheses with the agent before performing your confirmatory analysis.
- It is not a raw Neuropixels or SpikeGLX preprocessing pipeline.
- **You are responsible** for API, compute, storage, and download costs. Be careful with pay-as-you-go plans: long agent sessions, repeated retries, large analyses, or accidental loops could lead to high charges.
- The IBL AI Agent maintainers are not responsible for any costs incurred while using this repository, or any results produced. 
- Like any agentic coding system, it can run commands and edit files through your coding agent. Use sandboxing and permissions carefully, as always when using agentic coding.

## Data

The agent is specialized for working on the **IBL Brain Wide Map (BWM)**, a large collaborative dataset mapping neural activity across the mouse brain during a decision-making task. The flagship paper is
[A brain-wide map of neural activity during complex behaviour](https://www.nature.com/articles/s41586-025-09235-0).

This repository uses a compressed representation of the BWM data, that fits the high-quality neurons and behavioral traces of all BWM experiments into less than 10 GB. This enables large-scale analyses to be conducted more quickly than using the original API. The agent will download these datasets into your repo directory so ensure you have ~10GB free. 

See [docs/bwm/README.md](docs/bwm/README.md) for current dataset contents, sizes, and versions, and [docs/data_locations.md](docs/data_locations.md) for local data setup.

## Scientific Workflow

IBL AI Agent is designed for interactive scientific work. Its design philosophy is that scientists usually start from conceptual questions, that need to be refined or even radically changed before precise answers can be given. It takes the following steps:

- **Question clarification:** the agent ensures it was understood the question corectly, and creates an analysis plan together with the user
- **Data split:** the BWM data is split into an *exploratoration set*, which can be used freely and repeatedly to refine questions, metrics, and statistical tests; and a *confirmation set* which is held out for one-time testing of final hypotheses.  If brainwide coverage is requried for confirmatory analysis, consider using a subset of the BWM repeated site recordings for exploratoration.
- **Data exploration:** the agent and user work on a small fraction of the dataset to refine the question further and define quantitative metrics that measure the phenomena of interest.  This step often involves refining analysis parameters such as time bin sizes, deciding whether to consider the mean or median, and running trial statistical tests on the exploratory data.
- **Analysis plan locking:** once the user is satisfied with a statistical approach, a statistical test is locked for confirmatory analysis. To avoid p-hacking this can only be done once, so be sure you have chosen all details carefully, for example by running power analyses or testing the approach on the exploratory dataset.
- **Confirmatory analysis:** the agent runs the locked analysis on held out data.
- **Report writing:** the agent writes a report of the analysis using Quarto 
- **Optional publication:** if the user desires, the report is uploaded to Github Pages. *The user is responsible for all published content, and must check carefully the report prior to upload to ensure no security-sensitive or inappropriate content is included*.

See [docs/workflow.md](docs/workflow.md) for the detailed scientific workflow,
project directory layout, and agent interaction pattern.


## Installation

The expected user path is to work from your own fork:

1. **Install a coding agent** such as Codex or Claude Code, and purchase a monthly subscription.
2. **Clone** this repository to a directory on your computer. If you don't know git, ask your agent to clone https://github.com/int-brain-lab/ibl-ai-agent into a local directory.
3. **Start the coding agent from the cloned repository.**
4. **Type `install`** to have the agent help you complete installation by downloading data files and installing other required tools.
5. **Ask a scientific question about IBL data.**

## Publishing Reports

After Codex writes a final HTML report, it can help you publish the report to a
user-owned GitHub Pages repository. Publishing is always opt-in. Reports should
be published to your own repository, not to the upstream `ibl-ai-agent` repository.

The default public report repository is `ibl-ai-agent-reports`, at URL `https://<github_owner>.github.io/ibl-ai-agent-reports/<project_slug>/`. 

**You are responsible** for checking that the report does not reveal identifying, sensitive, unpublished, or confidential information before making it public. To check, open the rendered HTML report in a browser, review the visible text, figures, captions, tables, links, hover text, and appendices, search for names, usernames, computer names, institutions, email addresses, local paths such as `C:\Users` or `/home`, passwords, tokens, subject identifiers, and unpublished or private data, and review the `files_to_publish` manifest printed by the publishing command.

## Example Reports

Some example reports can be found at https://kdharris101.github.io/ibl-ai-agent-reports/.

## Impressions So Far

- The agent appears to function well, writing code that can analyze the BWM data much faster than possible before 
- This speed improvement, together with the large amount of data available, enables analyses using strict separation of data into exploration/confirmation sets. This approach should greatly help reduce false conclusions, but is still not universal in systems neuroscience.
- The agent often generates good ideas, but is not yet ready to run fully autonomously
- We have not yet found any egregious errors or deception
- The mistakes it makes have been similar to those a human scientist would (for example metrics that might be biased by firing rate or nonsense correlations).  Adding

## Future Work

This is work in progress! Current candidate directions:

- A compressed LFP dataset.
- A compact `bwm_neurobehavior` query layer for broad questions about where
  task, movement, pose, and behavioral-state information is represented in the
  brain.
- Capability to export entire chat logs.
- Stronger examples of negative controls and false-positive failure modes.

## Contribute

Feedback, issues, pull requests, and collaborators are welcome.

- Open an issue for bugs, confusing docs, missing examples, or questionable
  scientific behavior.
- Open a pull request for documentation, skills, data-loading improvements,
  tests, or examples.
- See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) for development setup and
  quality gates.

## FAQs

**Is this safe to run unattended?**

No. The workflow is designed for interactive review of plans, code, plots,
statistics, and caveats.

**Where do outputs go?**

Scientific outputs should go under `projects/<project_slug>/`. See
[docs/workflow.md](docs/workflow.md).

**Does it download data?**

Yes. It will download the main compressed BWM data, and may offer to download more via the API. 

**How much disk space do the public BWM derived datasets need?**

Less than 10 GB for all spikes from good units and behavior data from the BWM.

See [docs/bwm/README.md](docs/bwm/README.md) for more details. You
will also need working space for generated artifacts.

**Can it use private IBL data?**

The repository tells the agent how to access all IBL data via the API, but public BWM local datasets are the default

**How do I know whether a result is trustworthy?**

Don't take the agent's work for it! Inspect the question definition, data split, metric diagnostics, code, exploratory plots, confirmatory statistics, caveats, and report. Treat generated results as scientific claims requiring review, not as final authority.

## Credits

IBL AI Agent builds on data, tools, and scientific work from the International
Brain Laboratory community.

We thank our funders the Wellcome Trust (338992/Z/25/Z) and Simons Foundation (SFI-AN-NC-IBL-00010540-05) for their generous support.