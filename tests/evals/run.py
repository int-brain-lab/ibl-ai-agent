"""Run the eval tasks on a list of models: the entry point for CI and for ad hoc runs.

    uv run python -m tests.evals.run                      # models of models.json
    uv run python -m tests.evals.run --models anthropic/claude-haiku-4-5
    uv run python -m tests.evals.run --models-file my_models.json --task skill_selection

Exits 1 when a run breaks (sandbox, provider error), not when answers are wrong.
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent


def load_models(models: str = "", models_file: str | Path = HERE.joinpath("models.json")) -> list[str]:
    """Return Inspect model ids: from a comma-separated string if given, else from a JSON list."""
    if models.strip():
        return [m.strip() for m in models.split(",") if m.strip()]
    return json.loads(Path(models_file).read_text())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--models", default="", help="comma-separated model ids; overrides the file")
    parser.add_argument("--models-file", default=HERE.joinpath("models.json"), help="JSON list of ids")
    parser.add_argument("--task", action="append", help="task name (repeatable); default all")
    parser.add_argument("--log-dir", default=HERE.joinpath("logs"))
    parser.add_argument("--max-connections", type=int, default=2)
    args = parser.parse_args(argv)

    from inspect_ai import eval as inspect_eval  # heavy import, not needed to load models

    task_file = "tests/evals/tasks.py"
    tasks = [f"{task_file}@{name}" for name in args.task or []]

    logs = inspect_eval(
        tasks or task_file,
        model=load_models(args.models, args.models_file),
        log_dir=str(args.log_dir),
        max_connections=args.max_connections,
    )
    broken = [log for log in logs if log.status != "success"]
    for log in broken:
        print(f"{log.eval.task} / {log.eval.model}: {log.status} {log.error or ''}", file=sys.stderr)
    return int(bool(broken))


if __name__ == "__main__":
    sys.exit(main())
