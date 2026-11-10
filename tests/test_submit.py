from __future__ import annotations

from pathlib import Path
import io
import json
import os
import zipfile

import pytest
import requests
from typer.testing import CliRunner

from ibl_ai_agent import submit
from ibl_ai_agent.cli import app
from ibl_ai_agent.errors import SubmitError
from ibl_ai_agent.submit import (
    DEFAULT_SUBMIT_TOKEN,
    DEFAULT_SUBMIT_URL,
    build_report_bundle,
    collect_submission,
    locate_transcript,
    normalize_records,
    post_submission,
)

CLAUDE_ID = "b2f89d44-3c48-48ba-9c80-11eef8cdf8e8"
CODEX_ID = "01a1b6fa-cc02-7081-8da3-a71fb2efd057"
# Where each agent writes the log of session {} (relative to the home folder).
CLAUDE_LOG = ".claude/projects/-Users-me-proj/{}.jsonl"
CODEX_LOG = ".codex/sessions/2026/11/07/rollout-2026-11-07T17-07-31-{}.jsonl"


@pytest.fixture(autouse=True)
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run in tmp_path with a temporary home, no real session ids or server settings, no network.

    The tests may run inside an agent, whose session id would otherwise be picked up. Local
    copies land in tmp_path/submissions. A test that sends replaces requests.post (_capture_post).
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.chdir(tmp_path)
    for variable in (
        "CLAUDE_CODE_SESSION_ID",
        "CODEX_SESSION_ID",
        "CLAUDE_CONFIG_DIR",
        "CODEX_HOME",
        "IBL_AGENT_SUBMIT_URL",
        "IBL_AGENT_SUBMIT_TOKEN",
    ):
        monkeypatch.delenv(variable, raising=False)

    def no_network(url, **kwargs):
        raise AssertionError(f"a test tried to send to {url}")

    monkeypatch.setattr(requests, "post", no_network)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _codex_session(monkeypatch: pytest.MonkeyPatch, text: str = "hi") -> Path:
    """Write a one-message Codex log, mark it as the running session, and return its path."""
    log = Path.home() / CODEX_LOG.format(CODEX_ID)
    content = [{"type": "input_text", "text": text}]
    _write_jsonl(log, [{"payload": {"type": "message", "role": "user", "content": content}}])
    monkeypatch.setenv("CODEX_SESSION_ID", CODEX_ID)
    return log


def _make_project(tmp_path: Path, *, qmd_in_report: bool = False) -> Path:
    """Create a project laid out as AGENTS.md describes: report.qmd + rendered report/."""
    project = tmp_path / "my-project"
    report = project / "report"
    (report / "figures").mkdir(parents=True)
    qmd = (report if qmd_in_report else project) / "report.qmd"
    qmd.write_text('---\ntitle: "My Report"\nformat: html\n---\n\nBody\n', encoding="utf-8")
    (report / "report.html").write_text("<html><body>report</body></html>", encoding="utf-8")
    (report / "figures" / "fig1.png").write_bytes(b"\x89PNG fake")
    return project


