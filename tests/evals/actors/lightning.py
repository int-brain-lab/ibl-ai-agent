"""Lightning AI actor: many models behind one OpenAI-compatible endpoint.

Lightning serves third-party and open-weight models at an OpenAI-shaped API, so
the official `openai` client works against its base URL. Note the key is scoped:
listing models returns more than the key may actually call, and an unauthorised
model raises AuthenticationError on the call rather than at list time.
"""

from __future__ import annotations

import os

from .base import ChatCompletionActor, Usage

BASE_URL = "https://lightning.ai/api/v1/"

# Newer OpenAI-lineage models reject `max_tokens` and require
# `max_completion_tokens`; older ones accept only the former.
_COMPLETION_TOKEN_PREFIXES = ("openai/", "o1", "o3")


class LightningActor(ChatCompletionActor):
    """One Lightning-hosted model, called as a single chat completion."""

    def __init__(self, model, api_key=None, name=None, max_output_tokens=4096,
                 cwd=None, max_turns=12):
        super().__init__(
            model,
            api_key or os.environ["LITAI_API_KEY"],
            name or model.split("/")[-1],
            max_output_tokens,
            cwd,
            max_turns,
        )

    def _client(self):
        from openai import OpenAI

        return OpenAI(api_key=self.api_key, base_url=BASE_URL)

    def _token_limit_kwarg(self):
        """Return the token-limit argument this model accepts."""
        key = (
            "max_completion_tokens"
            if self.model.startswith(_COMPLETION_TOKEN_PREFIXES)
            else "max_tokens"
        )
        return {key: self.max_output_tokens}

    def _complete(self, client, messages, tools):
        kwargs = {"model": self.model, "messages": messages, **self._token_limit_kwarg()}
        if tools:
            kwargs["tools"] = tools
        response = client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        message = choice.message
        # Reasoning models return content=None when the token budget is spent on
        # reasoning; treat that as an empty answer, which the grader fails.
        return (
            message.content or "",
            list(message.tool_calls or []),
            self._usage_from(response.usage, choice.finish_reason),
        )

    @staticmethod
    def _assistant_message(content, tool_calls):
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in tool_calls
            ],
        }

    @staticmethod
    def _tool_message(call, output):
        return {"role": "tool", "tool_call_id": call.id, "content": output}

    @staticmethod
    def _usage_from(usage, finish_reason=None):
        """Map an OpenAI-style usage object onto Usage. Cost is not reported."""
        if usage is None:
            return Usage(stop_reason=finish_reason or 'answered')
        return Usage(
            input_tokens=getattr(usage, "prompt_tokens", 0),
            output_tokens=getattr(usage, "completion_tokens", 0),
            stop_reason=finish_reason or "answered",
        )
