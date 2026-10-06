"""Claude actor: the local Claude Code agent, via claude-agent-sdk.

Authentication comes from the local `claude login` session, exactly as in
scripts/scratch_query_claude_agent.py. On CI the same code path works with
ANTHROPIC_API_KEY set instead -- nothing here changes.

This is the only actor that can run code, so it is the only one that answers a
data-loading question by actually loading the data.
"""

from __future__ import annotations

import anyio

from .base import CodeExecutingActor, RunResult, Usage

MODEL_ALIASES = {
    "opus": "claude-opus-5",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5",
}

# Enough to read the dataset and run Python; no network tools, because the
# answer must come from the local data.
ALLOWED_TOOLS = ["Bash", "Read", "Glob", "Grep", "Write"]

SYSTEM_PROMPT = (
    "You are answering a data-loading question about the local IBL BWM datasets. "
    "Run Python with: UV_CACHE_DIR=.uv-cache uv run --no-sync python. "
    "Dataset locations are configured in data_locations.local.yaml and resolve "
    "with ibl_ai_agent.data_locations.resolve_dataset_dir."
)


class ClaudeSDKActor(CodeExecutingActor):
    """Runs a question through a local Claude Code agent session."""

    def __init__(self, model, cwd, name=None, max_turns=30):
        super().__init__(MODEL_ALIASES.get(model, model), cwd, name or model, max_turns)

    def run(self, prompt):
        return anyio.run(self._run_async, prompt)

    async def _run_async(self, prompt):
        # Imported here so that unit tests can import this module, and patch
        # this function, without the SDK's CLI being installed.
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            query,
        )

        options = ClaudeAgentOptions(
            model=self.model,
            cwd=str(self.cwd),
            allowed_tools=ALLOWED_TOOLS,
            permission_mode="acceptEdits",
            system_prompt=SYSTEM_PROMPT,
            max_turns=self.max_turns,
        )
        texts, usage = [], Usage()
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                texts += [b.text for b in message.content if isinstance(b, TextBlock)]
            elif isinstance(message, ResultMessage):
                usage = self._usage_from(message)
        return RunResult(reply="\n".join(texts), usage=usage)

    @staticmethod
    def _usage_from(message):
        """Map a ResultMessage onto Usage.

        cost_usd is the CLI's own estimate from token counts and list prices.
        On a subscription it is API-equivalent cost, not an amount billed.
        """
        raw = message.usage or {}
        return Usage(
            input_tokens=raw.get("input_tokens", 0),
            output_tokens=raw.get("output_tokens", 0),
            cache_read_tokens=raw.get("cache_read_input_tokens", 0),
            cache_write_tokens=raw.get("cache_creation_input_tokens", 0),
            cost_usd=message.total_cost_usd or 0.0,
            num_turns=message.num_turns,
            stop_reason="answered",
        )