def _zip_names(data: bytes) -> set[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return set(archive.namelist())


def _saved(tmp_path: Path, suffix: str = "json") -> list[Path]:
    """The local copies written to submissions/ (the tests run in tmp_path)."""
    return list((tmp_path / "submissions").glob(f"*.{suffix}"))


# Finding the transcript ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "variable", "session_id", "pattern"),
    [
        ("claude-code", "CLAUDE_CODE_SESSION_ID", CLAUDE_ID, CLAUDE_LOG),
        ("codex", "CODEX_SESSION_ID", CODEX_ID, CODEX_LOG),
    ],
)
def test_locate_transcript_by_session_id(
    host: str, variable: str, session_id: str, pattern: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = Path.home() / pattern.format(session_id)
    _write_jsonl(log, [{}])
    _write_jsonl(Path.home() / pattern.format("another-session"), [{}])
    os.utime(log, (1000, 1000))  # another session in this folder wrote last; must not be picked
    monkeypatch.setenv(variable, session_id)

    assert locate_transcript() == (host, log, session_id)


@pytest.mark.parametrize(
    ("host", "variable", "session_id", "folder_variable", "pattern"),
    [
        ("claude-code", "CLAUDE_CODE_SESSION_ID", CLAUDE_ID, "CLAUDE_CONFIG_DIR", "projects/-Users-me-proj/{}.jsonl"),
        ("codex", "CODEX_SESSION_ID", CODEX_ID, "CODEX_HOME", "sessions/2026/11/07/rollout-x-{}.jsonl"),
    ],
)  # fmt: skip
def test_locate_transcript_in_a_custom_agent_folder(
    host: str,
    variable: str,
    session_id: str,
    folder_variable: str,
    pattern: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = tmp_path / "custom" / pattern.format(session_id)
    _write_jsonl(log, [{}])
    monkeypatch.setenv(folder_variable, str(tmp_path / "custom"))
    monkeypatch.setenv(variable, session_id)

    assert locate_transcript() == (host, log, session_id)


@pytest.mark.parametrize(
    ("host", "records", "session_id"),
    [
        # Claude Code: the first records may lack sessionId (e.g. a summary).
        ("claude-code", [{"type": "summary"}, {"type": "user", "sessionId": CLAUDE_ID}], CLAUDE_ID),
        # Codex: session_meta names the thread (id) and the user's conversation (session_id).
        (
            "codex",
            [{"type": "session_meta", "payload": {"id": "thread-1", "session_id": CODEX_ID}}],
            CODEX_ID,
        ),
    ],
)
def test_locate_transcript_from_a_given_file(
    host: str, records: list[dict], session_id: str, tmp_path: Path
) -> None:
    log = tmp_path / "copied.jsonl"  # anywhere, with no session running
    _write_jsonl(log, records)

    assert locate_transcript(log) == (host, log, session_id)


def test_locate_transcript_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SubmitError, match="Claude Code or Codex.*--transcript-file"):
        locate_transcript()  # no session running

    monkeypatch.setenv("CODEX_SESSION_ID", CODEX_ID)  # set, but no log with that id
    with pytest.raises(SubmitError, match="Claude Code or Codex"):
        locate_transcript()

    notes = tmp_path / "notes.jsonl"
    _write_jsonl(notes, [{"note": "hello"}])
    with pytest.raises(SubmitError, match="not a Claude Code or Codex transcript"):
        locate_transcript(notes)


# Cleaning up the chat and redacting secrets ----------------------------------------------


@pytest.mark.parametrize(
    ("host", "records", "texts"),
    [
        (
            "claude-code",
            [
                {"type": "user", "message": {"role": "user", "content": "hello"}},
                {"type": "assistant", "message": {"role": "assistant", "content": [
                    {"type": "thinking", "thinking": "list the files first"},
                    {"type": "thinking", "thinking": "", "signature": "abc"},  # unreadable: skipped
                    {"type": "text", "text": "hi"},
                    {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                ]}},
                # A message the user typed while the agent was busy; task notifications are skipped.
                {"type": "attachment", "attachment": {"type": "queued_command",
                                                      "commandMode": "prompt", "prompt": "and x"}},
                {"type": "attachment", "attachment": {"type": "queued_command",
                                                      "commandMode": "task-notification",
                                                      "prompt": "<task-notification>"}},
                {"type": "summary", "summary": "skip me"},
            ],
            [
                "hello",
                '[thinking] list the files first\nhi\n[tool call: Bash] {"command": "ls"}',
                "and x",
            ],
        ),
        (
            "codex",
            [
                {"type": "session_meta", "payload": {"cwd": "/x"}},
                {"payload": {"type": "message", "role": "user",
                             "content": [{"type": "input_text", "text": "hello"}]}},
                # Codex encrypts its reasoning; only the summary, when there is one, is readable.
                {"payload": {"type": "reasoning",
                             "summary": [{"type": "summary_text", "text": "Checking files"}]}},
                {"payload": {"type": "reasoning", "summary": [], "encrypted_content": "x"}},
                {"payload": {"type": "function_call", "name": "shell", "arguments": "{}"}},
                {"payload": {"type": "function_call_output", "output": "file1"}},
                {"payload": {"type": "custom_tool_call", "name": "exec", "input": "ls"}},
                {"payload": {"type": "custom_tool_call_output",
                             "output": [{"type": "input_text", "text": "file2"}]}},
                # Messages between the main agent and its subagents; encrypted ones are skipped.
                {"payload": {"type": "agent_message", "author": "/root", "recipient": "/root/a",
                             "content": [{"type": "input_text", "text": "check it"}]}},
                {"payload": {"type": "agent_message", "author": "/root/a", "recipient": "/root",
                             "content": [{"type": "encrypted_content", "text": "",
                                          "encrypted_content": "x"}]}},
            ],
            [
                "hello",
                "[thinking] Checking files",
                "[tool call: shell] {}",
                "file1",
                "[tool call: exec] ls",
                "file2",
                "[agent message /root -> /root/a] check it",
            ],
        ),
    ],
)  # fmt: skip
def test_normalize_records(host: str, records: list[dict], texts: list[str]) -> None:
    assert [message["text"] for message in normalize_records(records, host)] == texts


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
        ("curl -H 'Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abcdefgh'", "eyJhbGciOiJIUzI1NiJ9"),
        ("-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkq\n-----END PRIVATE KEY-----", "MIIEvQIBADANBgkq"),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----", "MIIEowIBAAKCAQEA"),
        ("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXkt\n-----END OPENSSH PRIVATE KEY-----", "b3BlbnNzaC1rZXkt"),
        ("cat key.pem\n-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkq", "MIIEvQIBADANBgkq"),  # cut off
    ],
)  # fmt: skip
def test_redact_removes_whole_secrets(text: str, secret: str) -> None:
    redacted, count = submit._redact(text)

    assert secret not in redacted and "PRIVATE KEY" not in redacted
    assert count == 1


