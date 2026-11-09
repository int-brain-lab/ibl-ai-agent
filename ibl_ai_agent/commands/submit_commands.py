from __future__ import annotations

from pathlib import Path
import json

import typer

from ibl_ai_agent.commands.common import fail
from ibl_ai_agent.commands.kernel import run_or_fail
from ibl_ai_agent.errors import IblAgentError
from ibl_ai_agent.submit import collect_submission, post_submission

# Shown before anything is sent so the user understands what leaves the machine.
PRIVACY_NOTICE = (
    "A submission uploads this session's chat history, your message, and the rendered "
    "report if you include one. Secrets such as tokens and passwords are redacted from "
    "the chat; nothing else is. Check the saved copy before sending. The chat and message "
    "are only visible to the IBL maintainers. A report stays private until the maintainers "
    "review it and publish it on https://iblagent.iblcore.org."
)


def register(app: typer.Typer) -> None:
    @app.command("submit")
    def submit_command(
        message: str = typer.Option(
            "", "--message", "-m", help="What you want to tell the maintainers."
        ),
        report: Path | None = typer.Option(
            None, help="Project folder (or its report/ folder) whose rendered report to include."
        ),
        send: bool = typer.Option(
            False,
            "--yes/--no-send",
            help="--yes sends to the server; otherwise only a local copy is saved in submissions/.",
        ),
        transcript_file: Path | None = typer.Option(
            None,
            exists=True,
            dir_okay=False,
            help="Transcript to send instead of the running session's; works outside an agent.",
        ),
        transcript: bool = typer.Option(
            True,
            "--transcript/--no-transcript",
            help="--no-transcript sends the report without any chat (needs --report).",
        ),
        saved: Path | None = typer.Option(
            None,
            exists=True,
            dir_okay=False,
            help="Send this saved copy (submissions/<id>.json) as it is; needs --yes.",
        ),
    ) -> None:
        """Submit this session's chat (secrets redacted) plus a message and/or a report."""
        if saved is not None:
            # Send the files the user checked, with the report zip saved next to the JSON.
            if not send or message or report or transcript_file or not transcript:
                fail("--saved sends a saved copy as it is: pass only --saved and --yes.")
            metadata = json.loads(saved.read_text(encoding="utf-8"))
            bundle_file = saved.with_suffix(".zip")
            bundle = bundle_file.read_bytes() if bundle_file.is_file() else None
        else:
            if not message and report is None:
                fail("Nothing to submit: pass --message, --report, or both.")
            if not transcript and report is None:
                fail("--no-transcript sends a report alone: pass --report too.")
            typer.echo(PRIVACY_NOTICE)

            metadata, bundle, warnings, saved = run_or_fail(
                lambda: collect_submission(
                    message,
                    report_path=report,
                    transcript_file=transcript_file,
                    transcript=transcript,
                ),
                error_type=IblAgentError,
            )
            if metadata["host"]:
                typer.echo(f"host={metadata['host']}")
                typer.echo(f"session_id={metadata['session_id']}")
                typer.echo(f"messages={len(metadata['transcript'])}")
            else:
                typer.echo("transcript=none (--no-transcript)")
            typer.echo(f"secrets_redacted={metadata['redaction']['counts']['secrets']}")
            if bundle is not None:
                typer.echo(f"report_title={metadata['title']}")
            for warning in warnings:
                typer.echo(f"warning: {warning}")
            typer.echo(f"saved_local_copy={saved}")

            # Only --yes sends; the agent asks the user in the chat before passing it.
            if not send:
                typer.echo("mode=local_only (pass --yes to send)")
                return
        try:
            response = post_submission(metadata, bundle)
        except IblAgentError as exc:
            fail(f"{exc}\nYour submission is still saved locally at {saved}.")
        typer.echo("mode=sent")
        typer.echo(f"server_response={json.dumps(response)}")
