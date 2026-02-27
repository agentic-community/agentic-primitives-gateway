"""Strands agent mixing AgentCore and open-source providers.

Demonstrates per-primitive provider routing — the gateway's killer feature.
Each request header tells the server which backend to use for each primitive:

  Memory        -> mem0 + Milvus   (open-source vector search)
  Observability -> Langfuse        (open-source tracing)
  Identity      -> AgentCore       (AWS-managed credential exchange)
  Code Interp.  -> AgentCore       (AWS-managed sandbox)
  Browser       -> AgentCore       (AWS-managed cloud browser)
  Tools         -> AgentCore       (AWS-managed MCP gateway)

This is the "best of both worlds" pattern: use managed AWS services for
compute-heavy primitives (code execution, browser, identity) while keeping
memory and observability in your own infrastructure.

Server config:
    ./run.sh kitchen-sink

Prerequisites:
    pip install -r requirements.txt

    # Milvus for mem0 memory
    docker run -d --name milvus -p 19530:19530 milvusdb/milvus:latest

Usage:
    # Langfuse credentials
    export LANGFUSE_PUBLIC_KEY=pk-lf-...
    export LANGFUSE_SECRET_KEY=sk-lf-...
    export LANGFUSE_BASE_URL=http://localhost:3000

    # AgentCore memory ID (for the agentcore memory backend, not used here)
    # export AGENTCORE_MEMORY_ID=...

    python agent.py
"""

import json
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
    Tools,
)

# ── Platform client with per-primitive routing ──────────────────────

platform = AgenticPlatformClient(
    "http://localhost:8000",
    aws_from_environment=True,
)

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
    print("WARNING: LANGFUSE_PUBLIC_KEY not set — observability won't reach Langfuse")

AGENT_NAMESPACE = "agent:strands-mixed"

obs = Observability(
    platform,
    namespace=AGENT_NAMESPACE,
    tags=["strands-agent", "mixed-providers", "mem0", "langfuse", "agentcore"],
)

memory = Memory(platform, namespace=AGENT_NAMESPACE, observability=obs)
identity = Identity(platform)
code = CodeInterpreter(platform)
browser = Browser(platform)
tools_client = Tools(platform)


# ── Memory tools (routed to mem0 + Milvus) ─────────────────────────


@tool
def remember(key: str, content: str, source: str = "") -> str:
    """Store information in long-term memory (mem0 + Milvus).

    Args:
        key: A short identifier for this memory.
        content: The information to remember.
        source: Optional source of the information.
    """
    result = memory.remember_sync(key, content, source)
    obs.trace_sync("memory:remember", {"key": key}, result)
    return result


@tool
def recall(key: str) -> str:
    """Retrieve a memory by its exact key.

    Args:
        key: The key to look up.
    """
    return memory.recall_sync(key)


@tool
def search_memory(query: str, top_k: int = 5) -> str:
    """Search memories using Milvus vector similarity.

    Args:
        query: Natural language query.
        top_k: Maximum number of results.
    """
    return memory.search_sync(query, top_k)


@tool
def list_memories(limit: int = 20) -> str:
    """List all stored memories.

    Args:
        limit: Maximum number to show.
    """
    return memory.list_sync(limit)


@tool
def forget(key: str) -> str:
    """Delete a memory from Milvus.

    Args:
        key: The key of the memory to delete.
    """
    return memory.forget_sync(key)


# ── Identity tools (routed to AgentCore) ───────────────────────────


@tool
def get_service_token(provider_name: str, scopes: str = "") -> str:
    """Exchange agent identity for a third-party OAuth2 token (via AgentCore).

    Args:
        provider_name: Credential provider (e.g., "github", "slack", "jira").
        scopes: Comma-separated OAuth scopes.
    """
    scope_list = [s.strip() for s in scopes.split(",") if s.strip()] if scopes else None
    result = identity.get_token_sync(provider_name, scopes=scope_list)
    obs.trace_sync("identity:get_token", {"provider": provider_name}, result)
    return result


