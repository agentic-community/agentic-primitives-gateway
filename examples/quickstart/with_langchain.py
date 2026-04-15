"""Quickstart: LangChain agent using gateway primitives.

The model is routed through the gateway — the operator controls which
LLM provider and model is used via gateway config. The gateway also
provides memory as tools. LangChain handles the agent loop (including
automatic tool call execution).

Prerequisites:
    pip install agentic-primitives-gateway-client[aws] langchain langchain-core
    # Gateway running at localhost:8000 (./run.sh)

Usage:
    python examples/quickstart/with_langchain.py
"""

from __future__ import annotations

import asyncio
import os

from langchain.agents import create_agent
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from agentic_primitives_gateway_client import AgenticPlatformClient, Memory

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
    memory = Memory(client, namespace="agent:langchain-demo")

    # Wrap gateway primitives as LangChain tools
    @tool
    async def remember(key: str, content: str) -> str:
        """Store information in long-term memory."""
        return await memory.remember(key, content)

    @tool
    async def search_memory(query: str) -> str:
        """Search memory for relevant information."""
        return await memory.search(query)

    @tool
    async def recall(key: str) -> str:
        """Retrieve a specific memory by key."""
        return await memory.recall(key)

    @tool
    async def list_memories() -> str:
        """List all stored memories. Use this to see everything you remember."""
        records = await memory._client.list_memories(memory.namespace, limit=20)
        items = records.get("records", [])
        if not items:
            return "No memories stored."
        return "\n".join(f"- {r['key']}: {r['content']}" for r in items)

    # Model routed through the gateway — operator controls the actual provider/model
    llm = client.get_model(format="langchain")

    agent = create_agent(
        llm,
        tools=[remember, search_memory, recall, list_memories],
    )

    system_prompt = "You are a helpful assistant. Use memory to remember and recall things."

    print("LangChain agent with gateway memory. Type 'quit' to exit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input or user_input.lower() in ("quit", "exit"):
            break

        result = await agent.ainvoke(
            {
                "messages": [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_input),
                ]
            },
            config={"callbacks": [ToolTracer()]},
        )

        # Extract the final AI response
        for msg in reversed(result.get("messages", [])):
            if hasattr(msg, "type") and msg.type == "ai" and msg.content:
                content = msg.content
                if isinstance(content, list):
                    reply = " ".join(
                        block.get("text", "") for block in content if isinstance(block, dict) and block.get("text")
                    )
                else:
                    reply = str(content)
                print(f"\nAssistant: {reply}\n")
                break


if __name__ == "__main__":
    asyncio.run(main())
