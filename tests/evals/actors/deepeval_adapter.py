"""Present an Actor as a deepeval model.

Actors are the models under test, and deliberately do not implement
`DeepEvalBaseLLM`: that interface returns a bare string, with nowhere for token
usage or an agent's tool loop.

Some deepeval metrics still require a model object at construction -- even
non-LLM ones like ToolCorrectnessMetric, which never calls it. This adapter
satisfies that requirement without a second set of credentials, and makes an
actor usable as a judge where a metric genuinely needs one.
"""

from __future__ import annotations

from deepeval.models.base_model import DeepEvalBaseLLM


class ActorJudge(DeepEvalBaseLLM):
    """Wrap an Actor so deepeval can treat it as a model."""

    def __init__(self, actor):
        self.actor = actor

    def load_model(self):
        return self.actor

    def generate(self, prompt, *args, **kwargs):
        """Return (text, cost) as deepeval's model classes do."""
        result = self.actor.run(prompt)
        return result.reply, result.usage.cost_usd

    async def a_generate(self, prompt, *args, **kwargs):
        return self.generate(prompt)

    def get_model_name(self):
        return self.actor.name
