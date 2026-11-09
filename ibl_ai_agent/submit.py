"""Build and send a submission for ``ibl-ai-agent submit`` (the ``/ibl-submit`` skill).

A submission is this session's chat (cleaned up, secrets redacted), the user's message and,
optionally, the project's rendered Quarto report. It is always saved locally first; sending
is a separate step. Printing belongs to ``ibl_ai_agent/commands/submit_commands.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, NamedTuple
import io
import json
import os
import platform
import re
import subprocess
import zipfile

import requests

from ibl_ai_agent.errors import SubmitError

# The checks trusted for GitHub Pages publishing, so "sensitive" and "web file" mean the same.
from ibl_ai_agent.report_publish import (
    POSIX_HOME_RE,
    SECRET_PATTERNS,
    WEB_EXTENSIONS,
    WINDOWS_PATH_RE,
    _read_text_sample,
    _risky_file_reason,
    default_report_slug,
    scan_publish_tree,
)

# Stored with each submission (the server only records it); bump when the metadata shape changes.
SCHEMA_VERSION = 1
# Where local copies are saved (git-ignored).
SUBMIT_DIR = Path("submissions")
# Same as the server's nginx client_max_body_size, so a too-large upload fails here clearly.
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
# Built in so a fresh clone can submit with no setup. The token is public because it is
# write-only (it can upload, never read); rotate it together with the server's
# IBL_AGENT_SERVER_INGEST_TOKEN. IBL_AGENT_SUBMIT_URL / IBL_AGENT_SUBMIT_TOKEN override both.
DEFAULT_SUBMIT_URL = "https://ibl-agent-feedback.duckdns.org/api/submissions"
DEFAULT_SUBMIT_TOKEN = "WEL5-8CYikhL9AHCsvQiaX2t9yj_ixriSc5WloF2lIs"


class Submission(NamedTuple):
    """A built submission, as saved locally and ready to send."""

    metadata: dict[str, Any]  # the JSON sent as the form's `metadata` field
    bundle: bytes | None  # the report zip, or None without a report
    warnings: list[str]  # report check warnings, as "path: message"
    saved: Path  # the local JSON copy; the zip is saved next to it


def locate_transcript(transcript_file: Path | None = None) -> tuple[str, Path, str]:
    """Return ``(host, path, session_id)`` of ``transcript_file``, else of the running session.

    A given file can come from anywhere: a Codex log starts with a ``session_meta`` record,
    Claude Code records carry ``sessionId``. The running session's log is named after the id
    its agent puts in the environment (``CODEX_SESSION_ID``, ``CLAUDE_CODE_SESSION_ID``) and
    lives in the agent's folder (``CODEX_HOME``, ``CLAUDE_CONFIG_DIR``; ``~/.codex``,
    ``~/.claude`` by default).
    """
    if transcript_file is not None:
        for record in read_jsonl(transcript_file):
            if record.get("type") == "session_meta":
                payload = record.get("payload") or {}
                return (
                    "codex",
                    transcript_file,
                    str(payload.get("session_id") or payload.get("id", "")),
                )
            if record.get("sessionId"):
                return "claude-code", transcript_file, str(record["sessionId"])
        raise SubmitError(f"{transcript_file} is not a Claude Code or Codex transcript.")

    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    claude_home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    for host, variable, folder, pattern in (
        ("codex", "CODEX_SESSION_ID", codex_home, "sessions/**/rollout-*-{}.jsonl"),
        ("claude-code", "CLAUDE_CODE_SESSION_ID", claude_home, "projects/*/{}.jsonl"),
    ):
        session_id = os.environ.get(variable)
        path = next(folder.glob(pattern.format(session_id)), None) if session_id else None
        if path is not None:
            return host, path, session_id
    raise SubmitError(
        "Could not find a transcript to send. Run the command inside Claude Code or Codex, pass "
        "--transcript-file, or send a report alone with --report and --no-transcript."
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL log, skipping lines that are not JSON objects (a live log may end mid-line)."""
    records = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _text(content: Any) -> str:
    """Flatten a message's content (a string or a list of Claude Code / Codex blocks) into text."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return json.dumps(content, ensure_ascii=False) if content else ""
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif not isinstance(block, dict):
            continue
        elif block.get("type") == "thinking":
            # Most Claude Code thinking blocks keep only an encrypted signature; skip those.
            if block.get("thinking"):
                parts.append(f"[thinking] {block['thinking']}")
        elif isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif block.get("type") == "tool_use":
            arguments = json.dumps(block.get("input", {}), ensure_ascii=False)
            parts.append(f"[tool call: {block.get('name', '?')}] {arguments}")
        elif block.get("type") == "tool_result":
            parts.append(f"[tool result] {_text(block.get('content', ''))}")
    return "\n".join(part for part in parts if part)


def _message(record: dict[str, Any], host: str) -> dict[str, str] | None:
    """Turn one log record into ``{role, text, kind}``, or None for records that are not chat."""
    if host == "claude-code":
        queued = record.get("attachment") or {}
        # A message the user typed while the agent was busy. Task notifications have another
        # commandMode; messages from other sessions are marked isMeta.
        if queued.get("commandMode") == "prompt" and not queued.get("isMeta"):
            return {"role": "user", "text": _text(queued.get("prompt")), "kind": "message"}
        message = record.get("message")
        if not isinstance(message, dict):
            return None
        role = message.get("role", record.get("type", "unknown"))
        return {"role": role, "text": _text(message.get("content")), "kind": "message"}

    payload = record.get("payload")
    kind = payload.get("type") if isinstance(payload, dict) else None
    if kind == "message":
        return {
            "role": payload.get("role", "unknown"),
            "text": _text(payload.get("content")),
            "kind": "message",
        }
    if kind in ("function_call", "custom_tool_call"):
        # Custom tools (such as exec) pass their input as one string instead of arguments.
        arguments = payload.get("arguments", payload.get("input", ""))
        text = f"[tool call: {payload.get('name', '?')}] {arguments}"
        return {"role": "assistant", "text": text, "kind": "tool"}
    if kind in ("function_call_output", "custom_tool_call_output"):
        return {"role": "tool", "text": _text(payload.get("output")), "kind": "tool"}
    if kind == "agent_message":
        # Between the main agent and a subagent; encrypted ones have no readable text.
        text = _text(payload.get("content"))
        header = f"[agent message {payload.get('author')} -> {payload.get('recipient')}]"
        return (
            {"role": "assistant", "text": f"{header} {text}", "kind": "message"} if text else None
        )
    if kind == "reasoning":
        # Codex encrypts its reasoning; only the summary, when there is one, is readable.
        summary = _text(payload.get("summary"))
        return (
            {"role": "assistant", "text": f"[thinking] {summary}", "kind": "thinking"}
            if summary
            else None
        )
    return None  # session_meta, event_msg, turn_context and other bookkeeping


def normalize_records(records: list[dict[str, Any]], host: str) -> list[dict[str, str]]:
    """Clean up a log into the chat messages that are sent, dropping the agent's own records."""
    messages = [_message(record, host) for record in records]
    return [message for message in messages if message and message["text"]]