@tool
def get_service_api_key(provider_name: str) -> str:
    """Retrieve an API key for a service (via AgentCore).

    Args:
        provider_name: Credential provider (e.g., "openai", "anthropic").
    """
    result = identity.get_api_key_sync(provider_name)
    obs.trace_sync("identity:get_api_key", {"provider": provider_name}, result)
    return result


# ── Code Interpreter tools (routed to AgentCore) ──────────────────


@tool
def run_python(code_str: str) -> str:
    """Execute Python in an AgentCore sandbox. State persists across calls.

    Args:
        code_str: The Python code to execute.
    """
    result = code.execute_sync(code_str)
    obs.trace_sync("code:execute", {"code": code_str[:200]}, result[:500])
    return result


@tool
def run_shell(command: str) -> str:
    """Run a shell command in the AgentCore sandbox.

    Args:
        command: The shell command to run.
    """
    wrapped = f"""
import subprocess
result = subprocess.run({command!r}, shell=True, capture_output=True, text=True)
print(result.stdout)
if result.stderr:
    print("STDERR:", result.stderr)
"""
    result = code.execute_sync(wrapped)
    obs.trace_sync("code:shell", {"command": command}, result[:500])
    return result


@tool
def install_package(package: str) -> str:
    """Install a Python package in the sandbox.

    Args:
        package: Package name (e.g., "requests", "pandas").
    """
    result = code.execute_sync(f"""
import subprocess
result = subprocess.run(["pip", "install", "{package}"], capture_output=True, text=True)
print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
if result.returncode != 0:
    print("ERROR:", result.stderr[-500:])
""")
    obs.trace_sync("code:install", {"package": package}, result[:200])
    return result


# ── Browser tools (routed to AgentCore) ────────────────────────────


@tool
def open_browser() -> str:
    """Start an AgentCore cloud browser session."""
    result = browser.start_sync()
    obs.trace_sync("browser:start", {}, result)
    return result


@tool
def browse_to(url: str) -> str:
    """Navigate the browser to a URL.

    Args:
        url: The URL to visit.
    """
    result = browser.navigate_sync(url)
    obs.trace_sync("browser:navigate", {"url": url}, result)
    return result


@tool
def read_page() -> str:
    """Read the text content of the current page."""
    result = browser.get_page_content_sync()
    obs.trace_sync("browser:read", {}, result[:200])
    return result


@tool
def click_element(selector: str) -> str:
    """Click an element on the page.

    Args:
        selector: CSS selector (e.g., "button.submit", "#login").
    """
    result = browser.click_sync(selector)
    obs.trace_sync("browser:click", {"selector": selector}, result)
    return result


@tool
def type_into(selector: str, text: str) -> str:
    """Type text into an input field.

    Args:
        selector: CSS selector of the input.
        text: Text to type.
    """
    result = browser.type_text_sync(selector, text)
    obs.trace_sync("browser:type", {"selector": selector}, result)
    return result


@tool
def take_screenshot() -> str:
    """Take a screenshot of the current browser page."""
    result = browser.screenshot_sync()
    obs.trace_sync("browser:screenshot", {}, "screenshot captured")
    return result


@tool
def close_browser() -> str:
    """Close the current browser session."""
    result = browser.close_sync()
    obs.trace_sync("browser:stop", {}, result)
    return result


# ── Tools (routed to AgentCore) ────────────────────────────────────


@tool
def search_tools(query: str, max_results: int = 10) -> str:
    """Search for available tools by capability.

    Args:
        query: What kind of tool you need.
        max_results: Maximum results.
    """
    result = tools_client.search_sync(query, max_results)
    obs.trace_sync("tools:search", {"query": query}, result[:500])
    return result


@tool
def invoke_tool(tool_name: str, params: str = "{}") -> str:
    """Invoke a registered tool.

    Args:
        tool_name: Tool name (format: "server/tool-name").
        params: JSON parameters.
    """
    parsed = json.loads(params) if isinstance(params, str) else params
    result = tools_client.invoke_sync(tool_name, parsed)
    obs.trace_sync("tools:invoke", {"tool": tool_name}, result[:500])
    return result


