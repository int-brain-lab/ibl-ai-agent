---
name: ibl-submit
description: Use this skill only when the user types `/ibl-submit` (optionally with text) or explicitly asks to send feedback, a message, a report, or chat history to the IBL AI Agent server or the IBL maintainers. Do not trigger on bare `submit` or unrelated submissions (cluster jobs, forms, pull requests).
---

# Sending a message to the IBL maintainers

A submission sends the user's **message** (for example feedback or a bug report about the agent)
to the IBL AI Agent server for the IBL maintainers, together with the agent version and the
operating system and Python version. Everything that will be sent is shown to the user first.

It does not send this session's chat history or a report. If the user asks for either, say that
this is not available yet and offer to send a message instead; to share a report, GitHub Pages
publishing (`skills/ibl-report/SKILL.md`) is the current option.

Do not trigger on bare `submit` or on other kinds of submission (cluster jobs, forms, pull
requests). Those are not this skill.

## Steps

1. **Get the message.** If the user typed text after `/ibl-submit`, use it, keeping their wording.
   Otherwise ask one short question: "What would you like to send to the IBL maintainers?"
2. **Build the draft (nothing is sent).** Run:
   ```bash
   UV_CACHE_DIR=.uv-cache uv run ibl-ai-agent submit --message "<their message>"
   ```
   This redacts secrets from the message, saves the draft as `submissions/<id>.json`, and prints
   the destination, how the server handles submissions, and everything that will be sent.
3. **Show the user the complete output, unchanged.** Do not summarize or shorten it. Secret
   redaction is best effort: `secrets_redacted=0` does not mean the message is safe, so ask the
   user to read the draft.
4. **Confirm, then send.** If the user wants changes, go back to step 2 with the new message and
   show the new draft. Only after the user explicitly agrees to send the draft they were shown:
   ```bash
   UV_CACHE_DIR=.uv-cache uv run ibl-ai-agent submit --send submissions/<id>.json
   ```
   This sends the draft exactly as shown; a draft changed after it was built is refused. Report
   the server response.

## Rules

- **Never send without explicit user confirmation** of the draft shown in step 3.
- **Never edit a draft file.** For any change, build a new draft and show it.
- **Server config is built in.** The agent ships with the default server URL and a write-only upload
  token, so sending works with no setup. The environment variables `IBL_AGENT_SUBMIT_URL` /
  `IBL_AGENT_SUBMIT_TOKEN` override them.
