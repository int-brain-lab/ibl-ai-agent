from __future__ import annotations

from pathlib import Path
import json
import webbrowser

import typer

from ibl_ai_agent.commands.common import fail
from ibl_ai_agent.commands.kernel import run_or_fail
from ibl_ai_agent.errors import IblAgentError
from ibl_ai_agent.submit import (
    PRIVACY_NOTICE,
    REPORT_NOTICE,
    collect_submission,
    post_submission,
    submit_url,
    write_preview,
)


def register(app: typer.Typer) -> None:
    @app.command("submit")
    def submit_command(
        message: str = typer.Option(
            "", "--message", "-m", help="What you want to tell the maintainers."
        ),
        transcript: bool = typer.Option(
            False,
            help="Include this session's chat (with tool calls, tool outputs and readable reasoning).",
        ),
        transcript_file: Path | None = typer.Option(
            None,
            exists=True,
            dir_okay=False,
            help="Include this transcript instead of the running session's; works outside an agent.",
        ),
        messages: str | None = typer.Option(
            None, help="Include only these chat messages, as numbered in the preview, e.g. 12-30."
        ),
        report: Path | None = typer.Option(
            None, help="Project folder (or its report/ folder) whose rendered report to include."
        ),
        report_source: bool = typer.Option(False, help="Also include the report's report.qmd."),
        allow_publishing: bool = typer.Option(
            False, help="The user agreed to the report being published on iblagent.iblcore.org."
        ),
        send: Path | None = typer.Option(
            None,
            exists=True,
            dir_okay=False,
            help="Send this draft (submissions/<id>.json) as it is, after the user has read it.",
        ),
    ) -> None:
        """Save a submission for the IBL maintainers as a draft and show it; --send sends a draft."""
        if send is not None:
            if any((message, transcript, transcript_file, messages, report, report_source, allow_publishing)):
                fail("--send sends a saved draft as it is: pass only --send.")
            try:
                response = post_submission(send)
            except IblAgentError as exc:
                fail(f"{exc}\nYour submission is still saved locally at {send}.")
            typer.echo("mode=sent")
            typer.echo(f"server_response={json.dumps(response)}")
            return

        if report is None and (report_source or allow_publishing):
            fail("--report-source and --allow-publishing need --report.")
        transcript = transcript or transcript_file is not None or messages is not None
        if not (message or transcript or report):
            fail("Nothing to submit: pass --message, --transcript or --report.")
        metadata, saved, warnings = run_or_fail(
            lambda: collect_submission(
                message,
                transcript=transcript,
                transcript_file=transcript_file,
                messages=tuple(map(int, messages.split("-"))) if messages else None,
                report_path=report,
                report_source=report_source,
                publication_consent=allow_publishing,
            ),
            error_type=IblAgentError,
        )
        typer.echo(f"destination={submit_url()}")
        typer.echo(PRIVACY_NOTICE)
        if not (transcript or report):
            typer.echo("Everything that will be sent:")
            typer.echo(saved.read_text(encoding="utf-8"))
        else:
            if report:
                typer.echo(f"{REPORT_NOTICE} publication_consent={str(allow_publishing).lower()}")
            for warning in warnings:
                typer.echo(f"warning: {warning}")
            preview = write_preview(saved)
            webbrowser.open(preview.resolve().as_uri())
            typer.echo(f"preview={preview} (everything that will be sent; opened in your browser)")
        typer.echo(f"secrets_redacted={metadata['redaction']['counts']['secrets']}")
        typer.echo(f"saved_draft={saved}")
        typer.echo(f"Nothing was sent. To send this draft: ibl-ai-agent submit --send {saved}")
