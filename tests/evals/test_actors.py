"""Unit tests for the actor hierarchy, with every API call mocked.

These run offline with no API keys: they test our own logic - request shape,
response parsing, usage normalisation, factory dispatch - not the providers.
What a real call returns is checked separately by running the grid.

Run with:  UV_CACHE_DIR=.uv-cache uv run --no-sync pytest tests/evals/test_actors.py
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.evals.actors import (
    ChatCompletionActor,
    ClaudeSDKActor,
    CodeExecutingActor,
    LightningActor,
    MistralActor,
    Usage,
    build_actor,
)


def chat_response(content, prompt_tokens=11, completion_tokens=7, tool_calls=None,
                  finish_reason="stop"):
    """A fake OpenAI/Mistral-shaped chat response."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls),
                finish_reason=finish_reason,
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
        ),
    )


# --------------------------------------------------------------------------
# factory
# --------------------------------------------------------------------------


def test_factory_builds_each_provider():
    claude = build_actor({"provider": "claude", "model": "haiku"}, cwd="/repo")
    lightning = build_actor(
        {"provider": "lightning", "model": "openai/gpt-5-nano", "api_key": "k"}
    )
    mistral = build_actor(
        {"provider": "mistral", "model": "ministral-8b-latest", "api_key": "k"}
    )
    assert isinstance(claude, ClaudeSDKActor)
    assert isinstance(lightning, LightningActor)
    assert isinstance(mistral, MistralActor)


def test_factory_passes_cwd_to_every_actor_that_can_run_code():
    claude = build_actor({"provider": "claude", "model": "haiku"}, cwd="/repo")
    chat = build_actor({"provider": "mistral", "model": "m", "api_key": "k"}, cwd="/repo")
    assert claude.cwd == "/repo"
    assert chat.cwd == "/repo"  # chat actors get it too, to run the tool loop


def test_factory_rejects_unknown_provider():
    with pytest.raises(KeyError):
        build_actor({"provider": "nope", "model": "m"})


def test_capability_flags_distinguish_actor_kinds():
    # The results table relies on this to avoid comparing unlike things.
    assert ClaudeSDKActor("haiku", cwd="/repo").executes_code is True
    assert MistralActor("m", api_key="k").executes_code is False
    assert MistralActor("m", api_key="k", cwd="/repo").executes_code is True
    assert issubclass(ClaudeSDKActor, CodeExecutingActor)
    assert issubclass(LightningActor, ChatCompletionActor)


def test_factory_tools_flag_controls_whether_a_chat_actor_can_run_code():
    with_tools = build_actor({"provider": "mistral", "model": "m", "api_key": "k"}, cwd="/repo")
    without = build_actor(
        {"provider": "mistral", "model": "m", "api_key": "k", "tools": False}, cwd="/repo"
    )
    assert with_tools.executes_code is True
    assert without.executes_code is False


def test_claude_resolves_model_aliases_but_keeps_display_name():
    actor = ClaudeSDKActor("haiku", cwd="/repo")
    assert actor.model == "claude-haiku-4-5"
    assert actor.name == "haiku"
    # An explicit model id is passed through untouched.
    assert ClaudeSDKActor("claude-opus-5", cwd="/repo").model == "claude-opus-5"


# --------------------------------------------------------------------------
# chat actors: request shape and response parsing
# --------------------------------------------------------------------------


def test_lightning_sends_prompt_and_parses_reply():
    actor = LightningActor("lightning-ai/gemma-4-31B-it", api_key="k")
    client = MagicMock()
    client.chat.completions.create.return_value = chat_response("the answer")
    with patch.object(actor, "_client", return_value=client):
        result = actor.run("how many sessions?")

    sent = client.chat.completions.create.call_args.kwargs
    assert sent["model"] == "lightning-ai/gemma-4-31B-it"
    assert sent["messages"] == [{"role": "user", "content": "how many sessions?"}]
    assert result.reply == "the answer"
    assert result.usage.input_tokens == 11
    assert result.usage.output_tokens == 7


@pytest.mark.parametrize(
    "model, expected_kwarg",
    [
        ("openai/gpt-5-nano", "max_completion_tokens"),
        ("o3-mini", "max_completion_tokens"),
        ("lightning-ai/gemma-4-31B-it", "max_tokens"),
    ],
)
def test_lightning_picks_the_token_limit_argument_each_model_accepts(model, expected_kwarg):
    # Newer OpenAI-lineage models reject max_tokens; older ones reject the other.
    actor = LightningActor(model, api_key="k")
    client = MagicMock()
    client.chat.completions.create.return_value = chat_response("ok")
    with patch.object(actor, "_client", return_value=client):
        actor.run("q")
    sent = client.chat.completions.create.call_args.kwargs
    assert expected_kwarg in sent
    assert sent[expected_kwarg] == actor.max_output_tokens


