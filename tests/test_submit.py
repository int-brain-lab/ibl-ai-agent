from __future__ import annotations

from pathlib import Path
import json

import pytest
import requests
from typer.testing import CliRunner

from ibl_ai_agent import submit
from ibl_ai_agent.cli import app
from ibl_ai_agent.errors import SubmitError
from ibl_ai_agent.submit import (
    DEFAULT_SUBMIT_TOKEN,
    DEFAULT_SUBMIT_URL,
    collect_submission,
    post_submission,
)


@pytest.fixture(autouse=True)
def offline_in_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run in tmp_path with no server settings and no network.

    Drafts land in tmp_path/submissions. A test that sends replaces requests.post (_capture_post).
    """
    monkeypatch.chdir(tmp_path)
    for variable in ("IBL_AGENT_SUBMIT_URL", "IBL_AGENT_SUBMIT_TOKEN"):
        monkeypatch.delenv(variable, raising=False)

    def no_network(url, **kwargs):
        raise AssertionError(f"a test tried to send to {url}")

    monkeypatch.setattr(requests, "post", no_network)


def _saved(tmp_path: Path) -> list[Path]:
    """The drafts written to submissions/ (the tests run in tmp_path)."""
    return list((tmp_path / "submissions").glob("*.json"))


# Building the draft ----------------------------------------------------------------------


def test_collect_submission_saves_only_the_message() -> None:
    submission = collect_submission("token sk-abcdefghij1234567890 in /Users/me/x")

    metadata, saved = submission.metadata, submission.saved
    assert set(metadata) == {
        "schema_version", "submission_id", "created_at", "message", "agent", "environment",
        "redaction",
    }  # no chat, no report  # fmt: skip
    assert "sk-abcdefghij1234567890" not in metadata["message"]  # secrets go, paths stay
    assert "/Users/me/x" in metadata["message"]
    assert metadata["redaction"] == {"counts": {"secrets": 1}}
    assert set(metadata["agent"]) == {"ibl_ai_agent_version", "repo_commit"}
    assert saved.stem == metadata["submission_id"]
    assert json.loads(saved.read_text(encoding="utf-8")) == metadata
    assert saved.with_suffix(".sha256").is_file()  # so a changed draft can be refused


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


def test_submit_cli_sends_the_saved_draft_as_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = _capture_post(monkeypatch)
    _run_cli("-m", "hi")
    (saved,) = _saved(tmp_path)

    result = _run_cli("--send", str(saved))

    assert result.exit_code == 0, result.output
    assert "mode=sent" in result.output
    (call,) = sent
    assert call["files"]["metadata"][1] == saved.read_bytes()
    assert _saved(tmp_path) == [saved]  # nothing is rebuilt or saved again


def test_submit_cli_refuses_a_draft_changed_after_it_was_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = _capture_post(monkeypatch)
    _run_cli("-m", "hello")
    (saved,) = _saved(tmp_path)
    saved.write_text(saved.read_text(encoding="utf-8").replace("hello", "edited"), encoding="utf-8")
    other = tmp_path / "other.json"  # not built by `submit --message`
    other.write_text("{}", encoding="utf-8")

    for draft in (saved, other):
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
        (["-m", "hello", "--report", "my-project"], "No such option"),  # attachments come later
    ],
)
def test_submit_cli_rejects_incomplete_arguments(args: list[str], expected: str) -> None:
    Path("saved.json").write_text("{}", encoding="utf-8")  # the tests run in tmp_path

    result = _run_cli(*args)

    assert result.exit_code != 0
    assert expected in result.output
