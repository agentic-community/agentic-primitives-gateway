from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from agentic_primitives_gateway.models.agents import PrimitiveConfig
from agentic_primitives_gateway.registry import registry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolDefinition:
    """A tool that can be given to an LLM and executed server-side."""

    name: str
    description: str
    primitive: str
    input_schema: dict[str, Any]
    handler: Callable[..., Awaitable[str]]


# ── Memory tool handlers ─────────────────────────────────────────────


async def _memory_store(namespace: str, key: str, content: str, source: str = "") -> str:
    metadata = {"source": source} if source else {}
    await registry.memory.store(namespace=namespace, key=key, content=content, metadata=metadata)
    return f"Stored memory '{key}'."


async def _memory_retrieve(namespace: str, key: str) -> str:
    record = await registry.memory.retrieve(namespace=namespace, key=key)
    if record is None:
        return f"No memory found for key '{key}'."
    return record.content


async def _memory_search(namespace: str, query: str, top_k: int = 5) -> str:
    results = await registry.memory.search(namespace=namespace, query=query, top_k=top_k)
    if not results:
        return "No memories found."
    lines = [f"- [{r.score:.2f}] {r.record.key}: {r.record.content}" for r in results]
    return "\n".join(lines)


async def _memory_delete(namespace: str, key: str) -> str:
    deleted = await registry.memory.delete(namespace=namespace, key=key)
    return f"Deleted: {deleted}"


async def _memory_list(namespace: str, limit: int = 20) -> str:
    records = await registry.memory.list_memories(namespace=namespace, limit=limit)
    if not records:
        return "No memories found."
    lines = [f"- {r.key}: {r.content[:100]}" for r in records]
    return "\n".join(lines)


# ── Code interpreter tool handlers ───────────────────────────────────


async def _code_execute(session_id: str, code: str, language: str = "python") -> str:
    result = await registry.code_interpreter.execute(session_id=session_id, code=code, language=language)
    return json.dumps(result, default=str)


# ── Browser tool handlers ────────────────────────────────────────────


async def _browser_navigate(session_id: str, url: str) -> str:
    logger.info("Browser[%s] navigate → %s", session_id, url)
    result = await registry.browser.navigate(session_id=session_id, url=url)
    logger.info("Browser[%s] navigate complete: %s", session_id, str(result)[:200])
    return json.dumps(result, default=str)


async def _browser_read_page(session_id: str) -> str:
    logger.info("Browser[%s] read_page", session_id)
    content = await registry.browser.get_page_content(session_id=session_id)
    logger.info("Browser[%s] read_page: %d chars", session_id, len(content))
    return content


async def _browser_click(session_id: str, selector: str) -> str:
    logger.info("Browser[%s] click → %s", session_id, selector)
    result = await registry.browser.click(session_id=session_id, selector=selector)
    return json.dumps(result, default=str)


async def _browser_type(session_id: str, selector: str, text: str) -> str:
    logger.info("Browser[%s] type → %s: %s", session_id, selector, text[:50])
    result = await registry.browser.type_text(session_id=session_id, selector=selector, text=text)
    return json.dumps(result, default=str)


async def _browser_screenshot(session_id: str) -> str:
    logger.info("Browser[%s] screenshot", session_id)
    result = await registry.browser.screenshot(session_id=session_id)
    logger.info("Browser[%s] screenshot: %d chars", session_id, len(result))
    # Return a summary instead of raw base64 to avoid blowing up token count
    return f"Screenshot captured ({len(result)} bytes). Use read_page to see text content instead."


async def _browser_evaluate_js(session_id: str, expression: str) -> str:
    logger.info("Browser[%s] evaluate_js: %s", session_id, expression[:100])
    result = await registry.browser.evaluate(session_id=session_id, expression=expression)
    return json.dumps(result, default=str)


# ── Tools (MCP/external) handlers ───────────────────────────────────


async def _tools_search(query: str, max_results: int = 10) -> str:
    results = await registry.tools.search_tools(query=query, max_results=max_results)
    if not results:
        return "No tools found."
    lines = [f"- {t.get('name', '?')}: {t.get('description', '')}" for t in results]
    return "\n".join(lines)


async def _tools_invoke(tool_name: str, params: str = "{}") -> str:
    try:
        parsed_params = json.loads(params) if isinstance(params, str) else params
    except (json.JSONDecodeError, TypeError):
        parsed_params = {}
    result = await registry.tools.invoke_tool(tool_name=tool_name, params=parsed_params)
    return json.dumps(result, default=str)


# ── Identity handlers ────────────────────────────────────────────────


async def _identity_get_token(credential_provider: str, scopes: str = "") -> str:
    scope_list = [s.strip() for s in scopes.split(",") if s.strip()] if scopes else None
    kwargs: dict[str, Any] = {
        "credential_provider": credential_provider,
        "workload_token": "",
    }
    if scope_list:
        kwargs["scopes"] = scope_list
    result = await registry.identity.get_token(**kwargs)
    return json.dumps(result, default=str)


