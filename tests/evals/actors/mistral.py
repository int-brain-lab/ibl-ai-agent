"""Mistral actor, via the official mistralai SDK.

Note the import path: mistralai 3.x exposes the client as
`mistralai.client.Mistral`, not `mistralai.Mistral` as older docs show. The
wrong path fails with a confusing "ImportError: cannot import name 'Mistral'
from 'mistralai' (unknown location)", because the top level is a namespace
package.
"""

from __future__ import annotations

import os

from .base import ChatCompletionActor, Usage


class MistralActor(ChatCompletionActor):
    """One Mistral model, called as a single chat completion."""

    def __init__(self, model, api_key=None, name=None, max_output_tokens=4096,
                 cwd=None, max_turns=12):
        super().__init__(
            model,
            api_key or os.environ["MISTRAL_API_KEY"],
            name or model,
            max_output_tokens,
            cwd,
            max_turns,
        )

    def _client(self):
        from mistralai.client import Mistral

        return Mistral(api_key=self.api_key)

    def _complete(self, client, messages, tools):
        kwargs = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_output_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        response = client.chat.complete(**kwargs)
        choice = response.choices[0]
        message = choice.message
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
        # Mistral requires the tool name alongside the id.
        return {
            "role": "tool",
            "name": call.function.name,
            "tool_call_id": call.id,
            "content": output,
        }

    @staticmethod
    def _usage_from(usage, finish_reason=None):
        """Map Mistral's usage onto Usage. Cost is not reported by the API."""
        if usage is None:
            return Usage(stop_reason=finish_reason or 'answered')
        return Usage(
            input_tokens=getattr(usage, "prompt_tokens", 0),
            output_tokens=getattr(usage, "completion_tokens", 0),
            stop_reason=finish_reason or "answered",
        )