def test_reasoning_model_returning_no_content_gives_empty_reply():
    # Observed with Qwen3.8-27B and gpt-5-nano on small budgets: reasoning
    # consumes the allowance and content comes back as None. That must not
    # crash -- it is an empty answer, which the grader then fails.
    actor = LightningActor("lightning-ai/Qwen3.8-27B", api_key="k")
    client = MagicMock()
    client.chat.completions.create.return_value = chat_response(None)
    with patch.object(actor, "_client", return_value=client):
        assert actor.run("q").reply == ""


def test_missing_usage_block_yields_zeroed_usage():
    actor = MistralActor("ministral-8b-latest", api_key="k")
    response = chat_response("hi")
    response.usage = None
    client = MagicMock()
    client.chat.complete.return_value = response
    with patch.object(actor, "_client", return_value=client):
        assert actor.run("q").usage == Usage()


def test_mistral_uses_its_own_sdk_call_signature():
    actor = MistralActor("ministral-8b-latest", api_key="k")
    client = MagicMock()
    client.chat.complete.return_value = chat_response("42")
    with patch.object(actor, "_client", return_value=client):
        result = actor.run("q")
    # Mistral's SDK is chat.complete(), not chat.completions.create().
    client.chat.complete.assert_called_once()
    assert result.reply == "42"


def test_api_errors_propagate_rather_than_scoring_as_a_wrong_answer():
    actor = MistralActor("ministral-8b-latest", api_key="k")
    client = MagicMock()
    client.chat.complete.side_effect = RuntimeError("401 unauthorized")
    with patch.object(actor, "_client", return_value=client):
        with pytest.raises(RuntimeError):
            actor.run("q")


def test_api_key_is_read_from_environment_when_not_passed(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "from-env")
    assert MistralActor("ministral-8b-latest").api_key == "from-env"


# --------------------------------------------------------------------------
# the tool loop
# --------------------------------------------------------------------------


def tool_call(code, call_id="call_1"):
    """A fake function-calling tool call asking to run `code`.

    Arguments are a JSON string, as the APIs send them.
    """
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name="run_python", arguments=json.dumps({"code": code})
        ),
    )


def test_tool_loop_runs_requested_code_and_feeds_output_back():
    actor = MistralActor("m", api_key="k", cwd="/repo")
    client = MagicMock()
    asked = chat_response(None)
    asked.choices[0].message.tool_calls = [tool_call("print(444)")]
    answered = chat_response("There are 444 sessions.")
    answered.choices[0].message.tool_calls = None
    client.chat.complete.side_effect = [asked, answered]

    with patch.object(actor, "_client", return_value=client), patch(
        "tests.evals.actors.tools.run_python", return_value="444"
    ) as run:
        result = actor.run("how many sessions?")

    run.assert_called_once_with("print(444)", "/repo")
    # The tool output must reach the model: second call carries it in history.
    history = client.chat.complete.call_args_list[1].kwargs["messages"]
    assert history[-1]["content"] == "444"
    assert history[-1]["role"] == "tool"
    assert result.reply == "There are 444 sessions."


def test_tool_loop_sums_tokens_and_counts_turns():
    actor = MistralActor("m", api_key="k", cwd="/repo")
    client = MagicMock()
    first = chat_response(None, prompt_tokens=100, completion_tokens=20)
    first.choices[0].message.tool_calls = [tool_call("print(1)")]
    second = chat_response("done", prompt_tokens=150, completion_tokens=30)
    second.choices[0].message.tool_calls = None
    client.chat.complete.side_effect = [first, second]

    with patch.object(actor, "_client", return_value=client), patch(
        "tests.evals.actors.tools.run_python", return_value="1"
    ):
        usage = actor.run("q").usage

    assert usage.input_tokens == 250
    assert usage.output_tokens == 50
    assert usage.num_turns == 2


def test_no_tools_offered_when_actor_has_no_cwd():
    actor = MistralActor("m", api_key="k")  # no cwd -> no code execution
    client = MagicMock()
    response = chat_response("I cannot run code.")
    response.choices[0].message.tool_calls = None
    client.chat.complete.return_value = response

    with patch.object(actor, "_client", return_value=client):
        actor.run("q")

    assert "tools" not in client.chat.complete.call_args.kwargs