def test_collect_submission_redacts_only_secrets_and_saves(monkeypatch: pytest.MonkeyPatch) -> None:
    _codex_session(monkeypatch, "token sk-abcdefghij1234567890 path /Users/me/file")

    submission = collect_submission("please look at /Users/me/x")

    metadata, saved = submission.metadata, submission.saved
    assert json.loads(saved.read_text(encoding="utf-8")) == metadata
    blob = json.dumps(metadata)
    assert "sk-abcdefghij1234567890" not in blob  # secrets go, paths stay
    assert "/Users/me/file" in blob and "/Users/me/x" in blob
    assert metadata["redaction"] == {"counts": {"secrets": 1}}
    assert (metadata["host"], metadata["session_id"]) == ("codex", CODEX_ID)
    assert saved.stem == metadata["submission_id"]
    assert len(metadata["transcript"]) == 1
    assert "transcript_raw" not in metadata  # only the cleaned-up chat is sent
    assert set(metadata["agent"]) == {"ibl_ai_agent_version", "repo_commit"}
    assert (submission.bundle, submission.warnings) == (None, [])


# The report bundle -----------------------------------------------------------------------


@pytest.mark.parametrize("qmd_in_report", [False, True])
def test_build_report_bundle(qmd_in_report: bool, tmp_path: Path) -> None:
    project = _make_project(tmp_path, qmd_in_report=qmd_in_report)
    (project / "report" / "notes.md").write_text("private notes", encoding="utf-8")

    # The project folder or its report/ folder both work.
    data, title, warnings = build_report_bundle(project / "report" if qmd_in_report else project)

    assert _zip_names(data) == {"report.html", "report.qmd", "figures/fig1.png"}
    assert title == "My Report"
    assert warnings == ["notes.md: non-web file type skipped"]


