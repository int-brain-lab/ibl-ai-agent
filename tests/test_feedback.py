from __future__ import annotations

from pathlib import Path
import json
import os
import re

import pytest
from typer.testing import CliRunner

from ibl_ai_agent.cli import app
from ibl_ai_agent.errors import FeedbackError
from ibl_ai_agent.feedback import (
    collect_feedback,
    locate_transcript,
    normalize_records,
    resolve_feedback_config,
)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Transcript location
# ---------------------------------------------------------------------------
def test_locate_transcript_session_file_override(tmp_path: Path) -> None:
    transcript = tmp_path / "my.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")

    source = locate_transcript(session_file=transcript)

    assert source.path == transcript


def test_locate_transcript_auto_prefers_newest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    cwd = (tmp_path / "proj").resolve()
    cwd.mkdir()

    encoded = re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    claude_dir = home / ".claude" / "projects" / encoded
    claude_dir.mkdir(parents=True)
    claude_file = claude_dir / "session.jsonl"
    claude_file.write_text("{}\n", encoding="utf-8")

    codex_dir = home / ".codex" / "sessions" / "2026" / "06" / "29"
    codex_dir.mkdir(parents=True)
    codex_file = codex_dir / "rollout-1.jsonl"
    codex_file.write_text("{}\n", encoding="utf-8")

    # Codex is newer -> chosen.
    os.utime(claude_file, (1000, 1000))
    os.utime(codex_file, (2000, 2000))
    assert locate_transcript(host="auto", cwd=cwd).host == "codex"

    # Make Claude newer -> chosen.
    os.utime(claude_file, (3000, 3000))
    chosen = locate_transcript(host="auto", cwd=cwd)
    assert chosen.host == "claude-code"
    assert chosen.path == claude_file


def test_locate_transcript_missing_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)

    with pytest.raises(FeedbackError):
        locate_transcript(host="auto", cwd=tmp_path / "proj")


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------
def test_normalize_claude_records() -> None:
    records = [
        {"type": "user", "message": {"role": "user", "content": "hello"}},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                ],
            },
        },
        {"type": "summary", "summary": "skip me"},
    ]

    messages = normalize_records(records, "claude-code")

    assert messages[0] == {"role": "user", "text": "hello", "kind": "message"}
    assert messages[1]["role"] == "assistant"
    assert "hi" in messages[1]["text"]
    assert "tool call: Bash" in messages[1]["text"]
    assert len(messages) == 2


def test_normalize_codex_records() -> None:
    records = [
        {"type": "session_meta", "payload": {"cwd": "/x"}},
        {"payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]}},
        {"payload": {"type": "function_call", "name": "shell", "arguments": '{"cmd":"ls"}'}},
        {"payload": {"type": "function_call_output", "output": "file1"}},
    ]

    messages = normalize_records(records, "codex")

    assert messages[0]["text"] == "hello"
    assert "tool call: shell" in messages[1]["text"]
    assert messages[2]["text"] == "file1"
    assert len(messages) == 3


# ---------------------------------------------------------------------------
# Redaction + payload assembly
# ---------------------------------------------------------------------------
def test_collect_feedback_redacts_and_saves(tmp_path: Path) -> None:
    transcript = tmp_path / "rollout-x.jsonl"
    _write_jsonl(
        transcript,
        [
            {
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "token sk-abcdefghij1234567890 path /Users/secret/file",
                        }
                    ],
                }
            },
            {"payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "ok"}]}},
        ],
    )
    feedback_dir = tmp_path / "feedback"

    result = collect_feedback(
        "please fix /Users/me/x",
        host="codex",
        session_file=transcript,
        feedback_dir=feedback_dir,
        repo_root=tmp_path,
    )

    assert result.local_path is not None and result.local_path.exists()
    saved = json.loads(result.local_path.read_text(encoding="utf-8"))
    blob = json.dumps(saved)

    # Secrets and paths must be gone from the entire payload (message + transcript + raw).
    assert "sk-abcdefghij1234567890" not in blob
    assert "/Users/secret/file" not in blob
    assert "/Users/me/x" not in blob
    assert result.redaction["secrets"] >= 1
    assert result.redaction["paths"] >= 1

    assert saved["schema_version"] == 1
    assert saved["host"] == "codex"
    assert saved["redaction"]["applied"] is True
    assert len(saved["transcript"]) == 2


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------
def test_resolve_feedback_config_prefers_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IBL_AGENT_FEEDBACK_URL", "https://env.example/api")
    monkeypatch.setenv("IBL_AGENT_FEEDBACK_TOKEN", "envtoken")

    url, token = resolve_feedback_config(tmp_path)

    assert url == "https://env.example/api"
    assert token == "envtoken"


def test_resolve_feedback_config_from_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IBL_AGENT_FEEDBACK_URL", raising=False)
    monkeypatch.delenv("IBL_AGENT_FEEDBACK_TOKEN", raising=False)
    (tmp_path / "ibl-agent.local.yaml").write_text(
        "feedback_url: https://yaml.example/api\nfeedback_token: yamltoken\n", encoding="utf-8"
    )

    url, token = resolve_feedback_config(tmp_path)

    assert url == "https://yaml.example/api"
    assert token == "yamltoken"


# ---------------------------------------------------------------------------
# CLI wiring (dry-run makes no network call)
# ---------------------------------------------------------------------------
def test_feedback_cli_dry_run(tmp_path: Path) -> None:
    transcript = tmp_path / "rollout-x.jsonl"
    _write_jsonl(
        transcript,
        [{"payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}}],
    )
    feedback_dir = tmp_path / "fb"

    result = CliRunner().invoke(
        app,
        [
            "feedback",
            "-m",
            "test message",
            "--dry-run",
            "--host",
            "codex",
            "--session-file",
            str(transcript),
            "--feedback-dir",
            str(feedback_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "mode=dry_run" in result.output
    assert list(feedback_dir.glob("*.json"))
