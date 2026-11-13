"""Build, preview and send a submission for ``ibl-ai-agent submit`` (the ``/ibl-submit`` skill).

A submission is the user's message and, only if they ask, this session's chat and the project's
rendered report (and ``report.qmd``), with secrets redacted, plus a few details about the agent
and system. It is first saved as a draft for the user to read; sending it is a separate step that
sends that draft as it is. Printing belongs to ``ibl_ai_agent/commands/submit_commands.py``.
"""

from __future__ import annotations

from base64 import b64encode
from datetime import datetime, timezone
from html import escape
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, NamedTuple
import hashlib
import io
import json
import mimetypes
import os
import platform
import subprocess
import zipfile

import requests

from ibl_ai_agent.errors import SubmitError
from ibl_ai_agent.report_publish import WEB_EXTENSIONS
from ibl_ai_agent.secret_scan import (
    POSIX_HOME_RE,
    SCANNED_EXTENSIONS,
    SECRET_PATTERNS,
    WINDOWS_PATH_RE,
    read_text_sample,
    redact,
    risky_file_reason,
    scan_publish_tree,
)

# Stored with each submission (the server only records it); bump when the metadata shape changes.
SCHEMA_VERSION = 1
# Where drafts are saved (git-ignored).
SUBMIT_DIR = Path("submissions")
# Same as the server's nginx client_max_body_size, so a too-large upload fails here clearly.
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
# Built in so a fresh clone can submit with no setup. The token is public because it is
# write-only (it can upload, never read); rotate it together with the server's
# IBL_AGENT_SERVER_INGEST_TOKEN. IBL_AGENT_SUBMIT_URL / IBL_AGENT_SUBMIT_TOKEN override both.
DEFAULT_SUBMIT_URL = "https://ibl-agent-feedback.duckdns.org/api/submissions"
DEFAULT_SUBMIT_TOKEN = "WEL5-8CYikhL9AHCsvQiaX2t9yj_ixriSc5WloF2lIs"

# Shown with every draft, so the user knows who receives it before deciding to send it.
PRIVACY_NOTICE = (
    "The IBL AI Agent server stores submissions privately: people with an admin account on the "
    "server can read them, and they are kept until a maintainer deletes them. The server also "
    "records your IP address and the size of the upload. Secrets such as tokens and passwords "
    "are redacted on a best effort basis: secrets_redacted=0 does not mean the submission is "
    "safe to send, so read the draft before sending it."
)
REPORT_NOTICE = (
    "By default the report stays private: it is stored in the IBL AI Agent server's database to "
    "build the agent's memory. It is published on https://iblagent.iblcore.org only if you "
    "agreed to publication."
)


class Submission(NamedTuple):
    """A built submission, as saved in its draft."""

    metadata: dict[str, Any]  # the JSON sent as the form's `metadata` field
    saved: Path  # the draft; its report zip and SHA-256 are saved next to it
    warnings: list[str]  # report check warnings, as "path: message"


def submit_url() -> str:
    """The server submissions are sent to."""
    return os.environ.get("IBL_AGENT_SUBMIT_URL") or DEFAULT_SUBMIT_URL


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
        "Could not find a transcript to send. Run the command inside Claude Code or Codex, or pass "
        "--transcript-file."
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


