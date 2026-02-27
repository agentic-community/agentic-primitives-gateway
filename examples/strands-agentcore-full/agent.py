"""Strands agent exercising every primitive via AWS Bedrock AgentCore.

Demonstrates:
  - Memory        -- store, search, recall, list, forget (key-value)
                     conversation events, session history, branching
                     memory resource lifecycle and strategy management
  - Identity      -- exchange workload tokens, retrieve API keys, manage
                     credential providers and workload identities
  - Code Interp.  -- execute Python/shell, upload & download files,
                     review execution history
  - Browser       -- start session, navigate, read page, click, type,
                     evaluate JS, take screenshots, stop session
  - Observability -- ingest traces & logs, log LLM generations,
                     score traces, query traces, manage sessions
  - Tools         -- register, list, search, invoke, and delete tools;
                     MCP server management
  - Gateway       -- route LLM completion requests, list models

Server config:
    ./run.sh agentcore

Prerequisites:
    pip install -r requirements.txt

Usage:
    export AGENTCORE_MEMORY_ID=<your-memory-id>
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

# ── Platform client ─────────────────────────────────────────────────

platform = AgenticPlatformClient(
    "http://localhost:8000",
    aws_from_environment=True,
)

# Route every primitive to agentcore (needed when running against kitchen-sink
# or any multi-backend config where agentcore isn't the default).
platform.set_provider("agentcore")

AGENT_NAMESPACE = "agent:strands-full"
SESSION_ID = "demo-session"
ACTOR_ID = "demo-agent"

memory_id = os.environ.get("AGENTCORE_MEMORY_ID", "")
if memory_id:
    platform.set_service_credentials("agentcore", {"memory_id": memory_id})

obs = Observability(platform, namespace=AGENT_NAMESPACE, session_id=SESSION_ID, tags=["strands-full"])
memory = Memory(platform, namespace=AGENT_NAMESPACE, session_id=SESSION_ID, observability=obs)
identity = Identity(platform)
code = CodeInterpreter(platform)
browser = Browser(platform)
tools_client = Tools(platform)


# ── Memory: key-value ──────────────────────────────────────────────


@tool
def remember(key: str, content: str, source: str = "") -> str:
    """Store information in long-term memory.

    Args:
        key: A short identifier for this memory.
        content: The information to remember.
        source: Optional source of the information.
    """
    return memory.remember_sync(key, content, source)


@tool
def recall(key: str) -> str:
    """Retrieve a memory by its exact key.

    Args:
        key: The key to look up.
    """
    return memory.recall_sync(key)


@tool
def search_memory(query: str, top_k: int = 5) -> str:
    """Search memories using semantic similarity.

    Args:
        query: What to search for.
        top_k: Maximum number of results.
    """
    return memory.search_sync(query, top_k)


@tool
def list_memories(limit: int = 20) -> str:
    """List all stored memories.

    Args:
        limit: Maximum number of memories to show.
    """
    return memory.list_sync(limit)


@tool
def forget(key: str) -> str:
    """Delete a memory.

    Args:
        key: The key of the memory to delete.
    """
    return memory.forget_sync(key)


# ── Memory: conversation events & sessions ─────────────────────────


@tool
def add_message(role: str, content: str) -> str:
    """Add a message to the conversation history (event API).

    Args:
        role: Message role ('user' or 'assistant').
        content: The message text.
    """
    return memory.add_message_sync(role, content)


@tool
def get_history(turns: int = 5) -> str:
    """Get recent conversation history.

    Args:
        turns: Number of recent turns to retrieve.
    """
    return memory.get_history_sync(turns)


@tool
def list_conversations() -> str:
    """List all conversation sessions for this agent."""
    return memory.list_conversations_sync()


# ── Memory: resource lifecycle & strategies ─────────────────────────


@tool
def create_memory_resource(name: str, description: str = "") -> str:
    """Create a new memory resource on the control plane.

    Args:
        name: Resource name.
        description: Optional description.
    """
    result = memory._sync(platform.create_memory_resource(name, description=description))
    obs.trace_sync("memory:create_resource", {"name": name}, str(result))
    return json.dumps(result, default=str)


@tool
def list_memory_resources() -> str:
    """List all memory resources on the control plane."""
    result = memory._sync(platform.list_memory_resources())
    return json.dumps(result.get("resources", []), default=str)


# ── Identity ───────────────────────────────────────────────────────


@tool
def get_service_token(provider_name: str, scopes: str = "") -> str:
    """Exchange workload identity for a third-party OAuth2 token.

    Args:
        provider_name: Credential provider name (e.g., "github", "slack").
        scopes: Comma-separated scopes (e.g., "repo,read:user").
    """
    scope_list = [s.strip() for s in scopes.split(",") if s.strip()] if scopes else None
    result = identity.get_token_sync(provider_name, scopes=scope_list)
    obs.trace_sync("identity:get_token", {"provider": provider_name}, result)
    return result


@tool
def get_service_api_key(provider_name: str) -> str:
    """Retrieve an API key for a third-party service.

    Args:
        provider_name: Credential provider name (e.g., "openai").
    """
    result = identity.get_api_key_sync(provider_name)
    obs.trace_sync("identity:get_api_key", {"provider": provider_name}, result)
    return result


@tool
def get_workload_token(workload_name: str) -> str:
    """Obtain a workload identity token for this agent.

    Args:
        workload_name: The registered workload identity name.
    """
    result = identity.get_workload_token_sync(workload_name)
    obs.trace_sync("identity:get_workload_token", {"workload": workload_name}, result)
    return result


@tool
def list_credential_providers() -> str:
    """List all registered credential providers."""
    result = memory._sync(platform.list_credential_providers())
    return json.dumps(result.get("credential_providers", []), default=str)


@tool
def list_workload_identities() -> str:
    """List all registered workload identities."""
    result = memory._sync(platform.list_workload_identities())
    return json.dumps(result.get("workload_identities", []), default=str)


# ── Code Interpreter ───────────────────────────────────────────────


@tool
def run_code(code_str: str, language: str = "python") -> str:
    """Execute code in a sandboxed environment. State persists across calls.

    Args:
        code_str: The code to execute.
        language: Programming language (default: python).
    """
    result = code.execute_sync(code_str, language)
    obs.trace_sync("code:execute", {"code": code_str[:200], "language": language}, result[:500])
    return result


@tool
def code_history(limit: int = 10) -> str:
    """Get recent execution history for the code session.

    Args:
        limit: Maximum number of entries.
    """
    return code.history_sync(limit)


@tool
def upload_to_sandbox(filename: str, content: str) -> str:
    """Upload a text file to the code sandbox.

    Args:
        filename: Name for the file.
        content: File contents.
    """
    if not code.session_id:
        code.execute_sync("print('session started')")
    result = memory._sync(platform.upload_file(code.session_id, filename, content.encode()))
    obs.trace_sync("code:upload", {"filename": filename}, str(result))
    return json.dumps(result, default=str)


# ── Browser ────────────────────────────────────────────────────────


@tool
def open_browser() -> str:
    """Start a cloud browser session. Returns session info and live view URL."""
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
    """Read the text content of the current browser page."""
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
        selector: CSS selector (e.g., "input[name='email']").
        text: Text to type.
    """
    result = browser.type_text_sync(selector, text)
    obs.trace_sync("browser:type", {"selector": selector}, result)
    return result


