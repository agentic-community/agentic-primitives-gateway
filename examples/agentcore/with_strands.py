"""AgentCore: Strands agent using all AWS managed primitives.

Demonstrates memory, browser, code execution, identity, tools,
and observability — all via AgentCore + Bedrock.

Prerequisites:
    pip install agentic-primitives-gateway-client[aws] strands-agents
    export AGENTCORE_MEMORY_ID=memory_xxxx  # from AWS console
    # Gateway running: ./run.sh agentcore

Usage:
    python examples/agentcore/with_strands.py
"""

from __future__ import annotations

import os

from strands import Agent

from agentic_primitives_gateway_client import AgenticPlatformClient, Observability

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")


def main():
    client = AgenticPlatformClient(GATEWAY_URL, aws_from_environment=True)
    obs = Observability(client, namespace="agent:strands-agentcore")

    # Build tools for all available primitives
    tools = client.get_tools_sync(
        ["memory", "browser", "code_interpreter"],
        namespace="agent:strands-agentcore",
        format="strands",
    )
    print(f"Loaded {len(tools)} tools: {[t.tool_name for t in tools]}\n")

    agent = Agent(
        model="us.anthropic.claude-sonnet-4-20250514-v1:0",
        system_prompt=(
            "You are a capable assistant with long-term memory, a web browser, "
            "and a code execution environment.\n\n"
            "Use memory tools (remember, recall, search_memory, list_memories) to "
            "persist information across conversations.\n"
            "Use browser tools (navigate, read_page, click) to research the web.\n"
            "Use execute_code to run Python code and verify results.\n\n"
            "Always search memory before saying you don't know something."
        ),
        tools=tools,
    )

    print("Strands agent (AgentCore). Type 'quit' to exit.\n")

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
