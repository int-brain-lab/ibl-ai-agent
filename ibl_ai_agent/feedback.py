"""Collect, redact, and ship interactive-session feedback.

This module powers the ``ibl-ai-agent feedback`` command used by the
``/ibl-feedback`` skill trigger. When a user invokes ``/ibl-feedback <text>``
in their coding agent, we:

1. locate *this* session's transcript file written by the host coding agent
   (Claude Code or Codex, auto-detected);
2. normalize it into a host-agnostic list of chat messages so the server can
   replay it the way the user saw it;
3. redact secrets and local filesystem paths *before anything leaves the
   machine*, reusing the scanner already trusted for report publishing;
4. save a local copy the user can inspect; and
5. optionally POST the JSON payload to a feedback server using a shared
   bearer token.

The functions here do no printing or prompting; that belongs to the command
layer (``ibl_ai_agent/commands/feedback_commands.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import logging
import os
import platform
import re
import socket
import subprocess
import urllib.error
import urllib.request

from ibl_ai_agent.errors import FeedbackError
from ibl_ai_agent.local_config import load_local_config

# Reuse the exact patterns trusted for report publishing so "what counts as
# sensitive" has a single definition across the project.
from ibl_ai_agent.report_publish import (
    POSIX_HOME_RE,
    SECRET_PATTERNS,
    WINDOWS_PATH_RE,
)

logger = logging.getLogger(__name__)

# Bump when the payload shape changes so the server can branch on it.
SCHEMA_VERSION = 1

#: Where local copies of feedback payloads are written (git-ignored).
DEFAULT_FEEDBACK_DIR = Path("feedback")

#: Replacement strings used when redacting.
SECRET_PLACEHOLDER = "[REDACTED-SECRET]"
PATH_PLACEHOLDER = "[REDACTED-PATH]"
IDENTIFIER_PLACEHOLDER = "[REDACTED-ID]"

#: Hard cap on the JSON we are willing to send. Keep in sync with the server's
#: nginx ``client_max_body_size`` so the client fails early with a clear error
#: rather than getting a 413 from the proxy.
MAX_PAYLOAD_BYTES = 30 * 1024 * 1024

#: Environment variables that configure the destination server.
ENV_FEEDBACK_URL = "IBL_AGENT_FEEDBACK_URL"
ENV_FEEDBACK_TOKEN = "IBL_AGENT_FEEDBACK_TOKEN"


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TranscriptSource:
    """A located session transcript file and the host that produced it."""

    host: str  # "claude-code" | "codex" | "unknown"
    path: Path
    mtime: float


@dataclass(frozen=True)
class FeedbackResult:
    """Outcome of building (and optionally saving) a feedback payload."""

    source: TranscriptSource
    payload: dict[str, Any]
    local_path: Path | None
    redaction: dict[str, int]


# ---------------------------------------------------------------------------
# Transcript location
# ---------------------------------------------------------------------------
def _claude_project_dir(cwd: Path) -> Path:
    """Return Claude Code's session directory for a working directory.

    Claude Code stores transcripts under ``~/.claude/projects/<encoded-cwd>/``
    where the encoding replaces every non-alphanumeric character in the
    absolute working-directory path with a hyphen (e.g. ``/Users/me/my_proj``
    -> ``-Users-me-my-proj``).
    """
    encoded = re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    return Path.home() / ".claude" / "projects" / encoded


def _codex_sessions_root() -> Path:
    """Return the root directory under which Codex writes session rollouts."""
    return Path.home() / ".codex" / "sessions"


def _newest_file(paths: list[Path]) -> Path | None:
    """Return the most-recently-modified file from ``paths`` (or None)."""
    files = [p for p in paths if p.is_file()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def _guess_host_from_path(path: Path) -> str:
    """Best-effort host detection from a transcript path's components."""
    parts = {part.lower() for part in path.parts}
    if ".codex" in parts or "codex" in parts:
        return "codex"
    if ".claude" in parts or "claude" in parts:
        return "claude-code"
    return "unknown"


