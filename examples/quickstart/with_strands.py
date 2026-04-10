"""Quickstart: Strands agent using gateway primitives.

The gateway client's get_tools_sync() auto-builds tool functions from
the server's tool catalog. Pass them directly to Strands — no manual
tool wrapping needed.

Prerequisites:
    pip install agentic-primitives-gateway-client[aws] strands-agents strands-agents-tools-mcp
    # Gateway running at localhost:8000 (./run.sh selfhosted)

Usage:
    python examples/quickstart/with_strands.py
"""

from __future__ import annotations

import os

from strands import Agent

from agentic_primitives_gateway_client import AgenticPlatformClient

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")


def main():
    client = AgenticPlatformClient(GATEWAY_URL, aws_from_environment=True)

    # Auto-build tools from the gateway's tool catalog
    # These are sync callables with proper names, docstrings, and type hints
    tools = client.get_tools_sync(
        ["memory"],
        namespace="agent:strands-demo",
    )
    print(f"Loaded {len(tools)} tools from gateway: {[t.__name__ for t in tools]}\n")

    # Create Strands agent with gateway tools
    agent = Agent(
        model="us.anthropic.claude-sonnet-4-20250514-v1:0",
        system_prompt="You are a helpful assistant. Use memory to remember and recall things.",
        tools=tools,
    )

    print("Strands agent with gateway tools. Type 'quit' to exit.\n")

    while True:
        user_input = input("You: ").strip()
        if not user_input or user_input.lower() in ("quit", "exit"):
            break

        response = agent(user_input)
        print(f"\nAssistant: {response}\n")


if __name__ == "__main__":
    main()