def build_report_bundle(path: Path, *, include_source: bool) -> tuple[bytes, list[str]]:
    """Zip the web files in a project's ``report/`` (and ``report.qmd`` with ``include_source``).

    Returns ``(zip bytes, warnings)``. Any blocker from the GitHub Pages checks, or a secret or
    local path in ``report.qmd``, raises ``SubmitError`` and nothing is zipped.
    """
    path = path.expanduser().resolve()
    report_dir = path / "report" if (path / "report" / "report.html").is_file() else path
    if not (report_dir / "report.html").is_file():
        raise SubmitError(f"No rendered report found: expected report.html in {report_dir}.")
    project_dir = report_dir.parent if report_dir.name == "report" else report_dir

    findings = [(f.severity, f"{f.path}: {f.message}") for f in scan_publish_tree(report_dir)]
    # The zip holds the web files in report/ (and the Quarto source if asked), all at its root.
    entries = {}
    if include_source:
        qmd = next(
            (p for p in (project_dir / "report.qmd", report_dir / "report.qmd") if p.is_file()), None
        )
        if qmd is None:
            raise SubmitError(f"No Quarto source found: expected report.qmd in {project_dir}.")
        qmd_text = read_text_sample(qmd)
        if qmd_text is None:
            findings.append(("blocker", "report.qmd: could not be read to check for secrets"))
            qmd_text = ""
        if any(pattern.search(qmd_text) for pattern in SECRET_PATTERNS):
            findings.append(("blocker", "report.qmd: credential-like text detected"))
        if WINDOWS_PATH_RE.search(qmd_text) or POSIX_HOME_RE.search(qmd_text):
            findings.append(("blocker", "report.qmd: absolute local path detected"))
        entries["report.qmd"] = qmd
    for file in sorted(p for p in report_dir.rglob("*") if p.is_file()):
        name = file.relative_to(report_dir).as_posix()
        if name == "report.qmd" or risky_file_reason(file):  # risky files are blockers above
            continue
        if file.suffix.lower() not in WEB_EXTENSIONS:
            findings.append(("warning", f"{name}: non-web file type skipped"))
            continue
        entries[name] = file
        if file.suffix.lower() not in SCANNED_EXTENSIONS:
            findings.append(("warning", f"{name}: not checked for secrets"))

    blockers = [text for severity, text in findings if severity == "blocker"]
    if blockers:
        problems = "\n".join(f"  - {text}" for text in blockers)
        raise SubmitError(f"The report can't be submitted until these are fixed:\n{problems}")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, file in entries.items():
            archive.write(file, name)
    return buffer.getvalue(), [text for severity, text in findings if severity == "warning"]


def collect_submission(
    message: str,
    *,
    transcript: bool = False,
    transcript_file: Path | None = None,
    messages: tuple[int, int] | None = None,
    report_path: Path | None = None,
    report_source: bool = False,
    publication_consent: bool = False,
) -> Submission:
    """Build a submission from only what is asked for and save it as a draft; nothing is sent.

    ``messages=(first, last)`` keeps only those chat messages, counted from 1.
    """
    bundle, warnings = (
        build_report_bundle(report_path, include_source=report_source) if report_path else (None, [])
    )
    redacted, secrets = redact(message)
    extra: dict[str, Any] = {}
    if transcript:
        host, path, session_id = locate_transcript(transcript_file)
        chat = normalize_records(read_jsonl(path), host)
        if messages:
            first, last = messages
            if not 1 <= first <= last <= len(chat):
                raise SubmitError(f"--messages must be a range within 1-{len(chat)}.")
            chat = chat[first - 1 : last]
            extra["transcript_range"] = [first, last]
        # Redacted after the excerpt is taken, so the count covers only what is sent.
        texts = [redact(item["text"]) for item in chat]
        chat = [{**item, "text": text} for item, (text, _) in zip(chat, texts)]
        secrets += sum(found for _, found in texts)
        extra |= {"host": host, "session_id": session_id, "transcript": chat}
    if bundle is not None:
        extra["publication_consent"] = publication_consent
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
        "message": redacted,
        "agent": {"ibl_ai_agent_version": agent_version, "repo_commit": commit},
        "environment": {"os": platform.system().lower(), "python": platform.python_version()},
        **extra,
        "redaction": {"counts": {"secrets": secrets}},
    }
    SUBMIT_DIR.mkdir(parents=True, exist_ok=True)
    saved = SUBMIT_DIR / f"{metadata['submission_id']}.json"
    body = json.dumps(metadata, indent=2, ensure_ascii=False).encode("utf-8")
    saved.write_bytes(body)
    if bundle is not None:
        saved.with_suffix(".zip").write_bytes(bundle)
    # Lets post_submission refuse a draft changed after the user read it.
    digest = hashlib.sha256(body + (bundle or b"")).hexdigest()
    saved.with_suffix(".sha256").write_text(digest, encoding="utf-8")
    return Submission(metadata, saved, warnings)