async def _identity_get_api_key(credential_provider: str) -> str:
    result = await registry.identity.get_api_key(credential_provider=credential_provider, workload_token="")
    return json.dumps(result, default=str)


# ── Tool catalog ─────────────────────────────────────────────────────

_TOOL_CATALOG: dict[str, list[ToolDefinition]] = {
    "memory": [
        ToolDefinition(
            name="remember",
            description="Store information in long-term memory.",
            primitive="memory",
            input_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "A short identifier for this memory."},
                    "content": {"type": "string", "description": "The information to remember."},
                    "source": {"type": "string", "description": "Optional source.", "default": ""},
                },
                "required": ["key", "content"],
            },
            handler=_memory_store,
        ),
        ToolDefinition(
            name="recall",
            description="Retrieve a memory by its exact key.",
            primitive="memory",
            input_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "The key to look up."},
                },
                "required": ["key"],
            },
            handler=_memory_retrieve,
        ),
        ToolDefinition(
            name="search_memory",
            description="Search memories using semantic similarity.",
            primitive="memory",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for."},
                    "top_k": {"type": "integer", "description": "Maximum results.", "default": 5},
                },
                "required": ["query"],
            },
            handler=_memory_search,
        ),
        ToolDefinition(
            name="forget",
            description="Delete a memory by key.",
            primitive="memory",
            input_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "The key of the memory to delete."},
                },
                "required": ["key"],
            },
            handler=_memory_delete,
        ),
        ToolDefinition(
            name="list_memories",
            description="List all stored memories.",
            primitive="memory",
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Maximum memories to show.", "default": 20},
                },
                "required": [],
            },
            handler=_memory_list,
        ),
    ],
    "code_interpreter": [
        ToolDefinition(
            name="execute_code",
            description="Execute code in a sandboxed environment. State persists across calls.",
            primitive="code_interpreter",
            input_schema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "The code to execute."},
                    "language": {
                        "type": "string",
                        "description": "Programming language.",
                        "default": "python",
                    },
                },
                "required": ["code"],
            },
            handler=_code_execute,
        ),
    ],
    "browser": [
        ToolDefinition(
            name="navigate",
            description="Navigate the browser to a URL.",
            primitive="browser",
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to visit."},
                },
                "required": ["url"],
            },
            handler=_browser_navigate,
        ),
        ToolDefinition(
            name="read_page",
            description="Read the current page content as text.",
            primitive="browser",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=_browser_read_page,
        ),
        ToolDefinition(
            name="click",
            description="Click an element on the page.",
            primitive="browser",
            input_schema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector of the element."},
                },
                "required": ["selector"],
            },
            handler=_browser_click,
        ),
        ToolDefinition(
            name="type_text",
            description="Type text into an input element.",
            primitive="browser",
            input_schema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector of the input."},
                    "text": {"type": "string", "description": "Text to type."},
                },
                "required": ["selector", "text"],
            },
            handler=_browser_type,
        ),
        ToolDefinition(
            name="screenshot",
            description="Take a screenshot of the current page.",
            primitive="browser",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=_browser_screenshot,
        ),
        ToolDefinition(
            name="evaluate_js",
            description="Evaluate a JavaScript expression in the browser.",
            primitive="browser",
            input_schema={
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "JS expression to evaluate."},
                },
                "required": ["expression"],
            },
            handler=_browser_evaluate_js,
        ),
    ],
    "tools": [
        ToolDefinition(
            name="search_tools",
            description="Search for available tools by query.",
            primitive="tools",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query."},
                    "max_results": {"type": "integer", "description": "Max results.", "default": 10},
                },
                "required": ["query"],
            },
            handler=_tools_search,
        ),
        ToolDefinition(
            name="invoke_tool",
            description="Invoke an external tool by name.",
            primitive="tools",
            input_schema={
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string", "description": "Name of the tool to invoke."},
                    "params": {
                        "type": "string",
                        "description": "JSON string of parameters.",
                        "default": "{}",
                    },
                },
                "required": ["tool_name"],
            },
            handler=_tools_invoke,
        ),
    ],
    "identity": [
        ToolDefinition(
            name="get_token",
            description="Get an OAuth2 token from a credential provider.",
            primitive="identity",
            input_schema={
                "type": "object",
                "properties": {
                    "credential_provider": {
                        "type": "string",
                        "description": "Credential provider name.",
                    },
                    "scopes": {
                        "type": "string",
                        "description": "Comma-separated scopes.",
                        "default": "",
                    },
                },
                "required": ["credential_provider"],
            },
            handler=_identity_get_token,
        ),
        ToolDefinition(
            name="get_api_key",
            description="Get an API key from a credential provider.",
            primitive="identity",
            input_schema={
                "type": "object",
                "properties": {
                    "credential_provider": {
                        "type": "string",
                        "description": "Credential provider name.",
                    },
                },
                "required": ["credential_provider"],
            },
            handler=_identity_get_api_key,
        ),
    ],
}


# ── Agent delegation handler ──────────────────────────────────────────