def _redact(value: Any) -> tuple[Any, int]:
    """Replace secrets in every string inside ``value``; return the result and how many were found.

    Only secrets go: paths, usernames and the rest of the chat are kept as they are.
    """
    if isinstance(value, str):
        count = 0
        for pattern in SECRET_PATTERNS:
            value, found = pattern.subn("[REDACTED-SECRET]", value)
            count += found
        return value, count
    if isinstance(value, list):
        pairs = [_redact(item) for item in value]
        return [item for item, _ in pairs], sum(found for _, found in pairs)
    if isinstance(value, dict):
        pairs = {key: _redact(item) for key, item in value.items()}
        return {key: item for key, (item, _) in pairs.items()}, sum(
            found for _, found in pairs.values()
        )
    return value, 0


def build_report_bundle(path: Path) -> tuple[bytes, str, list[str]]:
    """Zip a project's rendered report; return ``(zip bytes, title, warnings)``.

    ``path`` is the project folder or its ``report/`` folder: the HTML and its web files come
    from ``report/``, the Quarto source from ``<project>/report.qmd`` (or ``report/report.qmd``).
    The files get the GitHub Pages checks (``scan_publish_tree``) and the source is checked for
    secrets and local paths too; any blocker (a data file, a secret, an absolute local path)
    raises ``SubmitError`` and nothing is zipped.
    """
    path = path.expanduser().resolve()
    report_dir = path / "report" if (path / "report" / "report.html").is_file() else path
    if not (report_dir / "report.html").is_file():
        raise SubmitError(f"No rendered report found: expected report.html in {report_dir}.")
    project_dir = report_dir.parent if report_dir.name == "report" else report_dir
    qmd = next(
        (p for p in (project_dir / "report.qmd", report_dir / "report.qmd") if p.is_file()), None
    )
    if qmd is None:
        raise SubmitError(f"No Quarto source found: expected report.qmd in {project_dir}.")

    findings = [(f.severity, f"{f.path}: {f.message}") for f in scan_publish_tree(report_dir)]
    qmd_text = _read_text_sample(qmd) or ""
    if any(pattern.search(qmd_text) for pattern in SECRET_PATTERNS):
        findings.append(("blocker", "report.qmd: credential-like text detected"))
    if WINDOWS_PATH_RE.search(qmd_text) or POSIX_HOME_RE.search(qmd_text):
        findings.append(("blocker", "report.qmd: absolute local path detected"))

    # The zip holds the Quarto source and the web files in report/, all at its root.
    entries = {"report.qmd": qmd}
    for file in sorted(p for p in report_dir.rglob("*") if p.is_file()):
        name = file.relative_to(report_dir).as_posix()
        if name == "report.qmd" or _risky_file_reason(file):  # risky files are blockers above
            continue
        if file.suffix.lower() in WEB_EXTENSIONS:
            entries[name] = file
        else:
            findings.append(("warning", f"{name}: non-web file type skipped"))

    blockers = [text for severity, text in findings if severity == "blocker"]
    if blockers:
        problems = "\n".join(f"  - {text}" for text in blockers)
        raise SubmitError(f"The report can't be submitted until these are fixed:\n{problems}")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, file in entries.items():
            archive.write(file, name)
    front = re.match(r"---\s*\n(.*?)\n---", qmd.read_text(encoding="utf-8", errors="ignore"), re.S)
    title = re.search(r"^title:\s*(.+?)\s*$", front.group(1), re.M) if front else None
    title = title.group(1).strip("'\"") if title else default_report_slug(report_dir)
    return buffer.getvalue(), title, [text for severity, text in findings if severity == "warning"]