def test_report_with_data_files_secrets_or_local_paths_is_refused(tmp_path: Path) -> None:
    # The same checks as GitHub Pages publishing (scan_publish_tree), plus the Quarto source.
    project = _make_project(tmp_path)
    report = project / "report"
    (report / "data.npy").write_bytes(b"raw data")
    (report / "report.html").write_text(
        "<html>key sk-abcdefghij1234567890</html>", encoding="utf-8"
    )
    (report / "figures" / "caption.txt").write_text("saved in /Users/me/proj", encoding="utf-8")
    password = '{"password": "correct horse battery staple"}'
    (report / "config.json").write_text(password, encoding="utf-8")
    (project / "report.qmd").write_text(
        f'---\ntitle: "R"\n---\nload("/home/me/x.npy")\n{password}\n', encoding="utf-8"
    )

    with pytest.raises(SubmitError) as error:
        collect_submission("", report_path=project, transcript=False)

    for problem in (
        "data.npy:",
        "report.html: credential-like text detected",
        "config.json: credential-like text detected",
        "figures/caption.txt: absolute local path detected",
        "report.qmd: absolute local path detected",
        "report.qmd: credential-like text detected",
    ):
        assert problem in str(error.value)
    assert not (tmp_path / "submissions").exists()  # nothing saved