def locate_transcript(
    *,
    host: str = "auto",
    cwd: Path | None = None,
    session_file: Path | None = None,
) -> TranscriptSource:
    """Locate the current coding-agent session transcript.

    Parameters
    ----------
    host:
        ``"auto"`` (default) checks both hosts and returns whichever has the
        most recently written transcript. ``"claude"``/``"claude-code"`` or
        ``"codex"`` restrict the search to one host.
    cwd:
        Working directory used to compute Claude Code's encoded project dir.
        Defaults to the current directory.
    session_file:
        Explicit transcript path that overrides auto-detection.

    Returns
    -------
    TranscriptSource

    Raises
    ------
    FeedbackError
        If no transcript can be found.

    Notes
    -----
    The live session's file is the one currently being written, so "most
    recently modified" reliably identifies the active session.
    """
    if session_file is not None:
        path = session_file.expanduser()
        if not path.is_file():
            raise FeedbackError(f"--session-file not found: {path}")
        detected = host if host not in ("auto",) else _guess_host_from_path(path)
        return TranscriptSource(host=detected, path=path, mtime=path.stat().st_mtime)

    cwd = (cwd or Path.cwd()).resolve()
    candidates: list[TranscriptSource] = []

    if host in ("auto", "claude", "claude-code"):
        claude_dir = _claude_project_dir(cwd)
        claude_path = _newest_file(list(claude_dir.glob("*.jsonl"))) if claude_dir.is_dir() else None
        if claude_path is not None:
            candidates.append(
                TranscriptSource("claude-code", claude_path, claude_path.stat().st_mtime)
            )

    if host in ("auto", "codex"):
        codex_root = _codex_sessions_root()
        codex_path = (
            _newest_file(list(codex_root.rglob("rollout-*.jsonl"))) if codex_root.is_dir() else None
        )
        if codex_path is not None:
            candidates.append(TranscriptSource("codex", codex_path, codex_path.stat().st_mtime))

    if not candidates:
        raise FeedbackError(
            "Could not find a session transcript. Looked for Claude Code logs under "
            f"{_claude_project_dir(cwd)} and Codex logs under {_codex_sessions_root()}. "
            "Pass --session-file to point at the transcript explicitly."
        )

    return max(candidates, key=lambda s: s.mtime)


# ---------------------------------------------------------------------------
# Transcript parsing / normalization
# ---------------------------------------------------------------------------
def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL transcript into a list of records, skipping bad lines."""
    records: list[dict[str, Any]] = []
    text = path.read_text(encoding="utf-8", errors="ignore")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("Skipping non-JSON transcript line in %s", path.name)
            continue
        if isinstance(obj, dict):
            records.append(obj)
    return records


def _stringify(value: Any) -> str:
    """Render an arbitrary JSON value as readable text."""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False)


def _blocks_to_text(content: Any) -> str:
    """Flatten a message's ``content`` into plain text.

    Handles both plain strings and the structured content-block lists used by
    Claude Code (``text`` / ``tool_use`` / ``tool_result``) and Codex
    (``input_text`` / ``output_text``), which all expose either a ``text`` key
    or a recognizable block ``type``.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return _stringify(content) if content else ""

    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif btype == "tool_use":
            parts.append(
                f"[tool call: {block.get('name', '?')}] "
                f"{json.dumps(block.get('input', {}), ensure_ascii=False)}"
            )
        elif btype == "tool_result":
            parts.append(f"[tool result] {_blocks_to_text(block.get('content', ''))}")
    return "\n".join(p for p in parts if p)


