"""Self-hosted: Strands agent with open-source backends.

Demonstrates memory (mem0/Milvus), browser (Selenium Grid),
code execution (Jupyter), and observability (Langfuse) — all
self-hosted with only Bedrock for LLM inference.

Prerequisites:
    pip install agentic-primitives-gateway-client[aws] strands-agents
    # Gateway running: ./run.sh selfhosted
    # Milvus, Langfuse, Selenium Grid, Jupyter running

Usage:
    python examples/selfhosted/with_strands.py
"""

from __future__ import annotations

import os

from strands import Agent

from agentic_primitives_gateway_client import AgenticPlatformClient, Observability

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")


def main():
    client = AgenticPlatformClient(GATEWAY_URL, aws_from_environment=True)

    # Optional: set Langfuse credentials for observability
    if os.environ.get("LANGFUSE_PUBLIC_KEY"):
        client.set_service_credentials(
            "langfuse",
            {
                "public_key": os.environ["LANGFUSE_PUBLIC_KEY"],
                "secret_key": os.environ["LANGFUSE_SECRET_KEY"],
            },
        )

    obs = Observability(client, namespace="agent:strands-selfhosted")

    # Build tools — mem0 memory has semantic search (unlike in-memory),
    # Selenium browser can actually navigate, Jupyter can execute code
    tools = client.get_tools_sync(
        ["memory", "browser", "code_interpreter"],
        namespace="agent:strands-selfhosted",
        format="strands",
    )
    print(f"Loaded {len(tools)} tools: {[t.tool_name for t in tools]}\n")

    agent = Agent(
        model="us.anthropic.claude-sonnet-4-20250514-v1:0",
        system_prompt=(
            "You are a research assistant with long-term memory (backed by Milvus "
            "vector search), a web browser (Selenium Grid), and a code execution "
            "environment (Jupyter).\n\n"
            "**Memory:** remember stores facts, search_memory finds them via semantic "
            "similarity (not just substring match), recall gets by exact key.\n"
            "**Browser:** navigate to URLs, read_page to extract content. Use "
            "DuckDuckGo for searches. If you hit a CAPTCHA, try a different site.\n"
            "**Code:** execute_code runs Python in a persistent Jupyter kernel. "
            "Variables survive across calls.\n\n"
            "Always search memory before saying you don't know something."
        ),
        tools=tools,
    )

    print("Strands agent (self-hosted). Type 'quit' to exit.\n")

    while True:
        user_input = input("You: ").strip()
        if not user_input or user_input.lower() in ("quit", "exit"):
            break

        print("Assistant: ", end="", flush=True)
        result = agent(user_input)
        print("\n")

        obs.trace_sync("conversation:turn", {"user": user_input}, str(result))


if __name__ == "__main__":
    main()
