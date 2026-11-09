---
name: ibl-submit
description: Use this skill only when the user types `/ibl-submit` (optionally with text) or explicitly asks to submit their report, feedback, or chat history to the IBL AI Agent server. Do not trigger on bare `submit` or unrelated submissions (cluster jobs, forms, pull requests).
---

# Submitting to the IBL AI Agent server

A submission sends this session's **chat history** to the IBL maintainers, together with an optional
**message** and, optionally, the project's **rendered Quarto report**. A submitted report stays
private until the IBL maintainers review it; they may then publish it on
https://iblagent.iblcore.org, where anyone can read it. Use it for final reports, bug reports, and
suggestions about the agent.

Do not trigger on bare `submit` or on other kinds of submission (cluster jobs, forms, pull
requests). Those are not this skill.

## Steps

1. **Work out what to send.**
   - **Message:** if the user typed text after `/ibl-submit`, use it, keeping their wording.
   - **Report:** if the current project has a rendered report (`<project>/report/report.html`, see
     `AGENTS.md`), ask whether to include it. If there is no report and no message, ask one short
     question: "What would you like to submit?"
2. **Build and save locally first (never auto-send).** Run, adding `--report "<project dir>"` when
   including the report:
   ```bash
   UV_CACHE_DIR=.uv-cache uv run ibl-ai-agent submit --message "<their message>" --no-send
   ```
   This locates the session transcript, redacts secrets (tokens, passwords, keys; nothing else),
   zips the report, and writes `submissions/<id>.json` (plus `submissions/<id>.zip` for a report).
   Only if the user asks to send the report without the chat, add `--no-transcript` (needs
   `--report`).
   It does **not** contact the server. If the report check lists blockers (data files, secrets or
   absolute local paths in the report or `report.qmd`), stop and help the user fix them.
3. **Show the user the result.** Report the saved path, the message count, `secrets_redacted`, and
   any `warning:` lines. Remind them they can open the saved files to see exactly what will be sent.
4. **Confirm, then send.** When a report is included, tell the user it may be published on
   https://iblagent.iblcore.org after review. Only after the user agrees, send the saved copy:
   ```bash
   UV_CACHE_DIR=.uv-cache uv run ibl-ai-agent submit --saved submissions/<id>.json --yes
   ```
   This sends the saved files exactly as they are, including any edits the user made, so the chat
   after step 2 is not included; to include it, repeat step 2 first. Report the server response,
   and the `report_url` if the report was published straight away.

## Rules

- **Never send without explicit user confirmation.** Step 2 (`--no-send`) always runs first; the
  command only sends when given `--yes`.
- **Privacy.** Only secrets are redacted: paths, usernames and the rest of the chat are sent as-is.
  The chat and message are visible only to the IBL maintainers. A report may be made public after
  review, so the `skills/ibl-report/SKILL.md` privacy guidance for report content applies.
- **Server config is built in.** The agent ships with the default server URL and a write-only upload
  token, so sending works with no setup. The environment variables `IBL_AGENT_SUBMIT_URL` /
  `IBL_AGENT_SUBMIT_TOKEN` override them.
- **The transcript is found automatically** from the session id that Claude Code or Codex sets in
  the environment, in the agent's folder (`CLAUDE_CONFIG_DIR` / `CODEX_HOME` when set, otherwise
  `~/.claude` / `~/.codex`). To send a different session's log, pass `--transcript-file <path>`.
