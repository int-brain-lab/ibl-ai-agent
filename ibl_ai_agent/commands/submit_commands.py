from __future__ import annotations

from pathlib import Path
import json

import typer

from ibl_ai_agent.commands.common import fail
from ibl_ai_agent.commands.kernel import run_or_fail
from ibl_ai_agent.errors import IblAgentError
from ibl_ai_agent.submit import collect_submission, post_submission, submit_url

# Shown with every draft, so the user knows who receives it before deciding to send it.
PRIVACY_NOTICE = (
    "The IBL AI Agent server stores submissions privately: people with an admin account on the "
    "server can read them, and they are kept until a maintainer deletes them. The server also "
    "records your IP address and the size of the upload. Secrets such as tokens and passwords "
    "are redacted from your message on a best effort basis: secrets_redacted=0 does not mean "
    "the message is safe to send, so read it below."
)


def register(app: typer.Typer) -> None:
    @app.command("submit")
    def submit_command(
        message: str = typer.Option(
            "", "--message", "-m", help="What you want to tell the maintainers."
        ),
        send: Path | None = typer.Option(
            None,
            exists=True,
            dir_okay=False,
            help="Send this draft (submissions/<id>.json) as it is, after the user has read it.",
        ),
    ) -> None:
        """Save a message for the IBL maintainers as a draft and show it; --send sends a draft."""
        if send is not None:
            if message:
                fail("--send sends a saved draft as it is: pass only --send.")
            try:
                response = post_submission(send)
            except IblAgentError as exc:
                fail(f"{exc}\nYour submission is still saved locally at {send}.")
            typer.echo("mode=sent")
            typer.echo(f"server_response={json.dumps(response)}")
            return

        if not message:
            fail("Nothing to submit: pass --message.")
        metadata, saved = run_or_fail(lambda: collect_submission(message), error_type=IblAgentError)
        typer.echo(f"destination={submit_url()}")
        typer.echo(PRIVACY_NOTICE)
        typer.echo("Everything that will be sent:")
        typer.echo(saved.read_text(encoding="utf-8"))
        typer.echo(f"secrets_redacted={metadata['redaction']['counts']['secrets']}")
        typer.echo(f"saved_draft={saved}")
        typer.echo(f"Nothing was sent. To send this draft: ibl-ai-agent submit --send {saved}")
