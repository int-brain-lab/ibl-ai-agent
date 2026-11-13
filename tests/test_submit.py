from __future__ import annotations

from html import escape
from pathlib import Path
import io
import json
import os
import webbrowser
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
    write_preview,
)

CLAUDE_ID = "b2f89d44-3c48-48ba-9c80-11eef8cdf8e8"
CODEX_ID = "01a1b6fa-cc02-7081-8da3-a71fb2efd057"
# Where each agent writes the log of session {} (relative to the home folder).
CLAUDE_LOG = ".claude/projects/-Users-me-proj/{}.jsonl"
CODEX_LOG = ".codex/sessions/2026/11/07/rollout-2026-11-07T17-07-31-{}.jsonl"
# What a message-only draft holds.
MESSAGE_KEYS = {
    "schema_version", "submission_id", "created_at", "message", "agent", "environment", "redaction",
}  # fmt: skip


@pytest.fixture(autouse=True)
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Run in tmp_path with a temporary home, no real session ids or server settings, no network.

    The tests may run inside an agent, whose session id would otherwise be picked up. Drafts land
    in tmp_path/submissions. A test that sends replaces requests.post (_capture_post). Returns the
    URLs the command opened in a browser.
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
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", opened.append)
    return opened


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _codex_session(monkeypatch: pytest.MonkeyPatch, *texts: str) -> Path:
    """Write a Codex log of user messages (one "hi" by default), mark it as the running session."""
    log = Path.home() / CODEX_LOG.format(CODEX_ID)
    _write_jsonl(log, [
        {"payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}}
        for text in texts or ["hi"]
    ])  # fmt: skip
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


def _saved(tmp_path: Path) -> list[Path]:
    """The drafts written to submissions/ (the tests run in tmp_path)."""
    return list((tmp_path / "submissions").glob("*.json"))


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



# Building the draft ----------------------------------------------------------------------


def test_collect_submission_saves_only_the_message() -> None:
    submission = collect_submission("token sk-abcdefghij1234567890 in /Users/me/x")

    metadata, saved = submission.metadata, submission.saved
    assert set(metadata) == MESSAGE_KEYS  # no chat, no report
    assert "sk-abcdefghij1234567890" not in metadata["message"]  # secrets go, paths stay
    assert "/Users/me/x" in metadata["message"]
    assert metadata["redaction"] == {"counts": {"secrets": 1}}
    assert set(metadata["agent"]) == {"ibl_ai_agent_version", "repo_commit"}
    assert saved.stem == metadata["submission_id"]
    assert json.loads(saved.read_text(encoding="utf-8")) == metadata
    assert saved.with_suffix(".sha256").is_file()  # so a changed draft can be refused
    assert not saved.with_suffix(".zip").exists()


@pytest.mark.parametrize(
    ("options", "extra_keys", "zipped"),
    [
        ({"transcript": True}, {"host", "session_id", "transcript"}, None),
        ({"report": True}, {"publication_consent"}, {"report.html", "figures/fig1.png"}),
        (
            {"report": True, "report_source": True},
            {"publication_consent"},
            {"report.html", "figures/fig1.png", "report.qmd"},
        ),
    ],
)
def test_collect_submission_adds_only_the_selected_attachments(
    options: dict, extra_keys: set[str], zipped: set[str] | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _codex_session(monkeypatch)
    report = options.pop("report", False) and _make_project(tmp_path)

    submission = collect_submission("hi", report_path=report or None, **options)

    assert set(submission.metadata) == MESSAGE_KEYS | extra_keys
    bundle = submission.saved.with_suffix(".zip")
    assert (_zip_names(bundle.read_bytes()) if bundle.exists() else None) == zipped


def test_collect_submission_redacts_the_chat_and_keeps_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    _codex_session(monkeypatch, "token sk-abcdefghij1234567890 path /Users/me/file")

    metadata = collect_submission("please look at /Users/me/x", transcript=True).metadata

    blob = json.dumps(metadata)
    assert "sk-abcdefghij1234567890" not in blob  # secrets go, paths stay
    assert "/Users/me/file" in blob and "/Users/me/x" in blob
    assert metadata["redaction"] == {"counts": {"secrets": 1}}
    assert (metadata["host"], metadata["session_id"]) == ("codex", CODEX_ID)
    assert [message["text"] for message in metadata["transcript"]] == [
        "token [REDACTED-SECRET] path /Users/me/file"
    ]


def test_collect_submission_sends_an_excerpt_of_the_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    _codex_session(monkeypatch, "one", "two", "three")

    metadata = collect_submission("", transcript=True, messages=(2, 3)).metadata

    assert [message["text"] for message in metadata["transcript"]] == ["two", "three"]
    assert metadata["transcript_range"] == [2, 3]
    with pytest.raises(SubmitError, match="1-3"):
        collect_submission("", transcript=True, messages=(2, 4))


def test_publication_needs_explicit_consent(tmp_path: Path) -> None:
    project = _make_project(tmp_path)

    assert collect_submission("", report_path=project).metadata["publication_consent"] is False
    assert collect_submission("", report_path=project, publication_consent=True).metadata[
        "publication_consent"
    ]


# The report bundle -----------------------------------------------------------------------


@pytest.mark.parametrize("qmd_in_report", [False, True])
@pytest.mark.parametrize("include_source", [False, True])
def test_build_report_bundle(qmd_in_report: bool, include_source: bool, tmp_path: Path) -> None:
    project = _make_project(tmp_path, qmd_in_report=qmd_in_report)
    (project / "report" / "notes.md").write_text("private notes", encoding="utf-8")

    # The project folder or its report/ folder both work.
    data, warnings = build_report_bundle(
        project / "report" if qmd_in_report else project, include_source=include_source
    )

    assert _zip_names(data) == {"report.html", "figures/fig1.png"} | (
        {"report.qmd"} if include_source else set()
    )
    assert warnings == ["figures/fig1.png: not checked for secrets", "notes.md: non-web file type skipped"]


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
        collect_submission("", report_path=project, report_source=True)

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


def test_build_report_bundle_requires_html_and_qmd_when_asked(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    (project / "report.qmd").unlink()
    build_report_bundle(project, include_source=False)  # the source isn't needed
    with pytest.raises(SubmitError, match="report.qmd"):
        build_report_bundle(project, include_source=True)
    with pytest.raises(SubmitError, match="report.html"):
        build_report_bundle(tmp_path / "empty", include_source=False)


# The preview -----------------------------------------------------------------------------


def test_write_preview_shows_everything_and_runs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _codex_session(monkeypatch, "first <b>message</b>", "second")
    project = _make_project(tmp_path)
    (project / "report" / "report.html").write_text(
        "<html><script>alert(1)</script></html>", encoding="utf-8"
    )
    submission = collect_submission("my message", transcript=True, report_path=project)

    page = write_preview(submission.saved).read_text(encoding="utf-8")

    assert "Content-Security-Policy" in page and "default-src 'none'" in page
    assert "<script" not in page  # the report's script is shown as text, never run
    assert DEFAULT_SUBMIT_URL in page and "did not agree to publication" in page
    for text in ("my message", escape("first <b>message</b>"), "second"):
        assert text in page
    with zipfile.ZipFile(submission.saved.with_suffix(".zip")) as archive:
        for info in archive.infolist():
            assert info.filename in page and f"{info.file_size:,} bytes" in page
    assert "not checked for secrets" in page and "data:image/png;base64," in page
    assert submission.metadata["submission_id"] in page and escape('"python"') in page
    assert "secrets_redacted=0" in page


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


def test_post_submission_sends_the_draft_to_the_built_in_or_the_configured_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _capture_post(monkeypatch, {"status": "stored"})
    saved = collect_submission("hello").saved

    assert post_submission(saved) == {"status": "stored"}
    monkeypatch.setenv("IBL_AGENT_SUBMIT_URL", "https://env.example/api")
    monkeypatch.setenv("IBL_AGENT_SUBMIT_TOKEN", "envtoken")
    post_submission(saved)

    built_in, configured = sent
    assert (built_in["url"], built_in["headers"]) == (
        DEFAULT_SUBMIT_URL,
        {"Authorization": f"Bearer {DEFAULT_SUBMIT_TOKEN}"},
    )
    # The saved bytes and nothing else.
    assert built_in["files"] == {"metadata": (None, saved.read_bytes(), "application/json")}
    assert (configured["url"], configured["headers"]) == (
        "https://env.example/api",
        {"Authorization": "Bearer envtoken"},
    )


def test_post_submission_refuses_an_upload_over_the_size_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _capture_post(monkeypatch)
    monkeypatch.setattr(submit, "MAX_UPLOAD_BYTES", 10)

    with pytest.raises(SubmitError, match="exceeds"):
        post_submission(collect_submission("hello").saved)
    assert sent == []  # refused before anything is sent


# The command -----------------------------------------------------------------------------


def _run_cli(*args: str):
    return CliRunner().invoke(app, ["submit", *args])


def test_submit_cli_shows_everything_and_sends_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = _capture_post(monkeypatch)

    result = _run_cli("-m", "the depth lookup looks wrong")

    assert result.exit_code == 0, result.output
    (saved,) = _saved(tmp_path)
    assert saved.read_text(encoding="utf-8") in result.output  # the complete content
    assert f"destination={DEFAULT_SUBMIT_URL}" in result.output
    assert "best effort" in result.output
    assert f"ibl-ai-agent submit --send {saved.relative_to(tmp_path)}" in result.output
    assert sent == []


def test_submit_cli_opens_a_preview_for_attachments_and_sends_nothing(
    fake_home: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = _capture_post(monkeypatch)
    _codex_session(monkeypatch)

    result = _run_cli("--transcript", "--report", str(_make_project(tmp_path)))  # no message

    assert result.exit_code == 0, result.output
    (saved,) = _saved(tmp_path)
    preview = saved.with_suffix(".html")
    assert f"preview={preview.relative_to(tmp_path)}" in result.output
    assert fake_home == [preview.resolve().as_uri()]
    assert "warning: figures/fig1.png: not checked for secrets" in result.output
    assert f"--send {saved.relative_to(tmp_path)}" in result.output
    assert sent == []


def test_submit_cli_with_transcript_file_outside_an_agent(tmp_path: Path) -> None:
    log = tmp_path / "copied.jsonl"  # no session variable is set (fake_home)
    _write_jsonl(log, [{"type": "user", "sessionId": CLAUDE_ID, "message": {"content": "hi"}}])

    result = _run_cli("-m", "hi", "--transcript-file", str(log))

    assert result.exit_code == 0, result.output
    (saved,) = _saved(tmp_path)
    assert json.loads(saved.read_text(encoding="utf-8"))["host"] == "claude-code"


@pytest.mark.parametrize("with_report", [False, True])
def test_submit_cli_sends_the_saved_draft_as_it_is(
    with_report: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = _capture_post(monkeypatch)
    _run_cli("-m", "hi", *(["--report", str(_make_project(tmp_path))] if with_report else []))
    (saved,) = _saved(tmp_path)

    result = _run_cli("--send", str(saved))

    assert result.exit_code == 0, result.output
    assert "mode=sent" in result.output
    (call,) = sent
    assert call["files"]["metadata"][1] == saved.read_bytes()
    if with_report:
        assert call["files"]["bundle"] == ("report.zip", saved.with_suffix(".zip").read_bytes(), "application/zip")
    assert _saved(tmp_path) == [saved]  # nothing is rebuilt or saved again


@pytest.mark.parametrize("change", ["edit the JSON", "edit the zip", "remove the zip", "another file"])
def test_submit_cli_refuses_a_draft_changed_after_it_was_built(
    change: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = _capture_post(monkeypatch)
    _run_cli("-m", "hello", "--report", str(_make_project(tmp_path)))
    (draft,) = _saved(tmp_path)
    bundle = draft.with_suffix(".zip")
    if change == "edit the JSON":
        draft.write_text(draft.read_text(encoding="utf-8").replace("hello", "edited"), encoding="utf-8")
    elif change == "edit the zip":
        bundle.write_bytes(bundle.read_bytes() + b"x")
    elif change == "remove the zip":
        bundle.unlink()
    else:
        draft = tmp_path / "other.json"  # not built by `submit`
        draft.write_text("{}", encoding="utf-8")

    result = _run_cli("--send", str(draft))

    assert result.exit_code != 0
    assert "Build a new draft" in result.output
    assert sent == []


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
def test_submit_cli_reports_server_errors_and_keeps_the_draft(
    fake_post, expected: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run_cli("-m", "hi")
    (saved,) = _saved(tmp_path)
    monkeypatch.setattr(requests, "post", fake_post)

    result = _run_cli("--send", str(saved))

    assert result.exit_code != 0
    assert expected in result.output
    assert "still saved locally" in result.output
    assert saved.is_file()


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ([], "Nothing to submit"),
        (["--send", "saved.json", "-m", "hello"], "pass only --send"),
        (["-m", "hello", "--report-source"], "need --report"),
        (["-m", "hello", "--allow-publishing"], "need --report"),
    ],
)
def test_submit_cli_rejects_incomplete_arguments(args: list[str], expected: str) -> None:
    Path("saved.json").write_text("{}", encoding="utf-8")  # the tests run in tmp_path

    result = _run_cli(*args)

    assert result.exit_code != 0
    assert expected in result.output
