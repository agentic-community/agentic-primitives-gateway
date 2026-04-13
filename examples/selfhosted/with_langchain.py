"""Self-hosted: LangChain agent with open-source backends.

Demonstrates memory (mem0/Milvus), browser (Selenium Grid),
code execution (Jupyter), and observability (Langfuse) — all
self-hosted with only Bedrock for LLM inference.

Prerequisites:
    pip install agentic-primitives-gateway-client[aws] langchain langchain-aws
    # Gateway running: ./run.sh selfhosted
    # Milvus, Langfuse, Selenium Grid, Jupyter running

Usage:
    python examples/selfhosted/with_langchain.py
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

    # Optional: set Langfuse credentials
    if os.environ.get("LANGFUSE_PUBLIC_KEY"):
        client.set_service_credentials(
            "langfuse",
            {
                "public_key": os.environ["LANGFUSE_PUBLIC_KEY"],
                "secret_key": os.environ["LANGFUSE_SECRET_KEY"],
            },
        )

    # The Memory client provides automatic context injection (RAG-style):
    # - recall_context() searches memory and injects results into the system prompt
    # - store_turn() auto-saves each conversation turn
    # Compare with the Strands example which uses tools-only — the agent decides
    # when to use memory. Both approaches are valid; this one guarantees relevant
    # context is always present without relying on the LLM to search proactively.
    memory = Memory(client, namespace="agent:langchain-selfhosted")
    obs = Observability(client, namespace="agent:langchain-selfhosted")

    # Build tools — with selfhosted backends, search_memory does real
    # semantic search via Milvus, browser uses Selenium, code uses Jupyter
    tools = await client.get_tools(
        ["memory", "browser", "code_interpreter"],
        namespace="agent:langchain-selfhosted",
        format="langchain",
    )
    print(f"Loaded {len(tools)} tools: {[t.name for t in tools]}\n")

    llm = ChatBedrock(
        model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
        region_name="us-east-1",
    )

    agent = create_agent(llm, tools=tools)

    system_prompt = (
        "You are a research assistant with long-term memory (Milvus vector search), "
        "a web browser (Selenium Grid), and a code execution environment (Jupyter).\n\n"
        "Use search_memory for semantic search — it works with natural language queries.\n"
        "Use browser tools to research the web. Use DuckDuckGo for searches.\n"
        "Use execute_code for Python with persistent state.\n\n"
        "Always search memory before saying you don't know something."
    )

    print("LangChain agent (self-hosted). Type 'quit' to exit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input or user_input.lower() in ("quit", "exit"):
            break

        # Memory context injection
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

        for msg in reversed(result.get("messages", [])):
            if hasattr(msg, "type") and msg.type == "ai" and msg.content:
                content = msg.content
                reply = (
                    " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("text"))
                    if isinstance(content, list)
                    else str(content)
                )
                print(f"\nAssistant: {reply}\n")
                await memory.store_turn(user_input, reply)
                await obs.trace("conversation:turn", {"user": user_input}, reply)
                break


if __name__ == "__main__":
    asyncio.run(main())
