"""Quickstart: LangChain agent using gateway primitives.

The gateway provides memory, browser, and code execution as tools.
LangChain handles the agent loop. Swap "langchain" for any framework —
the gateway tools are just async Python functions.

Prerequisites:
    pip install agentic-primitives-gateway-client[aws] langchain langchain-aws
    # Gateway running at localhost:8000 (./run.sh selfhosted)

Usage:
    python examples/quickstart/with_langchain.py
"""

from __future__ import annotations

import asyncio
import os

from langchain_aws import ChatBedrock
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from agentic_primitives_gateway_client import AgenticPlatformClient, Memory

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")


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

    # Create LangChain agent
    llm = ChatBedrock(
        model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
        region_name="us-east-1",
    )
    llm_with_tools = llm.bind_tools([remember, search_memory, recall])

    print("LangChain agent with gateway memory. Type 'quit' to exit.\n")

    messages = [
        SystemMessage(content="You are a helpful assistant. Use memory tools to remember and recall information.")
    ]

    while True:
        user_input = input("You: ").strip()
        if not user_input or user_input.lower() in ("quit", "exit"):
            break

        messages.append(HumanMessage(content=user_input))
        response = await llm_with_tools.ainvoke(messages)
        messages.append(response)

        # Handle tool calls
        while response.tool_calls:
            for tc in response.tool_calls:
                tool_fn = {"remember": remember, "search_memory": search_memory, "recall": recall}[tc["name"]]
                result = await tool_fn.ainvoke(tc["args"])
                print(f"  [tool] {tc['name']} → {result[:80]}")
                from langchain_core.messages import ToolMessage

                messages.append(ToolMessage(content=result, tool_call_id=tc["id"]))
            response = await llm_with_tools.ainvoke(messages)
            messages.append(response)

        print(f"\nAssistant: {response.content}\n")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
