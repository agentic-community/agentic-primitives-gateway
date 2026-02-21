"""LangChain agent with dynamic MCP tool discovery via MCP Gateway Registry.

Demonstrates the tools primitive backed by a self-hosted MCP Gateway Registry:
  - Discover tools via semantic search
  - List all registered MCP servers and their tools
  - Invoke tools through the gateway proxy
  - Register new tool servers
  - Auto-memory for conversation persistence

The agent dynamically discovers available tools at startup and can
search for new tools during conversation.

Server config:
    providers:
      tools:
        default: "mcp_registry"
        backends:
          mcp_registry:
            backend: agentic_primitives_gateway.primitives.tools.mcp_registry.MCPRegistryProvider
            config:
              base_url: "http://mcp-registry:8080"

Prerequisites:
    pip install -r requirements.txt

    # MCP Gateway Registry must be running
    # See: https://github.com/agentic-community/mcp-gateway-registry

Usage:
    # Set registry credentials
    export MCP_REGISTRY_URL=http://localhost:8080
    export MCP_REGISTRY_TOKEN=your-jwt-token  # optional

    # Start the platform server
    ./run.sh kitchen-sink

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

# Route tools to MCP Registry
platform.set_provider_for("tools", "mcp_registry")
platform.set_provider_for("observability", "langfuse")
platform.set_provider_for("memory", "mem0")

# MCP Registry credentials
platform.set_service_credentials(
    "mcp_registry",
    {
        "url": os.environ.get("MCP_REGISTRY_URL", ""),
        "token": os.environ.get("MCP_REGISTRY_TOKEN", ""),
    },
)

# Optional: Langfuse observability
if os.environ.get("LANGFUSE_PUBLIC_KEY"):
    platform.set_service_credentials(
        "langfuse",
        {
            "public_key": os.environ["LANGFUSE_PUBLIC_KEY"],
            "secret_key": os.environ["LANGFUSE_SECRET_KEY"],
            "base_url": os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"),
        },
    )

AGENT_NAMESPACE = "agent:langchain-mcp-tools"

obs = Observability(platform, namespace=AGENT_NAMESPACE, tags=["langchain-agent", "mcp-tools"])
memory = Memory(platform, namespace=AGENT_NAMESPACE, observability=obs)


# ── MCP tool discovery and invocation ───────────────────────────────


@tool
async def search_available_tools(query: str) -> str:
    """Search for available tools by description or capability.

    Use this to find tools that can help with a task.

    Args:
        query: What kind of tool you're looking for (e.g., "weather", "email", "database").
    """
    result = await platform.search_tools(query, max_results=10)
    tools_list = result.get("tools", [])
    if not tools_list:
        return f"No tools found matching '{query}'."
    lines = []
    for t in tools_list:
        name = t.get("name", "")
        desc = t.get("description", "")[:100]
        lines.append(f"  {name} — {desc}")
    output = f"Found {len(tools_list)} tools:\n" + "\n".join(lines)
    await obs.trace("tools:search", {"query": query}, output)
    return output


@tool
async def list_all_tools() -> str:
    """List all tools available in the MCP registry.

    Shows every registered tool across all MCP servers.
    """
    result = await platform.list_tools()
    tools_list = result.get("tools", [])
    if not tools_list:
        return "No tools are registered in the MCP registry."
    lines = []
    for t in tools_list:
        name = t.get("name", "")
        desc = t.get("description", "")[:80]
        lines.append(f"  {name} — {desc}")
    output = f"{len(tools_list)} tools available:\n" + "\n".join(lines)
    await obs.trace("tools:list", {}, f"{len(tools_list)} tools")
    return output


@tool
async def use_tool(tool_name: str, params: str = "{}") -> str:
    """Invoke a tool from the MCP registry.

    Args:
        tool_name: The full tool name (format: "server-name/tool-name").
        params: JSON string of parameters to pass to the tool.
    """
    import json

    try:
        parsed_params = json.loads(params) if isinstance(params, str) else params
    except json.JSONDecodeError:
        return f"Invalid JSON parameters: {params}"

    try:
        result = await platform.invoke_tool(tool_name, parsed_params)
        output = result.get("result", str(result))
        await obs.trace("tools:invoke", {"tool": tool_name, "params": parsed_params}, str(output)[:500])
        return str(output)
    except Exception as e:
        return f"Tool invocation failed: {e}"


@tool
async def register_mcp_server(name: str, url: str, description: str = "") -> str:
    """Register a new MCP server with the registry.

    Args:
        name: Server name (e.g., "weather-service").
        url: Server URL (e.g., "https://weather-api.example.com/mcp").
        description: Description of what the server provides.
    """
    try:
        await platform.register_tool(
            {
                "name": name,
                "url": url,
                "description": description,
            }
        )
        output = f"Registered MCP server '{name}' at {url}"
        await obs.trace("tools:register", {"name": name, "url": url}, output)
        return output
    except Exception as e:
        return f"Registration failed: {e}"


# ── Memory tools ────────────────────────────────────────────────────


@tool
async def remember(key: str, content: str) -> str:
    """Store information for later recall.

    Args:
        key: A short identifier (e.g., "favorite-tool").
        content: The information to remember.
    """
    return await memory.remember(key, content)


@tool
async def search_memory(query: str) -> str:
    """Search stored memories for relevant information.

    Args:
        query: What to search for.
    """
    return await memory.search(query)


# ── Agent ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a capable assistant with access to a dynamic tool registry and \
long-term memory.

**Tool Discovery & Invocation:**
- `search_available_tools` — find tools by capability (semantic search)
- `list_all_tools` — see everything available in the registry
- `use_tool` — invoke any tool by name with JSON parameters
- `register_mcp_server` — add a new MCP server to the registry

When asked to perform a task, first search for relevant tools. If you \
find one, use it. The tool name format is "server-name/tool-name".

**Memory:**
- `remember` — store useful information
- `search_memory` — recall stored information

Always check your memory before saying you don't know something. \
Remember which tools you've found useful for future reference.
"""