@tool
def run_js(expression: str) -> str:
    """Evaluate JavaScript in the browser.

    Args:
        expression: JavaScript expression to evaluate.
    """
    result = browser.evaluate_sync(expression)
    obs.trace_sync("browser:evaluate", {"expression": expression[:200]}, result[:500])
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


# ── Observability ──────────────────────────────────────────────────


@tool
def query_traces(limit: int = 10) -> str:
    """Query recent observability traces.

    Args:
        limit: Maximum number of traces to return.
    """
    return obs.query_traces_sync(limit)


@tool
def score_trace(trace_id: str, name: str, value: float, comment: str = "") -> str:
    """Attach an evaluation score to a trace.

    Args:
        trace_id: The trace ID to score.
        name: Score name (e.g., "accuracy", "relevance").
        value: Numeric score (0.0 to 1.0).
        comment: Optional comment.
    """
    return obs.score_sync(trace_id, name, value, comment or None)


@tool
def view_sessions(limit: int = 10) -> str:
    """List recent observability sessions.

    Args:
        limit: Maximum number of sessions to return.
    """
    return obs.get_sessions_sync(limit)


# ── Tools (MCP) ───────────────────────────────────────────────────


@tool
def register_tool_def(name: str, description: str, parameters: str = "{}") -> str:
    """Register a tool definition.

    Args:
        name: Tool name.
        description: What the tool does.
        parameters: JSON schema for parameters.
    """
    params = json.loads(parameters) if parameters else {}
    result = tools_client.register_sync(name, description, params)
    obs.trace_sync("tools:register", {"name": name}, result)
    return result


@tool
def list_tools() -> str:
    """List all registered tools."""
    return tools_client.list_tools_sync()


@tool
def search_tools(query: str, max_results: int = 10) -> str:
    """Search for tools by capability.

    Args:
        query: What kind of tool you need.
        max_results: Maximum results to return.
    """
    return tools_client.search_sync(query, max_results)


