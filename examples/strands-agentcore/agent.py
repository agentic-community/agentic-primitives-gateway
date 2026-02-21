"""Strands agent using AgentCore primitives via the Agentic Primitives Gateway.

Demonstrates all AgentCore-backed primitives:
  - Memory — store and search agent knowledge
  - Identity — exchange tokens for third-party services
  - Code Interpreter — run code in a sandbox
  - Browser — automate web interactions
  - Observability — trace tool invocations

Prerequisites:
    pip install -r requirements.txt

Usage:
    ./run.sh agentcore
    python agent.py
"""

import os

from strands import Agent, tool
from strands.models import BedrockModel

from agentic_primitives_gateway_client import (
    AgenticPlatformClient,
    Browser,
    CodeInterpreter,
    Identity,
    Memory,
    Observability,
)

# ── Platform client ─────────────────────────────────────────────────

platform = AgenticPlatformClient(
    "http://localhost:8000",
    aws_from_environment=True,
)

memory_id = os.environ.get("AGENTCORE_MEMORY_ID")
if memory_id:
    platform.set_service_credentials("agentcore", {"memory_id": memory_id})

AGENT_NAMESPACE = "agent:strands-research"
AGENTCORE_MEMORY_ID = os.environ.get("AGENTCORE_MEMORY_ID", "")

platform.set_service_credentials("agentcore", {"memory_id": AGENTCORE_MEMORY_ID})
obs = Observability(platform, namespace=AGENT_NAMESPACE, tags=["strands-agent", "agentcore"])
memory = Memory(platform, namespace=AGENT_NAMESPACE, observability=obs)
identity = Identity(platform)
code = CodeInterpreter(platform)
browser = Browser(platform)


# ── Memory tools ───────────────────────────────────────────────────


@tool
def remember(key: str, content: str, source: str = "") -> str:
    """Store a piece of information in long-term memory.

    Args:
        key: A short identifier for this memory.
        content: The information to remember.
        source: Optional source of the information.
    """
    return memory.remember_sync(key, content, source)


@tool
def recall(key: str) -> str:
    """Retrieve a specific memory by its key.

    Args:
        key: The key of the memory to retrieve.
    """
    return memory.recall_sync(key)


@tool
def search_memory(query: str, top_k: int = 5) -> str:
    """Search long-term memory for relevant information.

    Args:
        query: What to search for.
        top_k: Maximum number of results to return.
    """
    return memory.search_sync(query, top_k)


@tool
def list_memories(limit: int = 20) -> str:
    """List all stored memories.

    Args:
        limit: Maximum number of memories to list.
    """
    return memory.list_sync(limit)


@tool
def forget(key: str) -> str:
    """Delete a specific memory.

    Args:
        key: The key of the memory to delete.
    """
    return memory.forget_sync(key)


# ── Identity tools ─────────────────────────────────────────────────


@tool
def get_service_token(provider_name: str, scopes: str = "") -> str:
    """Get an OAuth2 access token for a third-party service.

    Args:
        provider_name: The identity provider (e.g., "github", "slack").
        scopes: Comma-separated scopes (e.g., "repo,read:user").
    """
    scope_list = [s.strip() for s in scopes.split(",") if s.strip()] if scopes else None
    result = identity.get_token_sync(provider_name, scopes=scope_list)
    obs.trace_sync("identity:get_token", {"provider": provider_name, "scopes": scopes}, result)
    return result


@tool
def get_service_api_key(provider_name: str) -> str:
    """Get an API key for a third-party service.

    Args:
        provider_name: The credential provider (e.g., "openai").
    """
    result = identity.get_api_key_sync(provider_name)
    obs.trace_sync("identity:get_api_key", {"provider": provider_name}, result)
    return result


# ── Code Interpreter tools ─────────────────────────────────────────


@tool
def run_code(code_str: str, language: str = "python") -> str:
    """Execute code in a sandboxed environment.

    Args:
        code_str: The code to execute.
        language: Programming language (default: python).
    """
    result = code.execute_sync(code_str, language)
    obs.trace_sync("code_interpreter:execute", {"code": code_str[:200], "language": language}, result[:500])
    return result


# ── Browser tools ──────────────────────────────────────────────────


@tool
def start_browser() -> str:
    """Start a cloud browser session."""
    result = browser.start_sync()
    obs.trace_sync("browser:start", {}, result)
    return result


@tool
def stop_browser() -> str:
    """Stop the current browser session."""
    result = browser.close_sync()
    obs.trace_sync("browser:stop", {}, result)
    return result


# ── Discovery ──────────────────────────────────────────────────────


@tool
def check_providers() -> str:
    """Check which providers are available on the platform server."""
    result = memory._sync(platform.list_providers())
    lines = [f"  {prim}: default={info['default']}, available={info['available']}" for prim, info in result.items()]
    return "Available providers:\n" + "\n".join(lines)


# ── Agent ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a research assistant with access to the full Agentic Primitives Gateway. \
Your capabilities:

**Memory** — Store findings with `remember`, look them up with \
`search_memory` or `recall`, list everything with `list_memories`, \
and clean up with `forget`.

**Identity** — Exchange your agent identity for third-party service \
tokens with `get_service_token` or API keys with `get_service_api_key`.

**Code Interpreter** — Run Python (or other languages) in a sandboxed \
container with `run_code`. State persists across calls within a session.

**Browser** — Start a cloud browser with `start_browser` for web \
automation. Stop it with `stop_browser` when done.

Always search your memory before saying you don't know something. Use \
the code interpreter for calculations or data processing.
"""


def main():
    model = BedrockModel(
        model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
        streaming=True,
    )

    agent = Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[
            remember,
            recall,
            search_memory,
            list_memories,
            forget,
            get_service_token,
            get_service_api_key,
            run_code,
            start_browser,
            stop_browser,
            check_providers,
        ],
    )

    print("Strands + AgentCore agent ready.")
    print("Connected to platform at http://localhost:8000")
    print("Type 'quit' to exit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input or user_input.lower() in ("quit", "exit"):
            break

        response = agent(user_input)
        obs.trace_sync(
            "conversation:turn",
            {"user": user_input},
            str(response),
            tags=["conversation"],
        )
        print()

    # Clean up sessions
    code.close_sync()
    browser.close_sync()


if __name__ == "__main__":
    main()
