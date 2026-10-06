"""Record each eval run, and turn the log into the summary table.

Tests call `record_usage` as they go; this script renders the result:

    UV_CACHE_DIR=.uv-cache uv run --no-sync python tests/evals/summarize_results.py

Writes results/summary.md and prints it. One row per model, one column per
question, plus tokens and cost.

`stop_reason` is reported because failures that score the same are not the same
result: a model that ran out of turns was still trying, one that stopped early
returned a confident wrong answer, and one that never answered hit a provider
error. Only the first two say anything about the model's data skills.
"""

import csv
import sys
from pathlib import Path

import pandas as pd

RESULTS_DIR = Path(__file__).parent / "results"
USAGE_CSV = RESULTS_DIR / "usage.csv"
SUMMARY_MD = RESULTS_DIR / "summary.md"

COLUMNS = ["model", "question", "passed", "input_tokens", "output_tokens",
           "cache_read_tokens", "cache_write_tokens", "cost_usd", "num_turns",
           "stop_reason"]

# Cost is only reported by the Claude CLI; Mistral and Lightning return none.
NO_COST = "not reported"


def record_usage(model_name, question_id, usage, passed):
    """Append one run to results/usage.csv.

    Appending per run (rather than writing at the end) means an interrupted
    grid still leaves its completed rows behind.
    """
    RESULTS_DIR.mkdir(exist_ok=True)
    is_new = not USAGE_CSV.exists()
    with USAGE_CSV.open("a", newline="") as handle:
        writer = csv.writer(handle)
        if is_new:
            writer.writerow(COLUMNS)
        writer.writerow([
            model_name, question_id, passed, usage.input_tokens, usage.output_tokens,
            usage.cache_read_tokens, usage.cache_write_tokens, round(usage.cost_usd, 6),
            usage.num_turns, usage.stop_reason,
        ])


def outcome(row):
    """One cell: a tick, or a cross with why the run ended that way."""
    return "pass" if row.passed else f"fail ({row.stop_reason})"


def summarize(runs):
    """One row per model, one column per question, then tokens and cost.

    Input tokens include cache reads, which dominate for agent runs that re-read
    context every turn.
    """
    runs = runs.assign(input_total=runs.input_tokens + runs.cache_read_tokens)
    # "not run" rather than a blank: a question a model never reached is not
    # the same as one it failed.
    by_question = runs.assign(outcome=runs.apply(outcome, axis=1)).pivot_table(
        index="model", columns="question", values="outcome", aggfunc="first"
    ).fillna("not run")
    totals = runs.groupby("model").agg(
        passed=("passed", "sum"),
        runs_=("passed", "size"),
        input_tokens=("input_total", "sum"),
        output_tokens=("output_tokens", "sum"),
        cost_usd=("cost_usd", "sum"),
    )
    summary = by_question.join(totals)
    summary["pass %"] = (100 * summary.passed / summary.runs_).round(0).astype(int)
    summary["cost"] = summary.cost_usd.map(lambda c: f"${c:.2f}" if c else NO_COST)
    return summary.sort_values(["pass %", "model"], ascending=[False, True])


def to_markdown(summary, runs):
    """Render the summary as markdown, with a one-line header of totals."""
    questions = sorted(runs.question.unique())
    table = summary[questions + ["pass %", "input_tokens", "output_tokens", "cost"]].rename(
        columns={
            "input_tokens": "input tokens (incl. cache)",
            "output_tokens": "output tokens",
        }
    )
    passed, total = int(runs.passed.sum()), len(runs)
    return (
        f"# Eval grid results\n\n"
        f"{len(summary)} models x {len(questions)} questions, {total} runs, "
        f"{passed} passed ({100 * passed // total}%).\n\n"
        f"{table.to_markdown()}\n"
    )


def main():
    if not USAGE_CSV.exists():
        sys.exit(f"no results at {USAGE_CSV} — run the grid first")
    runs = pd.read_csv(USAGE_CSV)
    report = to_markdown(summarize(runs), runs)
    SUMMARY_MD.write_text(report)
    print(report)
    print(f"wrote {SUMMARY_MD}")


if __name__ == "__main__":
    main()
