"""Metric registry for the DeepEval eval suite.

Custom metric classes and a ``build_metrics`` factory that instantiates them
from the ``checks`` entries in question JSON files.
"""

from __future__ import annotations

import re

from deepeval.metrics import BaseMetric, ToolCorrectnessMetric
from deepeval.test_case import LLMTestCase


class NeuronCountMetric(BaseMetric):
    """Checks that all expected neuron counts appear in the answer.

    Parameters
    ----------
    expected
        Mapping from region abbreviation to ``[good_count, total_count]``.
    """

    name = "NeuronCountMetric"

    def __init__(self, expected: dict[str, list[int]]) -> None:
        self.threshold = 1.0
        self.score = 0.0
        self.expected = expected

    def measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        clean = re.sub(r"(\d),(\d)", r"\1\2", test_case.actual_output)
        missing = [str(n) for counts in self.expected.values() for n in counts if str(n) not in clean]
        self.score = 0.0 if missing else 1.0
        output_preview = test_case.actual_output.replace("\n", " | ")[:200]
        self.reason = (
            f"Missing counts: {missing} — output: {output_preview}"
            if missing
            else "All counts present"
        )
        return self.score

    def is_successful(self) -> bool:
        return self.score >= self.threshold

    async def a_measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        return self.measure(test_case)


def build_metrics(checks: list[dict], evaluator=None) -> list[BaseMetric]:
    """Instantiate metrics from question JSON ``checks`` entries.

    Parameters
    ----------
    checks
        List of check dicts, each with a ``"metric"`` key and optional config.
    evaluator
        Optional ``DeepEvalBaseLLM`` instance used as the judge model for
        LLM-based metrics (e.g. ``ToolCorrectnessMetric``).  When ``None``,
        deepeval falls back to its default model, which requires OPENAI_API_KEY.

    Returns
    -------
    list[BaseMetric]
    """
    result: list[BaseMetric] = []
    for check in checks:
        name = check["metric"]
        if name == "NeuronCountMetric":
            result.append(NeuronCountMetric(expected=check["expected"]))
        elif name == "ToolCorrectnessMetric":
            result.append(ToolCorrectnessMetric(
                threshold=check.get("threshold", 1.0),
                model=evaluator,
            ))
        else:
            raise ValueError(f"Unknown metric: {name!r}")
    return result
