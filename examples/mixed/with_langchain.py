"""Mixed: LangChain agent with both AgentCore and self-hosted providers.

Demonstrates provider routing and JWT authentication. Default providers
are self-hosted, with AgentCore available via per-request overrides.

Prerequisites:
    pip install agentic-primitives-gateway-client[aws] langchain langchain-aws
    export AGENTCORE_MEMORY_ID=memory_xxxx  # from AWS console (for AgentCore memory backend)
    # Gateway running: ./run.sh mixed
    # All infrastructure running + OIDC provider configured

Usage:
    export OIDC_ISSUER=https://keycloak.example.com/realms/my-realm
    export OIDC_USERNAME=myuser
    export OIDC_PASSWORD=mypassword
    python examples/mixed/with_langchain.py
"""

from __future__ import annotations

import asyncio
import os

from langchain.agents import create_agent
from langchain_aws import ChatBedrock
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage, SystemMessage

from agentic_primitives_gateway_client import AgenticPlatformClient, Memory, Observability, fetch_token_from_env

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

    # Authenticate via JWT
    token = fetch_token_from_env()
    if token:
        client.set_auth_token(token)
        print("Authenticated via JWT\n")
    else:
        print("Warning: no JWT configured — running without auth\n")

    # The Memory client provides automatic context injection (RAG-style):
    # - recall_context() searches memory and injects results into the system prompt
    # - store_turn() auto-saves each conversation turn
    # Compare with the Strands example which uses tools-only — the agent decides
    # when to use memory. Both approaches are valid; this one guarantees relevant
    # context is always present without relying on the LLM to search proactively.
    memory = Memory(client, namespace="agent:langchain-mixed")
    obs = Observability(client, namespace="agent:langchain-mixed")

    # Build tools with default providers (self-hosted)
    tools = await client.get_tools(
        ["memory", "browser", "code_interpreter"],
        namespace="agent:langchain-mixed",
        format="langchain",
    )
    print(f"Loaded {len(tools)} tools (default providers): {[t.name for t in tools]}")

    # Demonstrate provider switching:
    #   client.set_provider_for("memory", "agentcore")
    # After this, all memory calls route to AgentCore instead of mem0.
    # Reset with: client.set_provider_for("memory", "mem0")

    llm = ChatBedrock(
        model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
        region_name="us-east-1",
    )

    agent = create_agent(llm, tools=tools)

    system_prompt = (
        "You are a capable assistant with long-term memory, a web browser, "
        "and a code execution environment.\n\n"
        "Your infrastructure is configurable — the same tools work whether "
        "backed by AWS managed services or self-hosted open-source backends.\n\n"
        "Always check memory before saying you don't know something."
    )

    print("\nLangChain agent (mixed providers). Type 'quit' to exit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input or user_input.lower() in ("quit", "exit"):
            break

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
