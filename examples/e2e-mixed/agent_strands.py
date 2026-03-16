"""E2E example: Strands agent with mixed providers.

Demonstrates the gateway's per-primitive provider routing -- the "best of
both worlds" pattern:
  - Memory (mem0 + Milvus) -- your data stays in your infrastructure
  - Observability (Langfuse) -- your traces stay in your infrastructure
  - Code/Browser/Identity/Tools (AgentCore) -- heavy compute on AWS
  - JWT authentication, Cedar enforcement, Redis stores (server-side)

Server config:
    ./run.sh e2e-mixed

Prerequisites:
    pip install -r requirements.txt
    docker run -d --name milvus -p 19530:19530 milvusdb/milvus:latest
    Redis running at localhost:6379

Usage:
    export LANGFUSE_PUBLIC_KEY=pk-lf-...
    export LANGFUSE_SECRET_KEY=sk-lf-...
    python agent_strands.py
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

# ── Platform client with per-primitive routing ──────────────────────

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")

platform = AgenticPlatformClient(
    GATEWAY_URL,
    aws_from_environment=True,
)

# JWT authentication (optional -- omit for noop auth)
auth_token = fetch_token_from_env()
if auth_token:
    platform.set_auth_token(auth_token)

# Route each primitive to the right backend
platform.set_provider_for("memory", "mem0")
platform.set_provider_for("observability", "langfuse")
platform.set_provider_for("identity", "agentcore")
platform.set_provider_for("code_interpreter", "agentcore")
platform.set_provider_for("browser", "agentcore")
platform.set_provider_for("tools", "agentcore")

# Langfuse credentials (for observability)
if os.environ.get("LANGFUSE_PUBLIC_KEY"):
    platform.set_service_credentials(
        "langfuse",
        {
            "public_key": os.environ["LANGFUSE_PUBLIC_KEY"],
            "secret_key": os.environ["LANGFUSE_SECRET_KEY"],
            "base_url": os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"),
        },
    )
    print(f"Langfuse: tracing to {os.environ.get('LANGFUSE_BASE_URL', 'cloud.langfuse.com')}")
else:
    print("WARNING: LANGFUSE_PUBLIC_KEY not set -- observability won't reach Langfuse")

AGENT_NAMESPACE = "agent:strands-e2e-mixed"

obs = Observability(
    platform,
    namespace=AGENT_NAMESPACE,
    tags=["strands-agent", "e2e-mixed", "mem0", "langfuse", "agentcore"],
)

memory = Memory(platform, namespace=AGENT_NAMESPACE, observability=obs)

# ── Agent ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a capable research assistant with a mixed-provider setup. Your memory \
is backed by mem0 + Milvus (self-hosted), observability goes through Langfuse \
(self-hosted), and code/browser/identity/tools run on AWS AgentCore (managed).

You have tools for memory (store/search/recall), code execution, web browsing, \
identity/credential management, and external tool invocation.

Always search memory before saying you don't know something. Use the \
code interpreter for calculations. Use the browser for web tasks.
"""


def main():
    model = BedrockModel(
        model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
        streaming=True,
    )

    print("Strands E2E mixed-provider agent ready.")
    print("  Memory:        mem0 + Milvus (self-hosted)")
    print("  Observability: Langfuse (self-hosted)")
    print("  Identity:      AgentCore (AWS)")
    print("  Code/Browser:  AgentCore (AWS)")
    print("  Tools:         AgentCore (AWS)")
    print(f"\nConnected to platform at {GATEWAY_URL}")
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
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
    )

    print("\nType 'quit' to exit.\n")

    obs.log_sync("info", "E2E mixed-provider agent started", framework="strands")

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

        # Recall relevant context
        context = memory.recall_context_sync(user_input)

        response = agent(user_input)

        # Auto-memory: store the response
        response_text = str(response)
        memory.store_turn_sync("assistant", response_text)

        obs.trace_sync(
            "conversation:turn",
            {"user": user_input, "context_retrieved": bool(context)},
            response_text[:500],
            tags=["conversation"],
        )
        print()

    obs.flush_sync()
    obs.log_sync("info", "E2E mixed-provider agent stopped", framework="strands")

    # Clean up Cedar policy (restores default-deny)
    cedar_cleanup_sync(GATEWAY_URL, cedar_engine_id, cedar_policy_id, auth_token)


if __name__ == "__main__":
    main()

# ═══════════════════════════════════════════════════════════════════════
# Example prompts to showcase mixed-provider routing:
#
# Memory (mem0 + Milvus -- self-hosted):
#   "Remember that our API rate limit is 1000 requests per minute"
#   "Search memory for anything about rate limits"
#   "List all memories"
#
# Identity (AgentCore -- AWS-managed):
#   "Get a GitHub token so I can access our repos"
#   "Retrieve the OpenAI API key"
#
# Code Interpreter (AgentCore -- AWS-managed):
#   "Write a Python script to analyze CSV data with pandas"
#   "Install the requests package and fetch https://httpbin.org/json"
#
# Browser (AgentCore -- AWS-managed):
#   "Open a browser, go to https://example.com, and read the page"
#   "Take a screenshot and close the browser"
#
# Tools (AgentCore MCP -- AWS-managed):
#   "Search for tools related to Slack notifications"
#   "List all available tools"
#
# Cross-primitive workflows:
#   "Search memory for past research, browse the web for updates,
#    write code to compare old vs new data, store the results"
#   "Get a GitHub token, use the browser to check our repo's README,
#    remember the key points, then write code to generate a summary"
# ═══════════════════════════════════════════════════════════════════════
