---
name: ibl-submit
description: Use this skill only when the user types `/ibl-submit` (optionally with text) or explicitly asks to send feedback, a message, a report, or chat history to the IBL AI Agent server or the IBL maintainers. Do not trigger on bare `submit` or unrelated submissions (cluster jobs, forms, pull requests).
---

# Submitting to the IBL maintainers

A submission sends the user's **message** (for example feedback or a bug report about the agent)
to the IBL AI Agent server for the IBL maintainers, together with the agent version and the
operating system and Python version. Only if the user asks, it also includes this session's
**chat** (all of it or a range of messages) and the project's **rendered report**, and, asked
separately, the report's `report.qmd`. Everything that will be sent is shown to the user first.

Do not trigger on bare `submit` or on other kinds of submission (cluster jobs, forms, pull
requests). Those are not this skill.

## Steps

1. **Work out what to send.** By default, only the message. Ask short questions, one at a time:
   - **Message:** if the user typed text after `/ibl-submit`, use it, keeping their wording. A
     submission may have no message if it has a chat or a report.
   - **Chat:** "Include this session's chat? It includes the messages, tool calls, tool outputs
     and readable reasoning." The answer is none, all of it, or a range of messages.
   - **Report:** if the current project has a rendered report (`<project>/report/report.html`,
     see `AGENTS.md`), ask whether to include it.
   - **Report source:** if the report is included, ask separately whether to include
     `report.qmd`; its comments and hidden code may hold things the rendered report doesn't show.
   - **Publication:** if the report is included, ask: "Do you agree to the IBL maintainers
     publishing this report on https://iblagent.iblcore.org? If not, it stays private: it is
     stored in our database to build the agent's memory." Only a clear yes counts as agreement.

   If the user wants nothing of these, there is nothing to send.
2. **Build the draft (nothing is sent).** Run, adding `--transcript` for the whole chat,
   `--messages A-B` for a range, `--report "<project dir>"` for the report, `--report-source` for
   `report.qmd`, and `--allow-publishing` only if the user agreed to publication:
   ```bash
   UV_CACHE_DIR=.uv-cache uv run ibl-ai-agent submit --message "<their message>"
   ```
   This redacts secrets, checks the report, saves the draft as `submissions/<id>.json` (with
   `submissions/<id>.zip` for a report), and prints the destination and how the server handles
   submissions. If the report check lists problems (data files, secrets, absolute local paths,
   files it can't read), stop and help the user fix them.
3. **Show the user the draft.**
   - Message only: show the complete printed output, unchanged; do not summarize or shorten it.
   - With a chat or a report: the command opens an HTML preview of everything that will be sent
     (`preview=submissions/<id>.html`). Give the user its path and the printed lines, and ask them
     to read the preview. For a range of messages, the message numbers are the ones in the preview
     of the whole chat.

   Secret redaction is best effort: `secrets_redacted=0` does not mean the draft is safe, and
   files marked "not checked for secrets" were not searched.
4. **Confirm, then send.** If the user wants changes, go back to step 2 and show the new draft.
   Only after the user explicitly agrees to send the draft they were shown:
   ```bash
   UV_CACHE_DIR=.uv-cache uv run ibl-ai-agent submit --send submissions/<id>.json
   ```
   This sends the draft exactly as shown; a draft changed after it was built is refused. Report
   the server response.

## Rules

- **Never send without explicit user confirmation** of the draft shown in step 3.
- **Never include the chat, the report or `report.qmd` unless the user asked for it**, and never
  pass `--allow-publishing` unless the user agreed to publication.
- **Never edit a draft file.** For any change, build a new draft and show it.
- **Server config is built in.** The agent ships with the default server URL and a write-only upload
  token, so sending works with no setup. The environment variables `IBL_AGENT_SUBMIT_URL` /
  `IBL_AGENT_SUBMIT_TOKEN` override them.
