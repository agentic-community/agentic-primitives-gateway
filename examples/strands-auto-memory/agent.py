"""Strands agent with automatic conversation memory and X-Ray observability.

Memory is NOT exposed as a tool. Instead, every conversation turn is
automatically stored to the platform's memory backend (mem0 or AgentCore),
and relevant past context is retrieved before each response. The memory
framework (mem0 / AgentCore) decides what's important, what to compact,
and what to surface.

Each conversation turn is traced to X-Ray via the AgentCore observability
provider, showing user input, agent response, and memory operations.

Server config (either works):
    # Option A: mem0 + Milvus (handles extraction/compaction automatically)
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

    # Option B: AgentCore Memory (managed extraction/compaction)
    providers:
      memory:
        default: "agentcore"
        backends:
          agentcore:
            backend: agentic_primitives_gateway.primitives.memory.agentcore.AgentCoreMemoryProvider
            config: { region: "us-east-1" }

Prerequisites:
    pip install -r requirements.txt

Usage:
    kubectl port-forward svc/agentic-primitives-gateway 8000:8000
    python agent.py
"""

import os

from strands import Agent, tool
from strands.models import BedrockModel

from agentic_primitives_gateway_client import AgenticPlatformClient, Memory, Observability

# ── Platform client ─────────────────────────────────────────────────

platform = AgenticPlatformClient(
    "http://localhost:8000",
    aws_from_environment=True,
)

AGENT_NAMESPACE = "agent:auto-memory"
AGENTCORE_MEMORY_ID = os.environ.get("AGENTCORE_MEMORY_ID", "")

platform.set_service_credentials("agentcore", {"memory_id": AGENTCORE_MEMORY_ID})
platform.set_provider_for("observability", "agentcore")

obs = Observability(platform, namespace=AGENT_NAMESPACE, tags=["strands-agent", "auto-memory"])
memory = Memory(platform, namespace=AGENT_NAMESPACE, observability=obs)


# ── Optional tools (the agent can still do things) ──────────────────


@tool
def web_search(query: str) -> str:
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


def main():
    model = BedrockModel(
        model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
        streaming=True,
    )

    agent = Agent(
        model=model,
        system_prompt=BASE_SYSTEM_PROMPT,
        tools=[web_search],
    )

    print("Auto-memory agent ready. Conversations are stored automatically.")
    print(f"Namespace: {AGENT_NAMESPACE} | Session: {memory.session_id}")
    print("Type 'quit' to exit.\n")

    obs.log_sync("info", "Strands auto-memory agent started")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input or user_input.lower() in ("quit", "exit"):
            break

        # 1. Store the user's message
        memory.store_turn_sync("user", user_input)

        # 2. Inject relevant memory context into the system prompt
        context = memory.recall_context_sync(user_input)
        if context:
            agent.system_prompt = BASE_SYSTEM_PROMPT + "\n\nRelevant context from past conversations:\n" + context
        else:
            agent.system_prompt = BASE_SYSTEM_PROMPT

        # 3. Get the agent's response
        response = agent(user_input)
        response_text = str(response)

        # 4. Store the assistant's response
        memory.store_turn_sync("assistant", response_text)

        # 5. Trace the full conversation turn to X-Ray
        obs.trace_sync(
            "conversation:turn",
            {"user": user_input},
            response_text,
            tags=["conversation"],
        )

        print()

    obs.log_sync("info", "Strands auto-memory agent stopped")


if __name__ == "__main__":
    main()
