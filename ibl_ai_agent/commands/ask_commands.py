from __future__ import annotations

from pathlib import Path
from typing import Literal
import json

import typer

from ibl_ai_agent.ask import run_ask
from ibl_ai_agent.ask.domain import AskConfig
from ibl_ai_agent.ask.app.preflight import run_preflight
from ibl_ai_agent.commands.common import fail
from ibl_ai_agent.commands.kernel import run_or_fail
from ibl_ai_agent.errors import IblAgentError
from ibl_ai_agent.local_config import default_project_root


DEFAULT_REPORT_DIR = Path("reports/ask_runs")


def register(app: typer.Typer) -> None:
    @app.command("doctor")
    def ask_doctor(
        auth_mode: Literal["public", "private"] = typer.Option(
            "public",
            help="Authentication mode for Alyx preflight.",
        ),
        auth_base_url: str | None = typer.Option(
            None,
            help="Optional Alyx base URL override.",
        ),
    ) -> None:
        """Run ask runtime preflight checks and print machine-readable diagnostics."""
        report = run_preflight(
            strict_auth=True,
            auth_mode=auth_mode,
            auth_base_url=auth_base_url,
            require_notebook=True,
        )
        typer.echo(json.dumps(report.as_dict(), indent=2))
        if not report.ok:
            raise typer.Exit(1)

    @app.command("ask")
    def ask_question(
        question: str = typer.Argument(..., help="Free-form scientific question about IBL data."),
        manifest: Path = typer.Option(
            Path("docs/ask/example_manifest.yaml"),
            exists=True,
            readable=True,
            help="Manifest used for optional session hints and data-loading artifacts.",
        ),
        report_dir: Path = typer.Option(
            DEFAULT_REPORT_DIR,
            help="Output directory for notebook + answer artifacts.",
        ),
        execute_notebook: bool = typer.Option(
            False,
            help="Execute generated notebook after rendering (typically used with --runtime-mode full).",
        ),
        run_name: str | None = typer.Option(
            None,
            help="Optional run name suffix for output folder.",
        ),
        max_sessions: int | None = typer.Option(
            None,
            min=1,
            help="Maximum number of sessions to load in notebook setup. Omit for no cap.",
        ),
        execution_backend: str = typer.Option(
            "auto",
            help="Notebook execution backend: auto (cli-local -> local -> mcp), cli-local, local, or mcp.",
        ),
        auth_mode: Literal["public", "private"] = typer.Option(
            "public",
            help="Authentication mode for ONE/Alyx preflight.",
        ),
        auth_base_url: str | None = typer.Option(
            None,
            help="Optional Alyx base URL override used during preflight.",
        ),
        runtime_mode: Literal["plan_only", "full"] = typer.Option(
            "plan_only",
            help="Runtime profile: plan_only renders notebook/code only; full runs preflight + optional execution.",
        ),
        sessions_hint: bool = typer.Option(
            True,
            "--sessions-hint/--no-sessions-hint",
            help="Use sessions hint file from manifest for EID seeding.",
        ),
        plan_file: Path | None = typer.Option(
            None,
            exists=True,
            readable=True,
            help="YAML/JSON plan payload with plan_steps, required_outputs_json, analysis_code.",
        ),
    ) -> None:
        """Run an execution-only ask flow: injected plan -> notebook + prose answer."""
        if plan_file is None:
            fail("Execution-only ask runtime requires --plan-file.")
        retry_command = (
            f"UV_CACHE_DIR=.uv-cache uv run ibl-ai-agent ask {question!r} --plan-file {str(plan_file)!r} "
            f"--runtime-mode {runtime_mode!r} --execution-backend {execution_backend!r}"
        )
        effective_report_dir = default_project_root() if report_dir == DEFAULT_REPORT_DIR else report_dir

        config = AskConfig(
            question=question,
            manifest_path=manifest,
            report_dir=effective_report_dir,
            execute_notebook=(execute_notebook and runtime_mode == "full"),
            run_name=run_name,
            max_sessions=max_sessions,
            execution_backend=execution_backend,
            auth_mode=auth_mode,
            auth_base_url=auth_base_url,
            runtime_mode=runtime_mode,
            use_sessions_hint=sessions_hint,
            retry_command=retry_command,
            plan_file=plan_file,
        )
        result = run_or_fail(lambda: run_ask(config=config, question=question), error_type=IblAgentError)

        typer.echo(
            " ".join(
                [
                    f"run_id={result.run_id}",
                    f"run_dir={result.run_dir}",
                    f"notebook={result.notebook_ipynb_path}",
                    f"notebook_edit_url={result.notebook_edit_url}",
                    f"notebook_html={result.notebook_html_path}",
                    f"answer={result.answer_md_path}",
                    f"manifest={result.manifest_path}",
                ]
            )
        )
