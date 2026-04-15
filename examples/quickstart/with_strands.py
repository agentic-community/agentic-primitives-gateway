"""Quickstart: Strands agent using gateway primitives.

Both the model and tools are provided by the gateway — the agent code
is completely decoupled from any specific LLM provider or backend.

Prerequisites:
    pip install agentic-primitives-gateway-client[aws] strands-agents
    # Gateway running at localhost:8000 (./run.sh)

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

    # Model routed through the gateway — operator controls the actual provider/model
    model = client.get_model(format="strands")

    # Tools auto-built from the gateway's tool catalog
    tools = client.get_tools_sync(
        ["memory"],
        namespace="agent:strands-demo",
        format="strands",
    )
    print(f"Loaded {len(tools)} tools from gateway: {[t.__name__ for t in tools]}\n")

    agent = Agent(
        model=model,
        system_prompt="You are a helpful assistant. Use memory to remember and recall things. Search your memory before answering",
        tools=tools,
    )

    print("Strands agent with gateway tools. Type 'quit' to exit.\n")

    while True:
        user_input = input("You: ").strip()
        if not user_input or user_input.lower() in ("quit", "exit"):
            break

        print("Assistant: ", end="", flush=True)
        agent(user_input)
        print("\n")


if __name__ == "__main__":
    main()