@tool
def list_tools() -> str:
    """List all registered tools."""
    return tools_client.list_tools_sync()


# ── Observability (routed to Langfuse) ─────────────────────────────


@tool
def query_traces(limit: int = 10) -> str:
    """Query recent traces from Langfuse.

    Args:
        limit: Maximum number of traces.
    """
    return obs.query_traces_sync(limit)


@tool
def score_trace(trace_id: str, name: str, value: float, comment: str = "") -> str:
    """Attach a score to a Langfuse trace.

    Args:
        trace_id: The trace to score.
        name: Score name (e.g., "accuracy", "helpfulness").
        value: Numeric score (0.0 to 1.0).
        comment: Optional explanation.
    """
    return obs.score_sync(trace_id, name, value, comment or None)


# ── Discovery ──────────────────────────────────────────────────────


@tool
def check_providers() -> str:
    """Show which backend is active for each primitive on this request."""
    result = memory._sync(platform.list_providers())
    lines = [f"  {p}: default={info['default']}, available={info['available']}" for p, info in result.items()]
    return (
        "Server providers:\n" + "\n".join(lines) + "\n\nThis agent routes:\n"
        "  memory       -> mem0 (Milvus)\n"
        "  observability -> langfuse\n"
        "  identity     -> agentcore\n"
        "  code_interp  -> agentcore\n"
        "  browser      -> agentcore\n"
        "  tools        -> agentcore"
    )


# ── Agent ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a capable research assistant with a mixed-provider setup:

**Memory** (mem0 + Milvus — your own infrastructure):
- `remember`, `recall`, `search_memory`, `list_memories`, `forget`
- Semantic vector search via Milvus

**Identity** (AWS AgentCore — managed):
- `get_service_token` — exchange identity for OAuth2 tokens
- `get_service_api_key` — retrieve stored API keys

**Code Interpreter** (AWS AgentCore — managed sandbox):
- `run_python` — execute Python (state persists across calls)
- `run_shell` — run shell commands
- `install_package` — install pip packages

**Browser** (AWS AgentCore — managed cloud browser):
- `open_browser`, `browse_to`, `read_page`, `click_element`, \
`type_into`, `take_screenshot`, `close_browser`

**Tools** (AWS AgentCore — managed MCP gateway):
- `search_tools`, `invoke_tool`, `list_tools`

**Observability** (Langfuse — your own infrastructure):
- `query_traces`, `score_trace`
- All tool calls are automatically traced

**Discovery:**
- `check_providers` — see the routing configuration

Always search memory before saying you don't know something. Use the \
code interpreter for calculations. Use the browser for web tasks.
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
            # Memory (mem0 + Milvus)
            remember,
            recall,
            search_memory,
            list_memories,
            forget,
            # Identity (AgentCore)
            get_service_token,
            get_service_api_key,
            # Code Interpreter (AgentCore)
            run_python,
            run_shell,
            install_package,
            # Browser (AgentCore)
            open_browser,
            browse_to,
            read_page,
            click_element,
            type_into,
            take_screenshot,
            close_browser,
            # Tools (AgentCore)
            search_tools,
            invoke_tool,
            list_tools,
            # Observability (Langfuse)
            query_traces,
            score_trace,
            # Discovery
            check_providers,
        ],
    )

    print("Strands mixed-provider agent ready.")
    print("  Memory:        mem0 + Milvus")
    print("  Observability: Langfuse")
    print("  Identity:      AgentCore")
    print("  Code/Browser:  AgentCore")
    print("  Tools:         AgentCore")
    print("\nConnected to platform at http://localhost:8000")
    print("Type 'quit' to exit.\n")

    obs.log_sync("info", "Mixed-provider agent started", framework="strands")

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

    code.close_sync()
    browser.close_sync()
    obs.flush_sync()
    obs.log_sync("info", "Mixed-provider agent stopped", framework="strands")


if __name__ == "__main__":
    main()
