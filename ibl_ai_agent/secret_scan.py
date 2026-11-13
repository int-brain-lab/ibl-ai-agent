"""Best-effort checks for secrets and local paths, shared by GitHub Pages publishing and ``/ibl-submit``.

No pattern list finds every secret, so a clean result does not mean the text is safe to share.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from pathlib import Path
import os
import re
import socket

RISKY_EXTENSIONS = {
    ".csv",
    ".db",
    ".env",
    ".feather",
    ".h5",
    ".hdf5",
    ".ipynb",
    ".jsonl",
    ".log",
    ".npy",
    ".npz",
    ".parquet",
    ".pkl",
    ".pickle",
    ".sqlite",
    ".sqlite3",
    ".tsv",
    ".yaml",
    ".yml",
}

RISKY_FILENAMES = {
    ".env",
    ".env.private",
    "alyx.json",
    "credentials",
    "credentials.json",
    "data_locations.local.yaml",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "known_hosts",
}

WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\(?:Users|Neuropixels Dropbox|Documents and Settings)\\[^<>'\"\s]+")
POSIX_HOME_RE = re.compile(r"/(?:Users|home)/[^<>'\"\s]+")

# The file types whose text is checked; anything else (images, fonts) is not.
SCANNED_EXTENSIONS = {".html", ".htm", ".css", ".js", ".json", ".txt", ".svg"}
MAX_SCAN_BYTES = 50_000_000

SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    # AWS, Slack, Google, Stripe, Hugging Face, GitLab and npm tokens, and JWTs.
    re.compile(
        r"\b(?:(?:AKIA|ASIA)[0-9A-Z]{16}|xox[abprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35}"
        r"|[rs]k_(?:live|test)_[0-9A-Za-z]{16,}|hf_[A-Za-z0-9]{30,}|glpat-[A-Za-z0-9_-]{20,}"
        r"|npm_[A-Za-z0-9]{36}|eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})"
    ),
    # A quoted value (\"escaped\" any number of times) runs to its closing quote, else to a space.
    re.compile(
        r"(?i)(password|passwd|passphrase|_pwd|token|api[_-]?key|private[_-]?key|access[_-]?key"
        r"|secret(?:[_-]?(?:access[_-]?)?key)?)\\*['\"]?\s*[:=]\s*"
        r"(?:(\\*['\"])(?:(?!(?<!\\)\2).){4,}(?<!\\)\2|\\*['\"]?[^'\"\s<]{8,})"
    ),
    re.compile(r"(?i)--(?:password|passwd|token|api[_-]?key|secret)\s+[^\s'\"-]\S{7,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    # Basic and Token (Alyx) authorization headers.
    re.compile(r"(?i)authorization\\*['\"]?\s*:\s*\\*['\"]?(?:basic|token)\s+[\w.~+/=-]{8,}"),
    # user:password in a URL or after curl -u.
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:@'\"]+:[^\s/@'\"]+@"),
    re.compile(r"(?<!\S)(?:-u|--user)\s+[\w.@-]+:[^\s'\"]+"),
    # The whole key; to the end of the text when the END line is cut off.
    re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----"
        r"(?:.*?-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|.*)",
        re.S,
    ),
]


def redact(text: str) -> tuple[str, int]:
    """Replace each secret in ``text`` with ``[REDACTED-SECRET]``; return the text and the count."""
    count = 0
    for pattern in SECRET_PATTERNS:
        text, found = pattern.subn("[REDACTED-SECRET]", text)
        count += found
    return text, count


@dataclass(frozen=True)
class PublishFinding:
    severity: str
    path: str
    message: str


def scan_publish_tree(root: Path) -> list[PublishFinding]:
    findings: list[PublishFinding] = []
    home = str(Path.home())
    username = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    hostname = socket.gethostname()

    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = str(path.relative_to(root)).replace("\\", "/")
        risk = risky_file_reason(path)
        if risk:
            findings.append(PublishFinding("blocker", rel, risk))
        if not path.resolve().is_relative_to(root.resolve()):
            findings.append(PublishFinding("blocker", rel, "links to a file outside the folder"))

        if path.suffix.lower() in SCANNED_EXTENSIONS:
            text = read_text_sample(path)
            if text is None:
                findings.append(PublishFinding("blocker", rel, "could not be read to check for secrets"))
                continue
            # Also the text as shown: Quarto writes quotes as &quot; and wraps code in <span>s.
            visible = unescape(re.sub(r"<[^>]+>", "", text))
            for pattern in SECRET_PATTERNS:
                if pattern.search(text) or pattern.search(visible):
                    findings.append(PublishFinding("blocker", rel, "credential-like text detected"))
                    break
            if WINDOWS_PATH_RE.search(text) or POSIX_HOME_RE.search(text) or (home and home in text):
                findings.append(PublishFinding("blocker", rel, "absolute local path detected"))
            if username and len(username) >= 3 and re.search(rf"(?i)\b{re.escape(username)}\b", text):
                findings.append(PublishFinding("warning", rel, "local username appears in text"))
            if hostname and len(hostname) >= 3 and re.search(rf"(?i)\b{re.escape(hostname)}\b", text):
                findings.append(PublishFinding("warning", rel, "computer name appears in text"))
    return findings


def risky_file_reason(path: Path) -> str | None:
    name = path.name.lower()
    if name in RISKY_FILENAMES:
        return "risky filename is not safe for public upload"
    if path.suffix.lower() in RISKY_EXTENSIONS:
        return "risky file type is not safe for public upload"
    return None


def read_text_sample(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_SCAN_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