def collect_submission(
    message: str,
    *,
    report_path: Path | None = None,
    transcript_file: Path | None = None,
    transcript: bool = True,
) -> Submission:
    """Build a submission and save it to ``submissions/``; nothing is sent.

    ``transcript=False`` sends no chat (a report-only submission).
    """
    host, path, session_id = locate_transcript(transcript_file) if transcript else ("", None, "")
    bundle, title, warnings = build_report_bundle(report_path) if report_path else (None, "", [])
    # Redact after cleaning up the log, so the count covers only what is sent.
    chat = normalize_records(read_jsonl(path), host) if path else []
    redacted, secrets = _redact({"message": message, "transcript": chat})
    try:  # which version of the agent made the submission
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    try:
        agent_version = version("ibl-ai-agent")
    except PackageNotFoundError:
        agent_version = None
    now = datetime.now(timezone.utc)

    metadata = {
        "schema_version": SCHEMA_VERSION,
        "submission_id": f"{now:%Y%m%dT%H%M%SZ}-{os.urandom(4).hex()}",
        "created_at": f"{now:%Y-%m-%dT%H:%M:%SZ}",
        "title": title,
        "message": redacted["message"],
        "host": host,
        "session_id": session_id,
        "agent": {"ibl_ai_agent_version": agent_version, "repo_commit": commit},
        "environment": {"os": platform.system().lower(), "python": platform.python_version()},
        "transcript": redacted["transcript"],
        "redaction": {"counts": {"secrets": secrets}},
    }
    SUBMIT_DIR.mkdir(parents=True, exist_ok=True)
    saved = SUBMIT_DIR / f"{metadata['submission_id']}.json"
    saved.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    if bundle is not None:
        saved.with_suffix(".zip").write_bytes(bundle)
    return Submission(metadata, bundle, warnings, saved)


def post_submission(metadata: dict[str, Any], bundle: bytes | None) -> dict[str, Any]:
    """Send a submission to the server (multipart, bearer token) and return its JSON reply."""
    url = os.environ.get("IBL_AGENT_SUBMIT_URL") or DEFAULT_SUBMIT_URL
    token = os.environ.get("IBL_AGENT_SUBMIT_TOKEN") or DEFAULT_SUBMIT_TOKEN
    body = json.dumps(metadata).encode("utf-8")
    size = len(body) + len(bundle or b"")
    if size > MAX_UPLOAD_BYTES:
        raise SubmitError(
            f"Submission is {size:,} bytes, which exceeds the {MAX_UPLOAD_BYTES:,} byte limit. "
            "The local copy was still saved."
        )
    # No file name for metadata, so the server reads it as a form field.
    files = {"metadata": (None, body, "application/json")}
    if bundle is not None:
        files["bundle"] = ("report.zip", bundle, "application/zip")
    try:
        response = requests.post(
            url, files=files, headers={"Authorization": f"Bearer {token}"}, timeout=60
        )
    except requests.RequestException as exc:
        raise SubmitError(f"Could not reach the submission server at {url}: {exc}") from exc
    if not response.ok:
        raise SubmitError(
            f"Server rejected the submission (HTTP {response.status_code}): {response.text}"
        )
    return response.json()
