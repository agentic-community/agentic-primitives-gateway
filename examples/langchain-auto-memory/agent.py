"""LangChain agent with automatic conversation memory via mem0 + Milvus.

Memory is NOT exposed as a tool. Instead, every conversation turn is
automatically stored to the platform's mem0/Milvus backend, and relevant
past context is retrieved before each response. mem0 decides what's
important, what to extract, and what to compact.

This gives the agent persistent memory across sessions without the agent
needing to explicitly decide what to remember.

Server config:
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

Prerequisites:
    pip install -r requirements.txt

Usage:
    # Start the server with mem0/Milvus config
    ./run.sh milvus-langfuse

    # Run the agent
    python agent.py
"""

import asyncio
import os
import sys

from langchain.agents import create_agent
from langchain_aws import ChatBedrock
from langchain_core.tools import tool

from agentic_primitives_gateway_client import AgenticPlatformClient, Memory, Observability

# ── Platform client ─────────────────────────────────────────────────

platform = AgenticPlatformClient(
    "http://localhost:8000",
    aws_from_environment=True,
)
platform.set_provider_for("observability", "langfuse")
platform.set_provider_for("memory", "mem0")
platform.set_service_credentials(
    "langfuse",
    {
        "public_key": os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
        "secret_key": os.environ.get("LANGFUSE_SECRET_KEY", ""),
        "base_url": os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"),
    },
)

AGENT_NAMESPACE = "agent:langchain-auto-memory"

obs = Observability(platform, namespace=AGENT_NAMESPACE, tags=["langchain-agent", "auto-memory"])
memory = Memory(platform, namespace=AGENT_NAMESPACE, observability=obs)


# ── Optional tools ──────────────────────────────────────────────────


@tool
async def web_search(query: str) -> str:
    """Search the web for information.

    Args:
        query: What to search for.
    """
    # Placeholder — wire to a real search API
    return f"(web search results for: {query})"


# ── Agent ───────────────────────────────────────────────────────────

BASE_SYSTEM_PROMPT = """\
You are a helpful assistant with long-term memory. You remember things \
from previous conversations automatically — you don't need to be told \
to remember something.

If context from past conversations is provided below, use it naturally \
in your responses. Don't mention "my memory" or "I recall from my \
database" — just use the information as if you naturally remember it.
"""


async def main():
    model = ChatBedrock(
        model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
        streaming=True,
    )

    agent = create_agent(
        model,
        tools=[web_search],
        system_prompt=BASE_SYSTEM_PROMPT,
    )

    print("LangChain auto-memory agent ready. Memory backed by mem0 + Milvus.")
    print(f"Namespace: {AGENT_NAMESPACE} | Session: {memory.session_id}")
    print("Type 'quit' to exit.\n")

    await obs.log("info", "LangChain auto-memory agent started")

    history = []

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input or user_input.lower() in ("quit", "exit"):
            break

        # 1. Store the user's message
        await memory.store_turn("user", user_input)

        # 2. Retrieve relevant memory context
        context = await memory.recall_context(user_input)

        # 3. Build messages with memory context injected
        system_prompt = BASE_SYSTEM_PROMPT
        if context:
            system_prompt += "\n\nRelevant context from past conversations:\n" + context

        messages = [{"role": "system", "content": system_prompt}, *history]
        messages.append({"role": "user", "content": user_input})

        # 4. Stream the agent's response
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

            # 5. Store the assistant's response
            await memory.store_turn("assistant", reply)

            # 6. Trace the full conversation turn
            await obs.trace(
                "conversation:turn",
                {"user": user_input},
                reply,
                tags=["conversation"],
            )
        else:
            print("\nAssistant: (no response)\n")

    await obs.log("info", "LangChain auto-memory agent stopped")


if __name__ == "__main__":
    asyncio.run(main())
