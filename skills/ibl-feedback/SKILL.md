---
name: ibl-feedback
description: Use this skill only when the user types `/ibl-feedback` (optionally with text) or explicitly asks to submit feedback about the IBL AI Agent. Do not trigger on `/feedback` or bare `feedback`, which may belong to the host coding agent.
---

# Collecting User Feedback

When the user types `/ibl-feedback`, or explicitly asks to report a problem with the
IBL AI Agent itself, help them submit feedback. Feedback bundles their message with the
**current session's chat history** so maintainers can reproduce the issue.

Do not treat `/feedback` or bare `feedback` as this skill's trigger; those may be
native feedback commands for the host coding agent.

## Steps

1. **Get the message.** If the user typed text after `/ibl-feedback`, use it. If not,
   ask one short question: "What would you like to report?" Keep their wording.
2. **Build and save locally first (never auto-send).** Run:
   ```bash
   UV_CACHE_DIR=.uv-cache uv run ibl-ai-agent feedback --message "<their message>" --no-send
   ```
   This locates the session transcript, redacts secrets and local paths, and writes
   a copy under `feedback/<id>.json`. It does **not** contact the server.
3. **Show the user the result.** Report the saved path, the message/redaction
   counts the command printed, and remind them they can open `feedback/<id>.json`
   to inspect exactly what will be sent.
4. **Confirm, then send.** Only after the user agrees, send it:
   ```bash
   UV_CACHE_DIR=.uv-cache uv run ibl-ai-agent feedback --message "<their message>" --yes
   ```
   (The command re-reads the live transcript so the just-finished exchange is
   included.) Report the `feedback_id` and server response back to the user.

## Rules

- **Never send without explicit user confirmation.** Step 2 (`--no-send`) always
  runs first so the user can review the saved copy.
- **Privacy.** Redaction is automatic but best-effort. Tell the user the saved JSON
  is theirs to inspect, and that the transcript is sent privately to the feedback
  server (it is not made public).
- **If the server is not configured** (`IBL_AGENT_FEEDBACK_URL` /
  `IBL_AGENT_FEEDBACK_TOKEN` or `ibl-agent.local.yaml` keys are missing), the
  command saves locally and tells the user; surface that message and explain they
  can configure the server or share the saved file directly.
- **Host detection is automatic** (Claude Code or Codex). Only pass `--host` or
  `--session-file` if auto-detection picks the wrong transcript.
