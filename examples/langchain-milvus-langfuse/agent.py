"""LangChain agent using mem0/Milvus memory + Langfuse observability.

Demonstrates the open-source stack through the Agentic Primitives Gateway:
  - Memory (mem0 + Milvus) — semantic memory with intelligent extraction
  - Observability (Langfuse) — trace every tool call and conversation turn

Uses Bedrock for the LLM. AWS credentials are needed for Bedrock model
invocation (and for mem0's Bedrock LLM calls on the server side).

The server should be configured with mem0 and Langfuse backends:
    providers:
      memory:
        default: "mem0"
        backends:
          mem0:
            backend: agentic_primitives_gateway.primitives.memory.mem0_provider.Mem0MemoryProvider
            config:
              vector_store:
                provider: milvus
                config: { collection_name: agentic_memories_bedrock, url: "http://milvus:19530", token: "", embedding_model_dims: 1024 }
              llm:
                provider: aws_bedrock
                config: { model: "us.anthropic.claude-sonnet-4-20250514-v1:0" }
              embedder:
                provider: aws_bedrock
                config: { model: "amazon.titan-embed-text-v2:0" }
      observability:
        default: "langfuse"
        backends:
          langfuse:
            backend: agentic_primitives_gateway.primitives.observability.langfuse.LangfuseObservabilityProvider
            config: {}   # credentials come from the client

Prerequisites:
    pip install -r requirements.txt

Usage:
    kubectl port-forward svc/agentic-primitives-gateway 8000:8000

    # Langfuse credentials (get from https://cloud.langfuse.com)
    export LANGFUSE_PUBLIC_KEY=pk-...
    export LANGFUSE_SECRET_KEY=sk-...

    # AWS credentials for Bedrock (Pod Identity, IRSA, env vars, etc.)

    python agent.py
"""

import asyncio
import os

from langchain.agents import create_agent
from langchain_aws import ChatBedrock
from langchain_core.tools import tool

from agentic_primitives_gateway_client import AgenticPlatformClient, Memory, Observability

# ── Platform client ─────────────────────────────────────────────────

platform = AgenticPlatformClient(
    "http://localhost:8000",
    aws_from_environment=True,
)

# Pass Langfuse credentials
if os.environ.get("LANGFUSE_PUBLIC_KEY"):
    platform.set_service_credentials(
        "langfuse",
        {
            "public_key": os.environ["LANGFUSE_PUBLIC_KEY"],
            "secret_key": os.environ["LANGFUSE_SECRET_KEY"],
            "base_url": os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"),
        },
    )
    print(f"Langfuse: tracing to project {os.environ['LANGFUSE_PUBLIC_KEY'][:8]}...")
else:
    print("Langfuse: LANGFUSE_PUBLIC_KEY not set, observability disabled")

AGENT_NAMESPACE = "agent:langchain-research"

obs = Observability(
    platform,
    namespace=AGENT_NAMESPACE,
    tags=["langchain-agent", "milvus-langfuse"],
)

memory = Memory(
    platform,
    namespace=AGENT_NAMESPACE,
    observability=obs,
)


# ── Memory tools (via Memory) ──────────────────────────────────


@tool
async def remember(key: str, content: str, source: str = "") -> str:
    """Store a piece of information in long-term memory.

    Backed by mem0 with Milvus vector storage.

    Args:
        key: A short identifier for this memory.
        content: The information to remember.
        source: Optional source of the information.
    """
    return await memory.remember(key, content, source)


@tool
async def recall(key: str) -> str:
    """Retrieve a specific memory by its key.

    Args:
        key: The key of the memory to retrieve.
    """
    return await memory.recall(key)


@tool
async def search_memory(query: str, top_k: int = 5) -> str:
    """Search long-term memory using semantic similarity.

    Args:
        query: Natural language query.
        top_k: Maximum number of results to return.
    """
    return await memory.search(query, top_k)


@tool
async def list_memories(limit: int = 20) -> str:
    """List all stored memories.

    Args:
        limit: Maximum number of memories to list.
    """
    return await memory.list(limit)


@tool
async def forget(key: str) -> str:
    """Delete a specific memory.

    Args:
        key: The key of the memory to delete.
    """
    return await memory.forget(key)


# ── Observability tools (via Observability helper) ──────────────────


@tool
async def query_traces(limit: int = 10) -> str:
    """Query recent traces from Langfuse.

    Args:
        limit: Maximum number of traces to return.
    """
    return await obs.query_traces(limit)


# ── Agent ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a research assistant with long-term memory backed by Milvus \
vector search and full observability through Langfuse.

**Memory** (mem0 + Milvus):
- `remember` — store findings with semantic indexing
- `search_memory` — find relevant information via vector similarity
- `recall` — retrieve by exact key
- `list_memories` — see everything stored
- `forget` — remove outdated information

**Observability** (Langfuse):
- `query_traces` — see recent activity and tool invocations
- All your tool calls are automatically traced to Langfuse

Always search your memory before saying you don't know something. When \
you learn something new, store it for future reference.
"""


async def main():
    model = ChatBedrock(model_id="us.anthropic.claude-sonnet-4-20250514-v1:0")

    agent = create_agent(
        model,
        tools=[remember, recall, search_memory, list_memories, forget, query_traces],
        system_prompt=SYSTEM_PROMPT,
    )

    print("LangChain + Milvus + Langfuse agent ready.")
    print("Connected to platform at http://localhost:8000")
    print(f"Session: {obs.session_id}")
    print("Type 'quit' to exit.\n")

    await obs.log("info", "LangChain agent started", framework="langgraph")

    history = []

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input or user_input.lower() in ("quit", "exit"):
            break

        history.append({"role": "user", "content": user_input})

        result = await agent.ainvoke({"messages": history})

        ai_messages = [m for m in result["messages"] if hasattr(m, "type") and m.type == "ai" and m.content]
        if ai_messages:
            reply = ai_messages[-1].content
            print(f"\nAssistant: {reply}\n")
            history.append({"role": "assistant", "content": reply})

            await obs.trace(
                "conversation:turn",
                {"user": user_input},
                reply,
                tags=["conversation"],
            )
        else:
            print("\nAssistant: (no response)\n")

    await obs.log("info", "LangChain agent stopped", framework="langgraph")


if __name__ == "__main__":
    asyncio.run(main())
