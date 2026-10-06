"""Base classes for eval actors.

An *actor* is the model under test: it receives a question and returns an
answer plus token usage. Every provider exposes the same interface, so the
grid runner never branches on which API it is talking to.

The hierarchy reflects a real capability difference rather than vendor names:

    Actor                      what every provider must offer
    |- CodeExecutingActor      runs code against the local dataset (agent loop)
    |  `- ClaudeSDKActor
    `- ChatCompletionActor     one request/response, no tools
       |- LightningActor       OpenAI-compatible endpoint
       `- MistralActor         Mistral SDK

That split matters for interpreting results: a data-loading question asks for
numbers computed from local parquet files, so a chat-only actor must answer
from reasoning alone and is expected to do worse. `executes_code` records which
kind an actor is, so the results table can say so instead of comparing unlike
things silently.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Usage:
    """Token counts and cost for one run, normalised across providers.

    Providers report usage under different names (prompt_tokens vs
    input_tokens, and so on); each subclass maps its own response onto this.
    cost_usd is 0.0 when the provider does not report one -- absence of a
    figure, not a claim that the call was free.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    num_turns: int = 1
    # Why the loop stopped: "answered", "max_turns", or a provider finish
    # reason such as "malformed_function_call" or "length". A wrong answer and
    # a model that never produced one are different results, and the summary
    # table should be able to tell them apart.
    stop_reason: str = "answered"

    def as_dict(self):
        return self.__dict__.copy()


@dataclass
class RunResult:
    """What an actor returns: the model's reply text plus its usage."""

    reply: str
    usage: Usage = field(default_factory=Usage)


class Actor(ABC):
    """One model behind one provider.

    Subclasses implement `run`. `name` identifies the actor in result tables
    and must be unique within a grid.
    """

    executes_code = False

    def __init__(self, model: str, name: str | None = None):
        self.model = model
        self.name = name or model

    @abstractmethod
    def run(self, prompt: str) -> RunResult:
        """Answer one question. Implementations should let API errors raise:
        a failed call is a real result and should not be silently scored."""

    def __repr__(self):
        return f"{type(self).__name__}(model={self.model!r})"


class CodeExecutingActor(Actor):
    """Actor that can run code and read files in the repo working directory."""

    executes_code = True

    def __init__(self, model, cwd, name=None, max_turns=30):
        super().__init__(model, name)
        self.cwd = cwd
        self.max_turns = max_turns


class ChatCompletionActor(Actor):
    """Actor reached through chat completions, optionally with a tool loop.

    With `cwd` set, the actor gets one tool -- run_python -- and loops:
    call the model, run any code it asks for, feed the output back, repeat
    until it answers or hits max_turns. That is the minimum needed to make a
    data-loading question answerable by a plain chat endpoint, and it makes
    this actor comparable with a code-executing one.

    Without `cwd`, no tool is offered and the model must answer from reasoning
    alone, which is the honest baseline for "can it do this without the data".

    `max_output_tokens` defaults high because reasoning models spend tokens
    before emitting any answer: with a small budget they return empty content
    rather than failing loudly.
    """

    def __init__(self, model, api_key, name=None, max_output_tokens=4096,
                 cwd=None, max_turns=12):
        super().__init__(model, name)
        self.api_key = api_key
        self.max_output_tokens = max_output_tokens
        self.cwd = cwd
        self.max_turns = max_turns

    @property
    def executes_code(self):
        """True when this actor was given the run_python tool."""
        return self.cwd is not None

    @abstractmethod
    def _client(self):
        """Return the provider SDK client. Separated so tests can patch it."""

    @abstractmethod
    def _complete(self, client, messages, tools):
        """One API call. Return (content, tool_calls, usage).

        content:    assistant text, or "" when the model only called tools
        tool_calls: list of objects with .id and .function.name/.arguments,
                    empty when the model answered directly
        usage:      a Usage for this call, with stop_reason set from the
                    provider's finish reason
        """

    @abstractmethod
    def _assistant_message(self, content, tool_calls):
        """Re-encode the model's own turn for the next request's history."""

    @abstractmethod
    def _tool_message(self, call, output):
        """Encode one tool result for the next request's history."""

    def run(self, prompt):
        """Answer one question, running the tool loop when tools are enabled."""
        from .tools import RUN_PYTHON_TOOL, run_python

        client = self._client()
        tools = [RUN_PYTHON_TOOL] if self.executes_code else None
        messages = [{"role": "user", "content": prompt}]
        total = Usage()
        reply = ""

        for turn in range(self.max_turns):
            content, tool_calls, usage = self._complete(client, messages, tools)
            total = _add_usage(total, usage)
            total.num_turns = turn + 1
            reply = content or reply
            if not tool_calls:
                # No text and no tool call means the provider returned nothing
                # usable -- e.g. Gemini's "malformed_function_call". Keep its
                # reason rather than reporting an empty answer.
                total.stop_reason = (
                    "answered" if content else usage.stop_reason or "empty_response"
                )
                break
            messages.append(self._assistant_message(content, tool_calls))
            for call in tool_calls:
                code = _parse_code_argument(call)
                messages.append(self._tool_message(call, run_python(code, self.cwd)))
        else:
            total.stop_reason = "max_turns"

        return RunResult(reply=reply, usage=total)


def _add_usage(total, usage):
    """Sum token counts across the turns of one run."""
    return Usage(
        input_tokens=total.input_tokens + usage.input_tokens,
        output_tokens=total.output_tokens + usage.output_tokens,
        cache_read_tokens=total.cache_read_tokens + usage.cache_read_tokens,
        cache_write_tokens=total.cache_write_tokens + usage.cache_write_tokens,
        cost_usd=total.cost_usd + usage.cost_usd,
        num_turns=total.num_turns,
    )


def _parse_code_argument(call):
    """Pull the `code` argument out of a tool call.

    Arguments arrive as a JSON string (sometimes already decoded). Malformed
    JSON is returned as code that fails loudly, so the model sees the error
    rather than the loop dying.
    """
    import json

    arguments = call.function.arguments
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return "raise SystemExit('tool call arguments were not valid JSON')"
    return arguments.get("code", "")
