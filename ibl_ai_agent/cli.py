from __future__ import annotations

import typer

from ibl_ai_agent.commands.access_commands import register as register_access_commands
from ibl_ai_agent.commands.ask_commands import register as register_ask_commands
from ibl_ai_agent.commands.feedback_commands import register as register_feedback_commands
from ibl_ai_agent.commands.maintenance_commands import register as register_maintenance_commands
from ibl_ai_agent.commands.plan_commands import register as register_plan_commands
from ibl_ai_agent.commands.report_commands import register as register_report_commands

app = typer.Typer(help="IBL agent CLI")
access_app = typer.Typer(help="IBL ONE/Alyx access commands")
plan_app = typer.Typer(help="Ask plan commands")

app.add_typer(access_app, name="access")
app.add_typer(plan_app, name="plan")

register_ask_commands(app)
register_feedback_commands(app)
register_maintenance_commands(app)
register_access_commands(access_app)
register_plan_commands(plan_app)
register_report_commands(app)

if __name__ == "__main__":
    app()
