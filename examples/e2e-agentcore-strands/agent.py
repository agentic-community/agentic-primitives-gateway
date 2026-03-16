"""E2E example: Strands agent with all AgentCore primitives.

Demonstrates every gateway feature using AWS-managed backends:
  - All 7 primitives (memory, identity, code, browser, observability, tools, gateway)
  - JWT authentication (Keycloak)
  - Auto-memory (transparent per-turn storage and context recall)
  - Cedar policy enforcement (configured server-side)
  - Redis-backed stores (configured server-side)
  - Declarative agents + teams (configured server-side, use via UI or API)

Server config:
    ./run.sh e2e-agentcore-strands

Prerequisites:
    pip install -r requirements.txt
    Redis running at localhost:6379

Usage:
    # Required: set your AgentCore memory resource ID
    export AGENTCORE_MEMORY_ID=memory_xxxxx

    # Without auth:
    python agent.py

    # With JWT auth:
    export KEYCLOAK_ISSUER=https://your-keycloak/realms/your-realm
    export KEYCLOAK_USERNAME=your-user
    export KEYCLOAK_PASSWORD=your-password
    python agent.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from e2e_helpers import cedar_cleanup_sync, cedar_demo_sync
from strands import Agent, tool
from strands.models import BedrockModel

from agentic_primitives_gateway_client import (
    AgenticPlatformClient,
    Memory,
    Observability,
    fetch_token_from_env,
)

# ── Platform client ────────────────────────────────────────────────────

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")

# JWT authentication (optional — skip if env vars are not set)
auth_token = fetch_token_from_env()

platform = AgenticPlatformClient(
    GATEWAY_URL,
    aws_from_environment=True,
    auth_token=auth_token,
)

# AgentCore memory resource ID (required for memory tools)
# Create one in the AgentCore console, then:
#   export AGENTCORE_MEMORY_ID=memory_xxxxx
memory_id = os.environ.get("AGENTCORE_MEMORY_ID", "")
if not memory_id:
    print("WARNING: AGENTCORE_MEMORY_ID not set — memory tools will fail")
    print("         Create a memory resource in AgentCore and export the ID")
else:
    platform.set_service_credentials("agentcore", {"memory_id": memory_id})

AGENT_NAMESPACE = "agent:e2e-strands"
SESSION_ID = "demo-session"

obs = Observability(
    platform,
    namespace=AGENT_NAMESPACE,
    session_id=SESSION_ID,
    tags=["e2e", "strands", "agentcore"],
)
memory = Memory(
    platform,
    namespace=AGENT_NAMESPACE,
    session_id=SESSION_ID,
    observability=obs,
)

# ── Agent setup ────────────────────────────────────────────────────────

BASE_SYSTEM_PROMPT = """\
You are a research assistant with access to the Agentic Primitives Gateway \
backed by AWS Bedrock AgentCore.

You have tools for memory (store/search/recall), code execution, web browsing, \
identity/credential management, and external tool invocation.

Always search memory before saying you don't know something.
Use the code interpreter for calculations and data processing.
Use the browser for web interactions.
"""


def main() -> None:
    model = BedrockModel(
        model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
        streaming=True,
    )

    print()
    print("=" * 60)
    print("  E2E Strands + AgentCore agent ready")
    print("=" * 60)
    print(f"  Gateway:   {GATEWAY_URL}")
    print(f"  Namespace: {AGENT_NAMESPACE}")
    print(f"  Session:   {SESSION_ID}")
    print(f"  Auth:      {'JWT' if auth_token else 'none'}")
    print("=" * 60)
    print()

    # ── Cedar enforcement demo ──────────────────────────────────
    cedar_engine_id, cedar_policy_id = cedar_demo_sync(GATEWAY_URL, AGENT_NAMESPACE, auth_token)

    # Fetch tools from gateway catalog (after Cedar policy is active)
    tools = [
        tool(fn)
        for fn in platform.get_tools_sync(
            ["memory", "browser", "code_interpreter", "identity", "tools"],
            namespace=AGENT_NAMESPACE,
        )
    ]
    print(f"  Tools: {len(tools)} loaded from gateway catalog")

    agent = Agent(
        model=model,
        system_prompt=BASE_SYSTEM_PROMPT,
        tools=tools,
    )

    print("\nType 'quit' to exit.\n")

    obs.log_sync("info", "E2E agent started", framework="strands")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input or user_input.lower() in ("quit", "exit"):
            break

        # Auto-memory: store the user's turn
        memory.store_turn_sync("user", user_input)

        # Recall relevant context and inject into the system prompt
        context = memory.recall_context_sync(user_input)
        if context:
            agent.system_prompt = BASE_SYSTEM_PROMPT + "\n\nRelevant context from past conversations:\n" + context
        else:
            agent.system_prompt = BASE_SYSTEM_PROMPT

        # Run the agent
        response = agent(user_input)
        response_text = str(response)

        # Auto-memory: store the assistant's response
        memory.store_turn_sync("assistant", response_text)

        # Trace the full conversation turn
        obs.trace_sync(
            "conversation:turn",
            {"user": user_input, "context_retrieved": bool(context)},
            response_text[:500],
            tags=["conversation"],
        )
        print()

    # Clean up sessions
    obs.flush_sync()
    obs.log_sync("info", "E2E agent stopped", framework="strands")

    # Clean up Cedar policy (restores default-deny)
    cedar_cleanup_sync(GATEWAY_URL, cedar_engine_id, cedar_policy_id, auth_token)


if __name__ == "__main__":
    main()

# ═══════════════════════════════════════════════════════════════════════
# Example prompts to showcase each primitive:
#
# Memory (key-value):
#   "Remember that the project deadline is March 30th and the lead is Alice"
#   "What do you know about the project deadline?"
#   "Search your memory for anything about Alice"
#   "List everything you remember"
#   "Forget the project deadline"
#
# Identity:
#   "List all credential providers available"
#   "Get a GitHub token with repo scope"
#
# Code Interpreter:
#   "Write a Python script that generates the first 20 Fibonacci numbers"
#   "Create a CSV file with sample sales data and analyze it with pandas"
#
# Browser:
#   "Open a browser and navigate to https://example.com, then read the page"
#   "Take a screenshot of the current page"
#
# Tools (MCP):
#   "List all registered tools"
#   "Search for tools related to weather"
#
# Multi-primitive workflows:
#   "Research the top 3 Python web frameworks, write a comparison table in
#    code, remember the results, and trace the whole workflow"
#   "Open a browser, go to news.ycombinator.com, read the top stories,
#    store them in memory, then close the browser"
# ═══════════════════════════════════════════════════════════════════════
