"""E2E example: LangChain agent with mixed providers.

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
    python agent_langchain.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from e2e_helpers import cedar_cleanup_async, cedar_demo_async
from langchain.agents import create_agent
from langchain_aws import ChatBedrock
from langchain_core.tools import tool

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

AGENT_NAMESPACE = "agent:langchain-e2e-mixed"
SESSION_ID = f"session-{uuid4().hex[:8]}"

obs = Observability(
    platform,
    namespace=AGENT_NAMESPACE,
    session_id=SESSION_ID,
    tags=["langchain-agent", "e2e-mixed", "mem0", "langfuse", "agentcore"],
)

memory = Memory(platform, namespace=AGENT_NAMESPACE, session_id=SESSION_ID, observability=obs)

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


async def main():
    print("LangChain E2E mixed-provider agent ready.")
    print("  Memory:        mem0 + Milvus (self-hosted)")
    print("  Observability: Langfuse (self-hosted)")
    print("  Identity:      AgentCore (AWS)")
    print("  Code/Browser:  AgentCore (AWS)")
    print("  Tools:         AgentCore (AWS)")
    print(f"\nConnected to platform at {GATEWAY_URL}")
    print(f"Session: {SESSION_ID}")
    print()

    # ── Cedar enforcement demo ──────────────────────────────────
    cedar_engine_id, cedar_policy_id = await cedar_demo_async(GATEWAY_URL, AGENT_NAMESPACE, auth_token)

    # Fetch tools from gateway catalog (after Cedar policy is active)
    tools = [
        tool(fn)
        for fn in await platform.get_tools(
            ["memory", "browser", "code_interpreter", "identity", "tools"],
            namespace=AGENT_NAMESPACE,
        )
    ]
    print(f"  Tools: {len(tools)} loaded from gateway catalog")

    model = ChatBedrock(model_id="us.anthropic.claude-sonnet-4-20250514-v1:0")

    agent = create_agent(
        model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
    )

    print("\nType 'quit' to exit.\n")

    await obs.log("info", "E2E mixed-provider agent started", framework="langchain")

    history: list[dict[str, str]] = []

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input or user_input.lower() in ("quit", "exit"):
            break

        # Auto-memory: store the user's turn
        await memory.store_turn("user", user_input)

        # Recall relevant context
        context = await memory.recall_context(user_input)

        # Build prompt with memory context
        system_prompt = SYSTEM_PROMPT
        if context:
            system_prompt += "\n\nRelevant context from past conversations:\n" + context

        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}, *history]
        messages.append({"role": "user", "content": user_input})

        # Stream the response
        sys.stdout.write("\nAssistant: ")
        sys.stdout.flush()
        reply_chunks: list[str] = []
        async for event in agent.astream_events({"messages": messages}, version="v2"):
            if event["event"] == "on_chat_model_stream":
                chunk = event["data"].get("chunk")
                if chunk and hasattr(chunk, "content"):
                    content = chunk.content
                    if isinstance(content, str) and content:
                        sys.stdout.write(content)
                        sys.stdout.flush()
                        reply_chunks.append(content)
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("text"):
                                sys.stdout.write(block["text"])
                                sys.stdout.flush()
                                reply_chunks.append(block["text"])

        reply = "".join(reply_chunks)
        sys.stdout.write("\n\n")
        sys.stdout.flush()

        if reply:
            history.append({"role": "user", "content": user_input})
            history.append({"role": "assistant", "content": reply})

            # Auto-memory: store the assistant's turn
            await memory.store_turn("assistant", reply)

            # Trace the full conversation turn
            await obs.trace(
                "conversation:turn",
                {"user": user_input, "context_retrieved": bool(context)},
                reply[:500],
                tags=["conversation"],
            )
        else:
            print("\nAssistant: (no response)\n")

    await obs.flush()
    await obs.log("info", "E2E mixed-provider agent stopped", framework="langchain")

    # Clean up Cedar policy (restores default-deny)
    await cedar_cleanup_async(GATEWAY_URL, cedar_engine_id, cedar_policy_id, auth_token)


if __name__ == "__main__":
    asyncio.run(main())

# ═══════════════════════════════════════════════════════════════════════
# Example prompts to showcase mixed-provider routing:
#
# Memory (mem0 + Milvus -- self-hosted):
#   "Remember that our deployment uses Kubernetes with 3 replicas"
#   "Search memory for anything about deployments"
#   "List all memories"
#
# Identity (AgentCore -- AWS-managed):
#   "Get a Slack token to send notifications"
#   "List available credential providers"
#
# Code Interpreter (AgentCore -- AWS-managed):
#   "Write Python to fetch and parse JSON from https://httpbin.org/json"
#   "Create a matplotlib chart showing a sine wave"
#
# Browser (AgentCore -- AWS-managed):
#   "Open a browser, navigate to https://example.com, read the page"
#   "Take a screenshot and close the browser"
#
# Tools (AgentCore MCP -- AWS-managed):
#   "Search for tools that can query databases"
#   "List all registered tools"
#
# Cross-primitive workflows:
#   "Research machine learning frameworks: check memory, browse the web,
#    write comparison code, store findings, and trace everything"
#   "Get an API key, use code to call the API, store the results in
#    memory, then browse the docs to verify the response format"
# ═══════════════════════════════════════════════════════════════════════
