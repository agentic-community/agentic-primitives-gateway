"""E2E example: LangChain agent with self-hosted providers.

Demonstrates every gateway feature using open-source backends:
  - Memory via mem0 + Milvus (semantic vector search)
  - Observability via Langfuse (tracing, scoring, LLM generation logging)
  - Code execution via Jupyter (persistent kernel state)
  - Browser automation via Selenium Grid (self-hosted Chrome)
  - Tool registry via MCP Registry (self-hosted)
  - JWT authentication (Keycloak)
  - Auto-memory (transparent per-turn storage and context recall)
  - Cedar policy enforcement (configured server-side)
  - Redis-backed stores (configured server-side)
  - Declarative agents + teams (configured server-side, use via UI or API)

Server config:
    ./run.sh e2e-selfhosted-langchain

Prerequisites:
    pip install -r requirements.txt
    docker run -d --name milvus -p 19530:19530 milvusdb/milvus:latest
    docker run -d --name selenium -p 4444:4444 -p 7900:7900 --shm-size="2g" selenium/standalone-chrome:latest
    docker run -d --name jupyter -p 8888:8888 jupyter/base-notebook:latest
    Redis running at localhost:6379

Usage:
    export LANGFUSE_PUBLIC_KEY=pk-lf-...
    export LANGFUSE_SECRET_KEY=sk-lf-...
    python agent.py
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

# ── Configuration ─────────────────────────────────────────────────────

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")
AGENT_NAMESPACE = "agent:e2e-selfhosted"
SESSION_ID = f"session-{uuid4().hex[:8]}"

# ── Platform client ───────────────────────────────────────────────────

platform = AgenticPlatformClient(
    GATEWAY_URL,
    aws_from_environment=True,
)

# JWT authentication (optional — set JWT_TOKEN env var or use Keycloak)
auth_token = fetch_token_from_env()
if auth_token:
    platform.set_auth_token(auth_token)

# Langfuse credentials (server-side via config, but also support header overrides)
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
    print("Langfuse: using server-side credentials from config")

platform.set_service_credentials("mcp_registry", {"token": os.environ.get("MCP_GATEWAY_REGISTRY_BEARER_TOKEN")})
# ── Primitive clients ─────────────────────────────────────────────────

obs = Observability(
    platform,
    namespace=AGENT_NAMESPACE,
    session_id=SESSION_ID,
    tags=["e2e_selfhosted", "langchain", "mem0", "langfuse"],
)

memory = Memory(
    platform,
    namespace=AGENT_NAMESPACE,
    session_id=SESSION_ID,
    observability=obs,
)

# ── Agent ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a research assistant running on a fully self-hosted stack with \
persistent memory backed by mem0 + Milvus, a browser via Selenium Grid, \
a Jupyter kernel for code execution, and full observability through Langfuse.

You have tools for memory (store/search/recall), code execution, web browsing, \
and external tool invocation.

Always search your memory before saying you don't know something. \
When you learn new information, store it. Your memory persists across \
sessions -- if a user told you something yesterday, you can recall it today. \
When using the browser, always start with open_browser, then browse to \
a URL. Use close_browser when done.
"""


async def main() -> None:
    print("=" * 60)
    print("E2E Self-Hosted LangChain Agent")
    print("=" * 60)
    print(f"Gateway:   {GATEWAY_URL}")
    print(f"Namespace: {AGENT_NAMESPACE}")
    print(f"Session:   {SESSION_ID}")
    print(f"Langfuse:  {obs.session_id}")
    print()
    print("Backends: mem0+Milvus | Langfuse | Jupyter | Selenium | Redis")
    print("Auth:     JWT (Keycloak) | Cedar enforcement")
    print()

    # ── Cedar enforcement demo ──────────────────────────────────
    cedar_engine_id, cedar_policy_id = await cedar_demo_async(GATEWAY_URL, AGENT_NAMESPACE, auth_token)

    # Fetch tools from gateway catalog (after Cedar policy is active)
    tools = [
        tool(fn)
        for fn in await platform.get_tools(
            ["memory", "browser", "code_interpreter", "tools"],
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

    await obs.log("info", "E2E self-hosted LangChain agent started")

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

        # Recall relevant context from past conversations
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
        async for event in agent.astream_events({"messages": messages}, version="v2", config={"recursion_limit": 100}):
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

            # Trace the full conversation turn to Langfuse
            await obs.trace(
                "conversation:turn",
                {"user": user_input},
                reply,
                tags=["conversation", "e2e_selfhosted"],
            )
        else:
            print("\nAssistant: (no response)\n")

    # Cleanup
    await obs.flush()
    await obs.log("info", "E2E self-hosted LangChain agent stopped")

    # Clean up Cedar policy (restores default-deny)
    await cedar_cleanup_async(GATEWAY_URL, cedar_engine_id, cedar_policy_id, auth_token)


if __name__ == "__main__":
    asyncio.run(main())

# ═══════════════════════════════════════════════════════════════════════
# Example prompts to showcase each primitive:
#
# Memory (key-value -- mem0 + Milvus):
#   "Remember that our team uses FastAPI for backend and React for frontend"
#   "Search your memory for anything about our tech stack"
#   "What do you recall about React?"
#   "List everything stored in memory"
#   "Forget the information about React"
#
# Code Interpreter (Jupyter):
#   "Write Python code to calculate compound interest over 10 years"
#   "Import pandas and create a DataFrame with sample employee data,
#    then compute the average salary by department"
#
# Browser (Selenium Grid):
#   "Open a browser and go to https://example.com"
#   "Read the page content"
#   "Take a screenshot"
#   "Close the browser"
#
# Tools (MCP Registry):
#   "List all registered tools"
#   "Search for tools that can send emails"
#
# Multi-primitive workflows:
#   "Research quantum computing: search memory first, then browse Wikipedia,
#    store key facts, and write Python code to visualize a qubit state"
#   "Open a browser, navigate to a news site, extract the headlines,
#    store them in memory, then use code to create a word cloud"
# ═══════════════════════════════════════════════════════════════════════