MAX_AGENT_DEPTH = 3


async def _call_agent(agent_name: str, message: str) -> str:
    """Placeholder — replaced at build time with a bound handler that has store/runner/depth."""
    raise RuntimeError("_call_agent must be bound with store, runner, and depth")


# ── Public API ───────────────────────────────────────────────────────


def build_tool_list(
    spec_primitives: dict[str, PrimitiveConfig],
    namespace: str,
    session_ctx: dict[str, str] | None = None,
    *,
    agent_store: Any | None = None,
    agent_runner: Any | None = None,
    agent_depth: int = 0,
) -> list[ToolDefinition]:
    """Build the list of enabled tools for an agent, with bound context.

    For memory tools, binds the ``namespace`` parameter via functools.partial.
    For browser/code_interpreter tools, binds the ``session_id`` if available.
    For agents, dynamically creates delegation tools from the agent store.
    The LLM-facing input_schema excludes these bound parameters.
    """
    session_ctx = session_ctx or {}
    tools: list[ToolDefinition] = []

    for primitive_name, config in spec_primitives.items():
        if not config.enabled:
            continue

        # Agent delegation tools are dynamic — built from the store
        if primitive_name == "agents":
            if agent_store is None or agent_runner is None:
                logger.warning("agents primitive enabled but no store/runner provided — skipping")
                continue
            if agent_depth >= MAX_AGENT_DEPTH:
                logger.info("Agent depth %d >= max %d — skipping sub-agent tools", agent_depth, MAX_AGENT_DEPTH)
                continue
            tools.extend(_build_agent_tools(config, agent_store, agent_runner, agent_depth))
            continue

        catalog_tools = _TOOL_CATALOG.get(primitive_name, [])
        for tool in catalog_tools:
            # Filter by allowed tool names if specified
            if config.tools is not None and tool.name not in config.tools:
                continue

            # Bind context parameters so the LLM doesn't need to provide them
            bound_handler = tool.handler
            if primitive_name == "memory":
                bound_handler = partial(tool.handler, namespace=namespace)
            elif primitive_name in ("code_interpreter", "browser"):
                sid = session_ctx.get(primitive_name, "")
                if sid:
                    bound_handler = partial(tool.handler, session_id=sid)

            tools.append(
                ToolDefinition(
                    name=tool.name,
                    description=tool.description,
                    primitive=tool.primitive,
                    input_schema=tool.input_schema,
                    handler=bound_handler,
                )
            )

    return tools


def _build_agent_tools(
    config: PrimitiveConfig,
    store: Any,
    runner: Any,
    depth: int,
) -> list[ToolDefinition]:
    """Build delegation tools for sub-agents listed in config.tools."""
    import asyncio

    agent_names: list[str] = config.tools or []
    if not agent_names:
        # If no specific agents listed, try to get all from store
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're in an async context — can't block. The caller should
                # have listed specific agent names in config.tools.
                logger.warning("agents.tools is empty and we're in async context — list agent names explicitly")
                return []
        except RuntimeError:
            return []

    tools: list[ToolDefinition] = []
    for agent_name in agent_names:
        # Create a bound handler for this specific sub-agent
        async def _handler(message: str, *, _name: str = agent_name) -> str:
            spec = await store.get(_name)
            if spec is None:
                return f"Agent '{_name}' not found."
            try:
                response = await runner.run(spec, message=message, _depth=depth + 1)
                # Include the response text plus any tool artifacts (code, results, etc.)
                parts = [response.response]
                if response.artifacts:
                    parts.append("\n\n--- Tool Artifacts ---")
                    for artifact in response.artifacts:
                        parts.append(f"\n[{artifact.tool_name}]")
                        if artifact.tool_input:
                            # For code tools, include the code that was written
                            code = artifact.tool_input.get("code", "")
                            if code:
                                parts.append(f"```\n{code}\n```")
                        if artifact.output:
                            parts.append(f"Output: {artifact.output}")
                return "\n".join(parts)
            except Exception as e:
                return f"Agent '{_name}' failed: {type(e).__name__}: {e}"

        tools.append(
            ToolDefinition(
                name=f"call_{agent_name}",
                description=f"Delegate a task to the '{agent_name}' agent. Send it a message and get back its response.",
                primitive="agents",
                input_schema={
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": f"The message/task to send to the {agent_name} agent.",
                        },
                    },
                    "required": ["message"],
                },
                handler=_handler,
            )
        )

    return tools


def to_gateway_tools(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    """Convert ToolDefinitions to the dict format for route_request."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in tools
    ]


async def execute_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    tools: list[ToolDefinition],
) -> str:
    """Execute a tool by name with the given input."""
    for tool in tools:
        if tool.name == tool_name:
            try:
                return await tool.handler(**tool_input)
            except TypeError:
                # If handler has bound params that conflict, try without them
                logger.warning("Tool %s handler type error, retrying with raw input", tool_name)
                return str(await tool.handler(**tool_input))
    raise ValueError(f"Unknown tool: {tool_name}")