def test_build_report_bundle_requires_html_and_qmd(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    (project / "report.qmd").unlink()
    with pytest.raises(SubmitError, match="report.qmd"):
        build_report_bundle(project)
    with pytest.raises(SubmitError, match="report.html"):
        build_report_bundle(tmp_path / "empty")


# Sending ---------------------------------------------------------------------------------


class _FakeResponse:
    """What requests.post returns, reduced to what post_submission reads."""

    def __init__(self, status: int, body: dict) -> None:
        self.status_code, self.ok, self.text = status, status < 400, json.dumps(body)

    def json(self) -> dict:
        return json.loads(self.text)


def _capture_post(monkeypatch: pytest.MonkeyPatch, reply: dict | None = None) -> list[dict]:
    """Patch requests.post to record each call's arguments and accept with ``reply``."""
    sent: list[dict] = []

    def fake_post(url, **kwargs):
        sent.append({"url": url, **kwargs})
        return _FakeResponse(201, reply or {"status": "stored"})

    monkeypatch.setattr(requests, "post", fake_post)
    return sent


def test_post_submission_sends_to_the_built_in_or_the_configured_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _capture_post(monkeypatch, {"status": "stored", "report_url": "https://x/r/"})
    metadata = {"submission_id": "s1", "message": "hello"}

    assert post_submission(metadata, b"zipbytes")["report_url"] == "https://x/r/"
    monkeypatch.setenv("IBL_AGENT_SUBMIT_URL", "https://env.example/api")
    monkeypatch.setenv("IBL_AGENT_SUBMIT_TOKEN", "envtoken")
    post_submission(metadata, None)

    built_in, configured = sent
    assert (built_in["url"], built_in["headers"]) == (
        DEFAULT_SUBMIT_URL,
        {"Authorization": f"Bearer {DEFAULT_SUBMIT_TOKEN}"},
    )
    assert json.loads(built_in["files"]["metadata"][1]) == metadata
    assert built_in["files"]["bundle"] == ("report.zip", b"zipbytes", "application/zip")
    assert (configured["url"], configured["headers"]) == (
        "https://env.example/api",
        {"Authorization": "Bearer envtoken"},
    )
    assert set(configured["files"]) == {"metadata"}  # no report, no bundle


def test_post_submission_refuses_an_upload_over_the_size_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _capture_post(monkeypatch)
    monkeypatch.setattr(submit, "MAX_UPLOAD_BYTES", 10)

    with pytest.raises(SubmitError, match="exceeds"):
        post_submission({"submission_id": "s1"}, None)
    assert sent == []  # refused before anything is sent


# The command -----------------------------------------------------------------------------


def _run_cli(*args: str):
    return CliRunner().invoke(app, ["submit", *args])


def test_submit_cli_saves_only_unless_told_to_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A plain `submit` (no --yes) never sends; with a report the zip is saved next to the JSON.
    sent = _capture_post(monkeypatch)
    _codex_session(monkeypatch)

    result = _run_cli(
        "-m", "the depth lookup looks wrong", "--report", str(_make_project(tmp_path))
    )

    assert result.exit_code == 0, result.output
    assert "mode=local_only" in result.output
    assert "report_title=My Report" in result.output
    assert sent == []
    (saved_json,) = _saved(tmp_path)
    assert saved_json.with_suffix(".zip").exists()


def test_submit_cli_with_transcript_file_outside_an_agent(tmp_path: Path) -> None:
    log = tmp_path / "copied.jsonl"  # no session variable is set (fake_home)
    _write_jsonl(log, [{"type": "user", "sessionId": CLAUDE_ID, "message": {"content": "hi"}}])

    result = _run_cli("-m", "hi", "--transcript-file", str(log), "--no-send")

    assert result.exit_code == 0, result.output
    assert f"session_id={CLAUDE_ID}" in result.output
    (saved_json,) = _saved(tmp_path)
    assert json.loads(saved_json.read_text(encoding="utf-8"))["host"] == "claude-code"


def test_submit_cli_report_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_lookup(*args):
        raise AssertionError("the transcript must not be looked up")

    monkeypatch.setattr(submit, "locate_transcript", no_lookup)

    result = _run_cli("--report", str(_make_project(tmp_path)), "--no-transcript", "--no-send")

    assert result.exit_code == 0, result.output
    assert "transcript=none" in result.output
    (saved_json,) = _saved(tmp_path)
    saved = json.loads(saved_json.read_text(encoding="utf-8"))
    assert (saved["transcript"], saved["host"], saved["session_id"]) == ([], "", "")


@pytest.mark.parametrize("with_report", [False, True])
def test_submit_cli_sends_with_yes(
    with_report: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = _capture_post(monkeypatch, {"status": "stored", "report_url": "https://site.example/r/"})
    _codex_session(monkeypatch)
    report_args = ["--report", str(_make_project(tmp_path))] if with_report else []

    result = _run_cli("-m", "hi", "--yes", *report_args)

    assert result.exit_code == 0, result.output
    assert "mode=sent" in result.output
    assert '"report_url": "https://site.example/r/"' in result.output
    (call,) = sent
    assert set(call["files"]) == ({"metadata", "bundle"} if with_report else {"metadata"})


def test_submit_cli_sends_a_saved_copy_as_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # --saved sends the files the user checked; nothing is rebuilt or saved again.
    sent = _capture_post(monkeypatch)
    _codex_session(monkeypatch)
    _run_cli("-m", "hi", "--report", str(_make_project(tmp_path)), "--no-send")
    (saved_json,) = _saved(tmp_path)

    result = _run_cli("--saved", str(saved_json), "--yes")

    assert result.exit_code == 0, result.output
    assert "mode=sent" in result.output
    (call,) = sent
    assert json.loads(call["files"]["metadata"][1]) == json.loads(saved_json.read_text("utf-8"))
    assert call["files"]["bundle"][1] == saved_json.with_suffix(".zip").read_bytes()
    assert _saved(tmp_path) == [saved_json]


def _connection_refused(url, **kwargs):
    raise requests.ConnectionError("connection refused")


@pytest.mark.parametrize(
    ("fake_post", "expected"),
    [
        (
            lambda url, **kwargs: _FakeResponse(401, {"error": "invalid or missing token"}),
            'HTTP 401): {"error": "invalid or missing token"}',
        ),
        (_connection_refused, "Could not reach"),
    ],
)
def test_submit_cli_reports_server_errors_and_keeps_the_local_copy(
    fake_post, expected: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _codex_session(monkeypatch)
    monkeypatch.setattr(requests, "post", fake_post)

    result = _run_cli("-m", "hi", "--yes")

    assert result.exit_code != 0
    assert expected in result.output
    assert "still saved locally" in result.output
    assert len(_saved(tmp_path)) == 1


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--no-send"], "Nothing to submit"),
        (["-m", "hello", "--no-transcript"], "--no-transcript"),
        (["--saved", "saved.json"], "only --saved and --yes"),
        (["--saved", "saved.json", "--yes", "-m", "hello"], "only --saved and --yes"),
    ],
)
def test_submit_cli_rejects_incomplete_arguments(
    args: list[str], expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _codex_session(monkeypatch)
    Path("saved.json").write_text("{}", encoding="utf-8")  # the tests run in tmp_path

    result = _run_cli(*args)

    assert result.exit_code != 0
    assert expected in result.output