def write_preview(saved: Path) -> Path:
    """Write ``submissions/<id>.html`` showing everything in the saved draft; return its path.

    No scripts, and the Content-Security-Policy blocks loading anything, so opening it neither
    runs what the attachments contain nor contacts a server.
    """
    metadata = json.loads(saved.read_text(encoding="utf-8"))
    parts = [
        f"<p>Draft: nothing has been sent. Destination: {escape(submit_url())}</p>",
        f"<p>{escape(PRIVACY_NOTICE)}</p>",
        f"<h2>Message</h2><pre>{escape(metadata['message'])}</pre>",
    ]
    if "transcript" in metadata:
        first = metadata.get("transcript_range", [1])[0]
        parts.append(
            f"<h2>Chat: {len(metadata['transcript'])} messages</h2>"
            "<p>Includes tool calls, tool outputs and readable reasoning.</p>"
        )
        parts += [
            f"<h3>{n}. {escape(item['role'])} ({escape(item['kind'])})</h3><pre>{escape(item['text'])}</pre>"
            for n, item in enumerate(metadata["transcript"], first)
        ]
    if "publication_consent" in metadata:
        agreed = "agreed" if metadata["publication_consent"] else "did not agree"
        parts.append(f"<h2>Report</h2><p>{escape(REPORT_NOTICE)} You {agreed} to publication.</p>")
        with zipfile.ZipFile(saved.with_suffix(".zip")) as archive:
            for info in archive.infolist():
                data, name = archive.read(info), info.filename
                checked = name == "report.qmd" or Path(name).suffix.lower() in SCANNED_EXTENSIONS
                kind = mimetypes.guess_type(name)[0] or ""
                parts.append(f"<h3>{escape(name)}: {info.file_size:,} bytes, {'' if checked else 'not '}checked for secrets</h3>")
                if checked:
                    parts.append(f"<details><summary>Contents</summary><pre>{escape(data.decode('utf-8', 'replace'))}</pre></details>")
                elif kind.startswith("image/"):
                    parts.append(f'<img src="data:{kind};base64,{b64encode(data).decode()}">')
    other = {key: value for key, value in metadata.items() if key not in ("message", "transcript")}
    parts.append(
        f"<h2>Also sent</h2><pre>{escape(json.dumps(other, indent=2, ensure_ascii=False))}</pre>"
        f"<p>secrets_redacted={metadata['redaction']['counts']['secrets']}: secrets are redacted "
        "on a best effort basis, and files marked “not checked” were not searched for them.</p>"
    )
    preview = saved.with_suffix(".html")
    preview.write_text(
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'; img-src data:\">"
        "<style>body{font-family:sans-serif;max-width:60rem;margin:auto}pre{white-space:pre-wrap}</style>"
        "</head><body>" + "".join(parts) + "</body></html>",
        encoding="utf-8",
    )
    return preview


def post_submission(saved: Path) -> dict[str, Any]:
    """Send the draft ``saved`` as it is (multipart, bearer token) and return the server's reply.

    A draft whose JSON or zip changed after it was built, or a file ``collect_submission`` did
    not save, is refused: the user must build a new draft and read it before it is sent.
    """
    body = saved.read_bytes()
    bundle_file = saved.with_suffix(".zip")
    bundle = bundle_file.read_bytes() if bundle_file.is_file() else None
    digest = saved.with_suffix(".sha256")
    expected = hashlib.sha256(body + (bundle or b"")).hexdigest()
    if not digest.is_file() or digest.read_text(encoding="utf-8") != expected:
        raise SubmitError(
            f"{saved} changed after it was built, or is not a draft. Build a new draft and read "
            "it before sending."
        )
    size = len(body) + len(bundle or b"")
    if size > MAX_UPLOAD_BYTES:
        raise SubmitError(
            f"Submission is {size:,} bytes, which exceeds the {MAX_UPLOAD_BYTES:,} byte limit."
        )
    url = submit_url()
    token = os.environ.get("IBL_AGENT_SUBMIT_TOKEN") or DEFAULT_SUBMIT_TOKEN
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
