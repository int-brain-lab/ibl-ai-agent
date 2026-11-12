"""Build and send a submission for ``ibl-ai-agent submit`` (the ``/ibl-submit`` skill).

A submission is the user's message (secrets redacted) and a few details about the agent and
system. It is first saved as a draft for the user to read; sending it is a separate step that
sends that draft as it is. Printing belongs to ``ibl_ai_agent/commands/submit_commands.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, NamedTuple
import hashlib
import json
import os
import platform
import subprocess

import requests

from ibl_ai_agent.errors import SubmitError
from ibl_ai_agent.secret_scan import redact

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


class Submission(NamedTuple):
    """A built submission, as saved in its draft."""

    metadata: dict[str, Any]  # the JSON sent as the form's `metadata` field
    saved: Path  # the draft; its SHA-256 is saved next to it


def submit_url() -> str:
    """The server submissions are sent to."""
    return os.environ.get("IBL_AGENT_SUBMIT_URL") or DEFAULT_SUBMIT_URL


def collect_submission(message: str) -> Submission:
    """Build a submission from ``message`` and save it as a draft in ``submissions/``; nothing is sent."""
    redacted, secrets = redact(message)
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
        "redaction": {"counts": {"secrets": secrets}},
    }
    SUBMIT_DIR.mkdir(parents=True, exist_ok=True)
    saved = SUBMIT_DIR / f"{metadata['submission_id']}.json"
    body = json.dumps(metadata, indent=2, ensure_ascii=False).encode("utf-8")
    saved.write_bytes(body)
    # Lets post_submission refuse a draft changed after the user read it.
    saved.with_suffix(".sha256").write_text(hashlib.sha256(body).hexdigest(), encoding="utf-8")
    return Submission(metadata, saved)


def post_submission(saved: Path) -> dict[str, Any]:
    """Send the draft ``saved`` as it is (multipart, bearer token) and return the server's reply.

    A draft changed after it was built, or a file ``collect_submission`` did not save, is refused:
    the user must build a new draft and read it before it is sent.
    """
    body = saved.read_bytes()
    digest = saved.with_suffix(".sha256")
    if not digest.is_file() or digest.read_text(encoding="utf-8") != hashlib.sha256(body).hexdigest():
        raise SubmitError(
            f"{saved} changed after it was built, or is not a draft. Build a new draft and read "
            "it before sending."
        )
    if len(body) > MAX_UPLOAD_BYTES:
        raise SubmitError(
            f"Submission is {len(body):,} bytes, which exceeds the {MAX_UPLOAD_BYTES:,} byte limit."
        )
    url = submit_url()
    token = os.environ.get("IBL_AGENT_SUBMIT_TOKEN") or DEFAULT_SUBMIT_TOKEN
    # No file name for metadata, so the server reads it as a form field.
    files = {"metadata": (None, body, "application/json")}
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
