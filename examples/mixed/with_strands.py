"""Mixed: Strands agent with both AgentCore and self-hosted providers.

Demonstrates provider routing — the same agent can switch between
backends per-request via headers. Default is self-hosted (mem0, Selenium,
Jupyter), with AgentCore available via X-Provider-* headers.

Also demonstrates JWT authentication for multi-user deployments.

Prerequisites:
    pip install agentic-primitives-gateway-client[aws] strands-agents
    export AGENTCORE_MEMORY_ID=memory_xxxx  # from AWS console (for AgentCore memory backend)
    # Gateway running: ./run.sh mixed
    # All infrastructure running (Milvus, Langfuse, Selenium, Jupyter, Redis)
    # OIDC provider configured (JWT_ISSUER set)

Usage:
    export OIDC_ISSUER=https://keycloak.example.com/realms/my-realm
    export OIDC_USERNAME=myuser
    export OIDC_PASSWORD=mypassword
    python examples/mixed/with_strands.py
"""

from __future__ import annotations

import os

from strands import Agent

from agentic_primitives_gateway_client import AgenticPlatformClient, Observability, fetch_token_from_env

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")


def main():
    client = AgenticPlatformClient(GATEWAY_URL, aws_from_environment=True)

    # Authenticate via JWT
    token = fetch_token_from_env()
    if token:
        client.set_auth_token(token)
        print("Authenticated via JWT\n")
    else:
        print("Warning: no JWT configured — running without auth\n")

    obs = Observability(client, namespace="agent:strands-mixed")

    # Build tools with default providers (self-hosted)
    tools = client.get_tools_sync(
        ["memory", "browser", "code_interpreter"],
        namespace="agent:strands-mixed",
        format="strands",
    )
    print(f"Loaded {len(tools)} tools (default providers): {[t.tool_name for t in tools]}")

    # You can also override providers per-request:
    #   client.set_provider_for("memory", "agentcore")  → switch memory to AgentCore
    #   client.set_provider_for("browser", "agentcore")  → switch browser to AgentCore
    # Or per-request via headers in curl:
    #   curl -H "X-Provider-Memory: agentcore" ...

    agent = Agent(
        model="us.anthropic.claude-sonnet-4-20250514-v1:0",
        system_prompt=(
            "You are a capable assistant with long-term memory, a web browser, "
            "and a code execution environment.\n\n"
            "Your infrastructure is configurable — the same tools work whether "
            "backed by AWS managed services or self-hosted open-source backends.\n\n"
            "Use memory to persist information, browser to research, and "
            "execute_code for Python. Always check memory first."
        ),
        tools=tools,
    )

    print("\nStrands agent (mixed providers). Type 'quit' to exit.\n")

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
