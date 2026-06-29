"""CLI command for collecting and sending interactive-session feedback."""

from __future__ import annotations

from pathlib import Path
import json

import typer

from ibl_ai_agent.commands.common import fail
from ibl_ai_agent.errors import IblAgentError
from ibl_ai_agent.feedback import (
    DEFAULT_FEEDBACK_DIR,
    collect_feedback,
    post_payload,
    resolve_feedback_config,
)

# Shown before anything is sent so the user understands what leaves the machine.
PRIVACY_NOTICE = (
    "Feedback bundles your message with this session's chat history. Secrets and "
    "local file paths are redacted automatically, but you remain responsible for "
    "checking the saved copy before sending. The transcript is private to the "
    "feedback server; it is not made public."
)


def register(app: typer.Typer) -> None:
    @app.command("feedback")
    def feedback_command(
        message: str = typer.Option(
            ...,
            "--message",
            "-m",
            help="Your feedback text describing the issue or suggestion.",
        ),
        send: bool = typer.Option(
            True,
            "--send/--no-send",
            help="Send to the configured server. --no-send only saves a local copy.",
        ),
        dry_run: bool = typer.Option(
            False,
            "--dry-run",
            help="Build and save a local copy only; never contact the server.",
        ),
        host: str = typer.Option(
            "auto",
            help="Which coding agent's transcript to read: auto, claude, or codex.",
        ),
        session_file: Path | None = typer.Option(
            None,
            exists=True,
            readable=True,
            help="Explicit transcript file, overriding auto-detection.",
        ),
        feedback_dir: Path = typer.Option(
            DEFAULT_FEEDBACK_DIR,
            help="Directory for local copies of feedback payloads.",
        ),
        yes: bool = typer.Option(
            False,
            "--yes",
            "-y",
            help="Skip the confirmation prompt before sending.",
        ),
    ) -> None:
        """Collect this session's transcript plus a message, redact it, and send it."""
        typer.echo(PRIVACY_NOTICE)

        # 1. Locate, normalize, redact, and save a local copy.
        try:
            result = collect_feedback(
                message,
                host=host,
                session_file=session_file,
                feedback_dir=feedback_dir,
            )
        except IblAgentError as exc:
            fail(str(exc))

        # 2. Report what was gathered (basename only; never echo the full path).
        counts = result.redaction
        typer.echo(f"host={result.source.host}")
        typer.echo(f"transcript_file={result.source.path.name}")
        typer.echo(f"messages={len(result.payload['transcript'])}")
        typer.echo(
            "redactions: "
            f"secrets={counts['secrets']} paths={counts['paths']} "
            f"identifiers={counts['identifiers']}"
        )
        typer.echo(f"saved_local_copy={result.local_path}")

        # 3. Decide whether to send.
        if dry_run:
            typer.echo("mode=dry_run (nothing sent)")
            return
        if not send:
            typer.echo("mode=local_only (--no-send)")
            return

        url, token = resolve_feedback_config()
        if not url or not token:
            fail(
                "Feedback server is not configured, so nothing was sent. Set "
                "IBL_AGENT_FEEDBACK_URL and IBL_AGENT_FEEDBACK_TOKEN (or add "
                "feedback_url/feedback_token to ibl-agent.local.yaml). Your "
                f"feedback was saved locally at {result.local_path}."
            )

        if not yes and not typer.confirm(f"Send this feedback to {url}?"):
            fail(f"Feedback not sent. The local copy was kept at {result.local_path}.")

        # 4. Send.
        try:
            response = post_payload(result.payload, url=url, token=token)
        except IblAgentError as exc:
            fail(f"{exc}\nYour feedback is still saved locally at {result.local_path}.")

        typer.echo("mode=sent")
        typer.echo(f"server_response={json.dumps(response)}")
