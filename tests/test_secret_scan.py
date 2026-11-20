from __future__ import annotations

import json

import pytest

from ibl_ai_agent.secret_scan import redact

# A Codex exec call (JavaScript) running a shell command with JSON in it: the JSON is escaped twice.
EXEC_WITH_JSON = r'''await tools.exec_command({cmd: "curl -d \"{\\\"password\\\": \\\"correct horse battery staple\\\"}\" https://example.org"})'''

# Built from pieces, so the repository holds no token-shaped strings for secret scanners to flag.
FAKE_TOKENS = [
    "AKIA" + "A" * 16,  # AWS
    "xoxb-" + "1" * 12 + "-" + "a" * 16,  # Slack
    "AIza" + "a" * 35,  # Google
    "sk_live_" + "a" * 24,  # Stripe
    "hf_" + "a" * 34,  # Hugging Face
    "glpat-" + "a" * 20,  # GitLab
    "npm_" + "a" * 36,  # npm
    "eyJ" + "a" * 10 + ".eyJ" + "a" * 10 + "." + "a" * 10,  # JWT
]


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ('{"api_key": "abcd1234efgh5678"}', "abcd1234efgh5678"),
        # JSON inside a tool call's arguments has its quotes escaped.
        (json.dumps({"command": "curl -d '{\"password\": \"hunter2hunter2\"}'"}), "hunter2hunter2"),
        # A quoted value goes whole: spaces, apostrophes and escaped quotes included.
        ('{"password": "correct horse battery staple"}', "battery staple"),
        ('{"password": " correct horse battery"}', "horse battery"),
        ('{"password": "don\'t tell anyone"}', "tell anyone"),
        (r'{"password": "say \"open sesame\" twice"}', "twice"),
        (json.dumps({"command": r"""curl -d '{"password": "say \"open sesame\" twice"}'"""}), "twice"),
        (EXEC_WITH_JSON, "battery staple"),
        (json.dumps({"code": EXEC_WITH_JSON}), "battery staple"),  # a js call: escaped three times
        ('{"password": "abc123"}', "abc123"),
        ('SECRET_KEY = "django-insecure-abc123"', "django-insecure-abc123"),
        ("AWS_SECRET_ACCESS_KEY=abcd1234efgh5678", "abcd1234efgh5678"),
        ('{"private_key": "abcd1234efgh5678"}', "abcd1234efgh5678"),
        ('{"ALYX_PWD": "my alyx pass"}', "alyx pass"),  # ONE's settings
        ('{"passphrase": "correct horse"}', "horse"),
        ("mysql -u root --password hunter2hunter2 db", "hunter2hunter2"),
        ("curl -u me:hunter2hunter2 https://example.org", "hunter2hunter2"),
        ("postgres://admin:hunter2hunter2@db.example.org/x", "hunter2hunter2"),
        ("Authorization: Basic dXNlcjpodW50ZXIy", "dXNlcjpodW50ZXIy"),
        ("Authorization: Token 0123456789abcdef", "0123456789abcdef"),  # Alyx
        *[(f"found {token} in the log", token) for token in FAKE_TOKENS],
        ("curl -H 'Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abcdefgh'", "eyJhbGciOiJIUzI1NiJ9"),
        ("-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkq\n-----END PRIVATE KEY-----", "MIIEvQIBADANBgkq"),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----", "MIIEowIBAAKCAQEA"),
        ("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXkt\n-----END OPENSSH PRIVATE KEY-----", "b3BlbnNzaC1rZXkt"),
        ("cat key.pem\n-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkq", "MIIEvQIBADANBgkq"),  # cut off
    ],
)  # fmt: skip
def test_redact_removes_whole_secrets(text: str, secret: str) -> None:
    redacted, count = redact(text)

    assert secret not in redacted and "PRIVATE KEY" not in redacted
    assert count == 1