def _record_to_message(rec: dict[str, Any], host: str) -> dict[str, Any] | None:
    """Convert one transcript record into ``{role, text, kind}`` or None."""
    if host == "codex":
        payload = rec.get("payload")
        if not isinstance(payload, dict):
            return None
        ptype = payload.get("type")
        if ptype == "message":
            return {
                "role": payload.get("role", "unknown"),
                "text": _blocks_to_text(payload.get("content")),
                "kind": "message",
            }
        if ptype == "function_call":
            return {
                "role": "assistant",
                "text": f"[tool call: {payload.get('name', '?')}] {payload.get('arguments', '')}",
                "kind": "tool",
            }
        if ptype == "function_call_output":
            return {"role": "tool", "text": _stringify(payload.get("output")), "kind": "tool"}
        return None  # reasoning / session_meta / event_msg are skipped

    # Claude Code (and a generic fallback).
    msg = rec.get("message")
    if isinstance(msg, dict):
        return {
            "role": msg.get("role", rec.get("type", "unknown")),
            "text": _blocks_to_text(msg.get("content")),
            "kind": "message",
        }
    if "role" in rec and "content" in rec:
        return {
            "role": rec.get("role", "unknown"),
            "text": _blocks_to_text(rec.get("content")),
            "kind": "message",
        }
    return None


def normalize_records(records: list[dict[str, Any]], host: str) -> list[dict[str, Any]]:
    """Turn raw transcript records into a host-agnostic chat message list."""
    messages: list[dict[str, Any]] = []
    for rec in records:
        try:
            msg = _record_to_message(rec, host)
        except Exception:  # pragma: no cover - defensive: never crash on odd lines
            logger.debug("Skipping unparseable transcript record", exc_info=True)
            continue
        if msg and msg.get("text"):
            messages.append(msg)
    return messages


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------
class _RedactionAccumulator:
    """Scrub sensitive text while counting what was removed.

    One accumulator is used for an entire payload so the reported counts cover
    the message, the normalized transcript, and the raw transcript together.
    """

    def __init__(self) -> None:
        self.secrets = 0
        self.paths = 0
        self.identifiers = 0
        # Literal identifiers from this machine, redacted with word boundaries
        # to avoid mangling unrelated words. Short values are ignored.
        self._literals: list[tuple[str, str]] = []
        home = str(Path.home())
        if home and len(home) >= 3:
            self._literals.append((home, PATH_PLACEHOLDER))
        for name in (os.environ.get("USER"), os.environ.get("USERNAME"), socket.gethostname()):
            if name and len(name) >= 3:
                self._literals.append((name, IDENTIFIER_PLACEHOLDER))

    def text(self, value: str) -> str:
        """Return ``value`` with secrets, local paths, and identifiers removed."""
        if not isinstance(value, str) or not value:
            return value
        result = value
        for pattern in SECRET_PATTERNS:
            result, n = pattern.subn(SECRET_PLACEHOLDER, result)
            self.secrets += n
        for pattern in (WINDOWS_PATH_RE, POSIX_HOME_RE):
            result, n = pattern.subn(PATH_PLACEHOLDER, result)
            self.paths += n
        for literal, placeholder in self._literals:
            count = len(re.findall(re.escape(literal), result))
            if count:
                result = result.replace(literal, placeholder)
                if placeholder == PATH_PLACEHOLDER:
                    self.paths += count
                else:
                    self.identifiers += count
        return result

    def obj(self, value: Any) -> Any:
        """Recursively redact all strings inside a JSON-like structure."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.obj(item) for item in value]
        if isinstance(value, dict):
            return {key: self.obj(item) for key, item in value.items()}
        return value

    def as_dict(self) -> dict[str, int]:
        """Return the redaction counts for inclusion in the payload."""
        return {"secrets": self.secrets, "paths": self.paths, "identifiers": self.identifiers}


# ---------------------------------------------------------------------------
# Metadata + payload assembly
# ---------------------------------------------------------------------------
def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_feedback_id() -> str:
    """Return a sortable, unique feedback id (timestamp + short random tail)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{os.urandom(4).hex()}"