def test_tool_loop_stops_at_max_turns():
    # A model that only ever calls tools must not loop forever.
    actor = MistralActor("m", api_key="k", cwd="/repo", max_turns=3)
    client = MagicMock()
    forever = chat_response(None)
    forever.choices[0].message.tool_calls = [tool_call("print(1)")]
    client.chat.complete.return_value = forever

    with patch.object(actor, "_client", return_value=client), patch(
        "tests.evals.actors.tools.run_python", return_value="1"
    ):
        result = actor.run("q")

    assert client.chat.complete.call_count == 3
    assert result.usage.num_turns == 3


def test_stop_reason_distinguishes_answering_from_giving_up():
    # Three outcomes that must not look alike in the results table.
    actor = MistralActor("m", api_key="k", cwd="/repo")

    answered = chat_response("the answer", finish_reason="stop")
    client = MagicMock()
    client.chat.complete.return_value = answered
    with patch.object(actor, "_client", return_value=client):
        assert actor.run("q").usage.stop_reason == "answered"

    # Observed with google/gemini-2.5-flash on a long prompt: the provider
    # rejects the model's own tool call, so nothing usable comes back.
    broken = chat_response(None, finish_reason="malformed_function_call")
    client.chat.complete.return_value = broken
    with patch.object(actor, "_client", return_value=client):
        assert actor.run("q").usage.stop_reason == "malformed_function_call"


def test_stop_reason_is_max_turns_when_the_loop_never_converges():
    actor = MistralActor("m", api_key="k", cwd="/repo", max_turns=2)
    client = MagicMock()
    forever = chat_response(None, tool_calls=[tool_call("print(1)")], finish_reason="tool_calls")
    client.chat.complete.return_value = forever
    with patch.object(actor, "_client", return_value=client), patch(
        "tests.evals.actors.tools.run_python", return_value="1"
    ):
        assert actor.run("q").usage.stop_reason == "max_turns"


def test_malformed_tool_arguments_become_failing_code_not_a_crash():
    actor = MistralActor("m", api_key="k", cwd="/repo")
    client = MagicMock()
    broken = chat_response(None)
    broken.choices[0].message.tool_calls = [
        SimpleNamespace(
            id="c1", function=SimpleNamespace(name="run_python", arguments="{not json")
        )
    ]
    answered = chat_response("sorry")
    answered.choices[0].message.tool_calls = None
    client.chat.complete.side_effect = [broken, answered]

    with patch.object(actor, "_client", return_value=client), patch(
        "tests.evals.actors.tools.run_python", return_value="err"
    ) as run:
        actor.run("q")

    assert "not valid JSON" in run.call_args.args[0]


def test_lightning_tool_loop_uses_openai_message_shapes():
    actor = LightningActor("openai/gpt-5-nano", api_key="k", cwd="/repo")
    client = MagicMock()
    asked = chat_response(None)
    asked.choices[0].message.tool_calls = [tool_call("print(1)")]
    answered = chat_response("1")
    answered.choices[0].message.tool_calls = None
    client.chat.completions.create.side_effect = [asked, answered]

    with patch.object(actor, "_client", return_value=client), patch(
        "tests.evals.actors.tools.run_python", return_value="1"
    ):
        actor.run("q")

    history = client.chat.completions.create.call_args_list[1].kwargs["messages"]
    assistant = history[1]
    assert assistant["tool_calls"][0]["type"] == "function"  # OpenAI requires type
    assert history[2] == {"role": "tool", "tool_call_id": "call_1", "content": "1"}


# --------------------------------------------------------------------------
# claude actor: usage mapping
# --------------------------------------------------------------------------


def test_claude_usage_mapping_includes_cache_tokens_and_cost():
    message = SimpleNamespace(
        usage={
            "input_tokens": 42,
            "output_tokens": 2554,
            "cache_read_input_tokens": 93019,
            "cache_creation_input_tokens": 5153,
        },
        total_cost_usd=0.0335,
        num_turns=6,
    )
    usage = ClaudeSDKActor._usage_from(message)
    assert (usage.input_tokens, usage.output_tokens) == (42, 2554)
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (93019, 5153)
    assert usage.cost_usd == 0.0335
    assert usage.num_turns == 6


def test_claude_usage_handles_missing_cost_and_fields():
    # A subscription session can report no cost; that must read as 0.0, which
    # means "not reported", not "free".
    message = SimpleNamespace(usage={}, total_cost_usd=None, num_turns=1)
    usage = ClaudeSDKActor._usage_from(message)
    assert usage.cost_usd == 0.0
    assert usage.input_tokens == 0
