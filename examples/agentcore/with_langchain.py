"""AgentCore: LangChain agent using all AWS managed primitives.

Demonstrates memory, browser, code execution, identity, tools,
and observability — all via AgentCore + Bedrock.

Prerequisites:
    pip install agentic-primitives-gateway-client[aws] langchain langchain-aws
    export AGENTCORE_MEMORY_ID=memory_xxxx  # from AWS console
    # Gateway running: ./run.sh agentcore

Usage:
    python examples/agentcore/with_langchain.py
"""

from __future__ import annotations

import asyncio
import os

from langchain.agents import create_agent
from langchain_aws import ChatBedrock
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage, SystemMessage

from agentic_primitives_gateway_client import AgenticPlatformClient, Memory, Observability

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")


class ToolTracer(BaseCallbackHandler):
    """Print tool calls as they happen, similar to Strands output."""

    def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        name = serialized.get("name", "?")
        print(f"  [tool] {name}")

    def on_tool_end(self, output, *, run_id, **kwargs):
        short = str(output)[:120]
        print(f"  [result] {short}")


async def main():
    client = AgenticPlatformClient(GATEWAY_URL, aws_from_environment=True)

    # The Memory client provides automatic context injection (RAG-style):
    # - recall_context() searches memory and injects results into the system prompt
    # - store_turn() auto-saves each conversation turn
    # Compare with the Strands example which uses tools-only — the agent decides
    # when to use memory. Both approaches are valid; this one guarantees relevant
    # context is always present without relying on the LLM to search proactively.
    memory = Memory(client, namespace="agent:langchain-agentcore")
    obs = Observability(client, namespace="agent:langchain-agentcore")

    # Build tools for all available primitives
    tools = await client.get_tools(
        ["memory", "browser", "code_interpreter"],
        namespace="agent:langchain-agentcore",
        format="langchain",
    )
    print(f"Loaded {len(tools)} tools: {[t.name for t in tools]}\n")

    llm = ChatBedrock(
        model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
        region_name="us-east-1",
    )

    agent = create_agent(llm, tools=tools)

    system_prompt = (
        "You are a capable assistant with long-term memory, a web browser, "
        "and a code execution environment.\n\n"
        "Use memory tools to persist information across conversations.\n"
        "Use browser tools to research the web.\n"
        "Use execute_code to run Python code and verify results.\n\n"
        "Always check memory (list_memories or search_memory) before saying "
        "you don't know something."
    )

    print("LangChain agent (AgentCore). Type 'quit' to exit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input or user_input.lower() in ("quit", "exit"):
            break

        # Inject memory context into the prompt
        context = await memory.recall_context(user_input)
        full_prompt = system_prompt
        if context:
            full_prompt += f"\n\nRelevant context from memory:\n{context}"

        result = await agent.ainvoke(
            {
                "messages": [
                    SystemMessage(content=full_prompt),
                    HumanMessage(content=user_input),
                ]
            },
            config={"callbacks": [ToolTracer()]},
        )

        # Extract final response
        for msg in reversed(result.get("messages", [])):
            if hasattr(msg, "type") and msg.type == "ai" and msg.content:
                content = msg.content
                if isinstance(content, list):
                    reply = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("text"))
                else:
                    reply = str(content)
                print(f"\nAssistant: {reply}\n")
                await memory.store_turn(user_input, reply)
                await obs.trace("conversation:turn", {"user": user_input}, reply)
                break


if __name__ == "__main__":
    asyncio.run(main())