def _git(repo_root: Path, *args: str) -> str | None:
    """Run a git command, returning stripped stdout or None on any failure."""
    try:
        proc = subprocess.run(  # noqa: S603
            ["git", *args],
            cwd=str(repo_root),
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _agent_metadata(repo_root: Path) -> dict[str, Any]:
    """Collect non-identifying info about the agent build (best effort)."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            pkg_version = version("ibl-ai-agent")
        except PackageNotFoundError:
            pkg_version = None
    except Exception:  # pragma: no cover - importlib should always be present
        pkg_version = None

    commit = _git(repo_root, "rev-parse", "--short", "HEAD")
    dirty_out = _git(repo_root, "status", "--porcelain")
    return {
        "ibl_ai_agent_version": pkg_version,
        "repo_commit": commit,
        "repo_dirty": bool(dirty_out) if dirty_out is not None else None,
    }


def _environment_metadata() -> dict[str, Any]:
    """Collect coarse, non-identifying environment info."""
    return {"os": platform.system().lower(), "python": platform.python_version()}


def build_payload(
    message: str,
    *,
    source: TranscriptSource,
    repo_root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Build a redacted, JSON-serializable feedback payload.

    Returns the payload dict and the redaction counts.
    """
    repo_root = (repo_root or Path.cwd()).resolve()
    raw_records = read_jsonl(source.path)
    normalized = normalize_records(raw_records, source.host)

    accumulator = _RedactionAccumulator()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "feedback_id": _make_feedback_id(),
        "created_at": _utcnow_iso(),
        "message": accumulator.text(message),
        "host": source.host,
        "agent": _agent_metadata(repo_root),
        "environment": _environment_metadata(),
        "transcript": accumulator.obj(normalized),
        "transcript_raw": accumulator.obj(raw_records),
    }
    # The redaction summary itself is computed after scrubbing everything above.
    counts = accumulator.as_dict()
    payload["redaction"] = {"applied": True, "counts": counts}
    return payload, counts


def save_payload(payload: dict[str, Any], *, feedback_dir: Path = DEFAULT_FEEDBACK_DIR) -> Path:
    """Write a feedback payload to ``feedback_dir`` and return its path."""
    feedback_dir.mkdir(parents=True, exist_ok=True)
    path = feedback_dir / f"{payload['feedback_id']}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def collect_feedback(
    message: str,
    *,
    host: str = "auto",
    session_file: Path | None = None,
    cwd: Path | None = None,
    repo_root: Path | None = None,
    feedback_dir: Path = DEFAULT_FEEDBACK_DIR,
    save: bool = True,
) -> FeedbackResult:
    """Locate the transcript, build a redacted payload, and optionally save it.

    This performs no network I/O; sending is a separate explicit step
    (:func:`post_payload`) so the payload can always be inspected first.
    """
    source = locate_transcript(host=host, cwd=cwd, session_file=session_file)
    payload, counts = build_payload(message, source=source, repo_root=repo_root)
    local_path = save_payload(payload, feedback_dir=feedback_dir) if save else None
    return FeedbackResult(source=source, payload=payload, local_path=local_path, redaction=counts)


# ---------------------------------------------------------------------------
# Configuration + sending
# ---------------------------------------------------------------------------
def resolve_feedback_config(root: Path | None = None) -> tuple[str | None, str | None]:
    """Resolve the feedback server URL and token.

    Environment variables take precedence over the (git-ignored)
    ``ibl-agent.local.yaml`` so a machine can override the checked-in defaults.
    """
    url = os.environ.get(ENV_FEEDBACK_URL)
    token = os.environ.get(ENV_FEEDBACK_TOKEN)
    if url and token:
        return url, token

    config = load_local_config(root)
    if config is not None:
        url = url or config.feedback_url
        token = token or config.feedback_token
    return url, token


def post_payload(
    payload: dict[str, Any],
    *,
    url: str,
    token: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """POST a feedback payload to the server with a bearer token.

    Uses only the standard library so no extra dependency is required.

    Raises
    ------
    FeedbackError
        If the payload is too large, the server rejects it, or it is
        unreachable.
    """
    data = json.dumps(payload).encode("utf-8")
    if len(data) > MAX_PAYLOAD_BYTES:
        raise FeedbackError(
            f"Feedback payload is {len(data):,} bytes, which exceeds the "
            f"{MAX_PAYLOAD_BYTES:,} byte limit. The local copy was still saved."
        )

    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = response.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore") if exc.fp else exc.reason
        raise FeedbackError(f"Server rejected feedback (HTTP {exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise FeedbackError(f"Could not reach feedback server at {url}: {exc.reason}") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw_response": body}
