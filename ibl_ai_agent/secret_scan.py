"""Best-effort secret detection and redaction, shared by GitHub Pages publishing and ``/ibl-submit``.

No pattern list finds every secret, so a clean result does not mean the text is safe to share.
"""

from __future__ import annotations

import re

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