@tool
def invoke_tool(tool_name: str, params: str = "{}") -> str:
    """Invoke a registered tool.

    Args:
        tool_name: Tool name to invoke.
        params: JSON parameters for the tool.
    """
    parsed = json.loads(params) if isinstance(params, str) else params
    result = tools_client.invoke_sync(tool_name, parsed)
    obs.trace_sync("tools:invoke", {"tool": tool_name}, result[:500])
    return result


@tool
def list_mcp_servers() -> str:
    """List registered MCP servers."""
    return tools_client.list_servers_sync()


# ── Gateway ────────────────────────────────────────────────────────


@tool
def gateway_completion(model: str, prompt: str) -> str:
    """Route an LLM completion request through the gateway.

    Args:
        model: Model identifier.
        prompt: The prompt to send.
    """
    from agentic_primitives_gateway_client import Gateway

    gw = Gateway(platform)
    result = gw.completions_sync(model, [{"role": "user", "content": prompt}])
    obs.trace_sync("gateway:completion", {"model": model, "prompt": prompt[:200]}, result[:500])
    return result


@tool
def list_gateway_models() -> str:
    """List models available through the gateway."""
    from agentic_primitives_gateway_client import Gateway

    gw = Gateway(platform)
    return gw.list_models_sync()


# ── Discovery ──────────────────────────────────────────────────────


@tool
def check_providers() -> str:
    """Check which providers are available on the platform."""
    result = memory._sync(platform.list_providers())
    lines = [f"  {p}: default={info['default']}, available={info['available']}" for p, info in result.items()]
    return "Available providers:\n" + "\n".join(lines)


# ── Agent ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a research assistant with access to the full Agentic Primitives \
Gateway backed by AWS Bedrock AgentCore. Your capabilities span all seven \
primitives:

**Memory** (key-value + conversation):
- `remember`, `recall`, `search_memory`, `list_memories`, `forget`
- `add_message`, `get_history`, `list_conversations`
- `create_memory_resource`, `list_memory_resources`

**Identity** (workload identity + credential exchange):
- `get_service_token` — exchange agent identity for OAuth2 tokens
- `get_service_api_key` — retrieve API keys
- `get_workload_token` — obtain workload identity tokens
- `list_credential_providers`, `list_workload_identities`

**Code Interpreter** (sandboxed execution):
- `run_code` — execute Python/shell in a sandbox (state persists)
- `code_history` — review past executions
- `upload_to_sandbox` — upload files to the sandbox

**Browser** (cloud automation):
- `open_browser`, `browse_to`, `read_page`, `click_element`, \
`type_into`, `run_js`, `take_screenshot`, `close_browser`

**Observability** (tracing + scoring):
- `query_traces`, `score_trace`, `view_sessions`
- All tool calls are automatically traced

**Tools** (MCP tool registry):
- `register_tool_def`, `list_tools`, `search_tools`, `invoke_tool`
- `list_mcp_servers`

**Gateway** (LLM routing):
- `gateway_completion` — route completions through the gateway
- `list_gateway_models`

**Discovery:**
- `check_providers` — see which backends are active

Always search memory before saying you don't know something. Use the \
code interpreter for calculations and data processing. Use the browser \
for web interactions.
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
            # Memory
            remember,
            recall,
            search_memory,
            list_memories,
            forget,
            add_message,
            get_history,
            list_conversations,
            create_memory_resource,
            list_memory_resources,
            # Identity
            get_service_token,
            get_service_api_key,
            get_workload_token,
            list_credential_providers,
            list_workload_identities,
            # Code Interpreter
            run_code,
            code_history,
            upload_to_sandbox,
            # Browser
            open_browser,
            browse_to,
            read_page,
            click_element,
            type_into,
            run_js,
            take_screenshot,
            close_browser,
            # Observability
            query_traces,
            score_trace,
            view_sessions,
            # Tools
            register_tool_def,
            list_tools,
            search_tools,
            invoke_tool,
            list_mcp_servers,
            # Gateway
            gateway_completion,
            list_gateway_models,
            # Discovery
            check_providers,
        ],
    )

    print("Strands + AgentCore full-primitives agent ready.")
    print("Connected to platform at http://localhost:8000")
    print(f"Memory namespace: {AGENT_NAMESPACE}")
    print("Type 'quit' to exit.\n")

    obs.log_sync("info", "Full-primitives agent started", framework="strands")

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

    code.close_sync()
    browser.close_sync()
    obs.log_sync("info", "Full-primitives agent stopped", framework="strands")


if __name__ == "__main__":
    main()
