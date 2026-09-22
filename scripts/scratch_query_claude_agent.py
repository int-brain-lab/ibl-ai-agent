import argparse
import asyncio

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query

MODELS = {
    "opus": "claude-opus-5",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5",
}


async def main(prompt: str, model: str) -> None:
    """Stream a Claude response using the local subscription session.

    Parameters
    ----------
    prompt : str
        User prompt sent to the model.
    model : str
        Model ID or alias (opus, sonnet, haiku).
    """
    model_id = MODELS.get(model, model)
    options = ClaudeAgentOptions(model=model_id)

    print(f"--- Using your Claude Subscription ({model_id}) ---")
    print("Note: Ensure you have run 'claude login' in your terminal.")

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(block.text, end="", flush=True)
        elif isinstance(message, ResultMessage):
            print(f"\n\n--- Usage: {message.usage} ---")
            print(f"--- Cost: ${message.total_cost_usd:.4f} ---")
            # message.model_usage also gives a per-model breakdown (inputTokens,
            # outputTokens, cacheReadInputTokens, costUSD, ...) when several
            # models were involved in the query.

    print("\n--- Done ---")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        default="sonnet",
        help=f"Model alias ({', '.join(MODELS)}) or a full model ID",
    )
    parser.add_argument(
        "--prompt",
        default="Explain the difference between synchronous and asynchronous code in 2 sentences.",
    )
    args = parser.parse_args()
    asyncio.run(main(args.prompt, args.model))

