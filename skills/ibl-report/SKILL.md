---
name: ibl-report
description: Use this skill to produce a final report of a project.
---

# IBL Report

## Use this skill when
- You need to perform a final writeup of a project.

## Output format

Prepare a scientific report using Quarto. Resolve the project directory using `AGENTS.md`; save source as `report.qmd` and render HTML to `report/report.html`, with only required web assets alongside the HTML. PDF is an optional additional output at `report.pdf`; for an explicit PDF-only request, omit HTML and the Pages publishing offer. Keep source, PDF, and private notes outside `report/`, because the publisher collects web files recursively from that directory.

The report should have the following sections:

- Original question asked by the user
- Introduction
  - Biological context. Include any motivation provided by the user, and web search if necessary to find relevant prior work.
- Refinement of the question
  - Describe if and how the question changed during exploratory analysis
  - Include plots from exploratory analysis to illustrate the exploratory analysis narrative
  - Include figures didactively explaining analysis metrics chosen
  - End with a clear statement of the revised question
- Locked analysis plan
  - Describe the confirmatory analysis strategy locked after exploratory analysis
- Results of confirmatory analysis
  - Include a narrative of the confirmatory analysis results
  - Include plots illustrating these main results
- Post-hoc analyses
  - Describe post-hoc analyses conducted
  - Include figures illustrating these results
- Discussion
  - Describe how the result relates to prior knowledge and the relevant literature
  - Include any caveats of this result (positive or negative)
  - Describe any lessons learned for future AI-based analyses of this data

## Prose style

State findings directly: figure + 1-2 sentence caption/interpretation. No boilerplate transitions or restating section purpose.

## Figures

For HTML reports (`format: html`), always set `lightbox: true` in the YAML header so figures are clickable/zoomable. This option has no effect on PDF reports (`format: pdf`).

The report should contain plentiful figures. Include all figures made in the original research except for "dead ends" that did not lead to the main results. Make new figures if necessary for illustration purposes, but do not conduct new research in the writeup phase.

## AI instruction file suggestions

If the project suggests reusable improvements to agent guidance, follow `skills/skill-maintenance/SKILL.md` and write proposals in `<project_root>/<project_slug>/instruction-suggestions.md`, outside the publishable report directory. Do not edit durable guidance without approval.

When suggesting edits, prioritize edits that would be generally useful for future project, for example alterations in workflow, quality control, or data management, over edits that are specific to this project, for example new metrics or analysis techniques.

## Privacy, security, and optional public publishing

*IMPORTANT* Users may make the reports public. Always ensure the report contains no information that could identify the user or constitute a security risk, such as directory or computer names, institution names, passwords, tokens, private local paths, usernames, host names, details of installation, or the names or identifying information of other individuals.

After writing the final Quarto HTML report, ask the user whether they want to publish it publicly on GitHub Pages. Publishing is always opt-in. If the user says yes, ask which GitHub account or organization should own the public report repository, and optionally ask for a URL slug if the project slug is not suitable.

Before rendering or publishing, perform the needed setup checks yourself. Verify Quarto before report rendering. Verify `git`, `gh`, and `gh auth status` before GitHub Pages publishing. If a required tool is missing, explain the missing tool briefly, ask for permission to install or authenticate when needed, run the setup commands yourself where possible, and then resume the report workflow. Do not ask the user to run shell commands manually.

Before publishing, warn the user exactly:

> This report will be public. I will try to avoid publishing obvious local paths, credentials, cache files, raw data, and private project artifacts, but **you are responsible** for checking that the report does not reveal identifying, sensitive, unpublished, or confidential information.
>
> How to check before confirming: open the rendered HTML report in a browser; review the title, text, figures, captions, tables, links, hover text, and appendices; use browser search for your name, username, computer name, institution, email addresses, local paths such as `C:\Users` or `/home`, passwords, tokens, subject identifiers, and unpublished or private data; then review the `files_to_publish` manifest printed by the publishing command.

If the user confirms public publishing, use the resolved project path (replace the placeholder below):

```bash
ibl-ai-agent publish-report-to-github "<project_root>/<project_slug>/report" --owner <github_owner> --repo ibl-ai-agent-reports --slug <project_slug> --confirm-public
```

The public URL will be:

```text
https://<github_owner>.github.io/ibl-ai-agent-reports/<project_slug>/
```

Do not publish the whole project directory. Only publish the rendered report directory and required static web assets. If the publishing preflight reports blockers, stop and help the user remove the risky content rather than bypassing the check.