async def main():
    model = ChatBedrock(
        model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
        streaming=True,
    )

    agent = create_agent(
        model,
        tools=[
            search_available_tools,
            list_all_tools,
            use_tool,
            register_mcp_server,
            remember,
            search_memory,
        ],
        system_prompt=SYSTEM_PROMPT,
    )

    # Discover tools at startup
    print("LangChain + MCP Gateway Registry agent ready.")
    print(f"Registry: {os.environ.get('MCP_REGISTRY_URL', 'http://localhost:8080')}")
    print(f"Session: {obs.session_id}")

    try:
        result = await platform.list_tools()
        tool_count = len(result.get("tools", []))
        print(f"Tools available: {tool_count}")
    except Exception as e:
        print(f"Could not connect to registry: {e}")

    print("Type 'quit' to exit.\n")

    await obs.log("info", "MCP tools agent started")

    history = []

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input or user_input.lower() in ("quit", "exit"):
            break

        # Store the user's message in memory
        await memory.store_turn("user", user_input)

        # Build messages with memory context
        context = await memory.recall_context(user_input)
        system_prompt = SYSTEM_PROMPT
        if context:
            system_prompt += "\n\nRelevant context from past conversations:\n" + context

        messages = [{"role": "system", "content": system_prompt}, *history]
        messages.append({"role": "user", "content": user_input})

        # Get the agent's response
        result = await agent.ainvoke({"messages": messages})

        reply = ""
        for msg in reversed(result.get("messages", [])):
            if hasattr(msg, "type") and msg.type == "ai" and msg.content:
                content = msg.content
                if isinstance(content, list):
                    reply = " ".join(
                        block.get("text", "") for block in content if isinstance(block, dict) and block.get("text")
                    )
                else:
                    reply = str(content)
                break

        if reply:
            print(f"\nAssistant: {reply}\n")
            history.append({"role": "user", "content": user_input})
            history.append({"role": "assistant", "content": reply})
            await memory.store_turn("assistant", reply)
            await obs.trace(
                "conversation:turn",
                {"user": user_input},
                reply,
                tags=["conversation"],
            )
        else:
            print("\nAssistant: (no response)\n")

    await obs.log("info", "MCP tools agent stopped")


if __name__ == "__main__":
    asyncio.run(main())
