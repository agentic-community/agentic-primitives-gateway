"""Tool catalog, builder, and executor.

The catalog maps primitive names to lists of ``ToolDefinition`` objects.
``build_tool_list`` constructs the final tool list for an agent run,
binding context parameters and optionally including delegation tools.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from agentic_primitives_gateway.agents.tools.handlers import (
    browser_click,
    browser_evaluate_js,
    browser_navigate,
    browser_read_page,
    browser_screenshot,
    browser_type,
    code_execute,
    identity_get_api_key,
    identity_get_token,
    memory_delete,
    memory_list,
    memory_retrieve,
    memory_search,
    memory_store,
    tools_invoke,
    tools_search,
)
from agentic_primitives_gateway.models.agents import PrimitiveConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolDefinition:
    """A tool that can be given to an LLM and executed server-side."""

    name: str
    description: str
    primitive: str
    input_schema: dict[str, Any]
    handler: Callable[..., Awaitable[str]]


# ── Static tool catalog ──────────────────────────────────────────────

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
            handler=memory_store,
        ),
        ToolDefinition(
            name="recall",
            description="Retrieve a memory by its exact key.",
            primitive="memory",
            input_schema={
                "type": "object",
                "properties": {"key": {"type": "string", "description": "The key to look up."}},
                "required": ["key"],
            },
            handler=memory_retrieve,
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
            handler=memory_search,
        ),
        ToolDefinition(
            name="forget",
            description="Delete a memory by key.",
            primitive="memory",
            input_schema={
                "type": "object",
                "properties": {"key": {"type": "string", "description": "The key of the memory to delete."}},
                "required": ["key"],
            },
            handler=memory_delete,
        ),
        ToolDefinition(
            name="list_memories",
            description="List all stored memories.",
            primitive="memory",
            input_schema={
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "Maximum memories to show.", "default": 20}},
                "required": [],
            },
            handler=memory_list,
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
                    "language": {"type": "string", "description": "Programming language.", "default": "python"},
                },
                "required": ["code"],
            },
            handler=code_execute,
        ),
    ],
    "browser": [
        ToolDefinition(
            name="navigate",
            description="Navigate the browser to a URL.",
            primitive="browser",
            input_schema={
                "type": "object",
                "properties": {"url": {"type": "string", "description": "The URL to visit."}},
                "required": ["url"],
            },
            handler=browser_navigate,
        ),
        ToolDefinition(
            name="read_page",
            description="Read the current page content as text.",
            primitive="browser",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=browser_read_page,
        ),
        ToolDefinition(
            name="click",
            description="Click an element on the page.",
            primitive="browser",
            input_schema={
                "type": "object",
                "properties": {"selector": {"type": "string", "description": "CSS selector of the element."}},
                "required": ["selector"],
            },
            handler=browser_click,
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
            handler=browser_type,
        ),
        ToolDefinition(
            name="screenshot",
            description="Take a screenshot of the current page.",
            primitive="browser",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=browser_screenshot,
        ),
        ToolDefinition(
            name="evaluate_js",
            description="Evaluate a JavaScript expression in the browser.",
            primitive="browser",
            input_schema={
                "type": "object",
                "properties": {"expression": {"type": "string", "description": "JS expression to evaluate."}},
                "required": ["expression"],
            },
            handler=browser_evaluate_js,
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
            handler=tools_search,
        ),
        ToolDefinition(
            name="invoke_tool",
            description="Invoke an external tool by name.",
            primitive="tools",
            input_schema={
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string", "description": "Name of the tool to invoke."},
                    "params": {"type": "string", "description": "JSON string of parameters.", "default": "{}"},
                },
                "required": ["tool_name"],
            },
            handler=tools_invoke,
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
                    "credential_provider": {"type": "string", "description": "Credential provider name."},
                    "scopes": {"type": "string", "description": "Comma-separated scopes.", "default": ""},
                },
                "required": ["credential_provider"],
            },
            handler=identity_get_token,
        ),
        ToolDefinition(
            name="get_api_key",
            description="Get an API key from a credential provider.",
            primitive="identity",
            input_schema={
                "type": "object",
                "properties": {"credential_provider": {"type": "string", "description": "Credential provider name."}},
                "required": ["credential_provider"],
            },
            handler=identity_get_api_key,
        ),
    ],
}


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
    """Build the list of enabled tools for an agent, with bound context."""
    from agentic_primitives_gateway.agents.tools.delegation import MAX_AGENT_DEPTH, _build_agent_tools

    session_ctx = session_ctx or {}
    tools: list[ToolDefinition] = []

    for primitive_name, config in spec_primitives.items():
        if not config.enabled:
            continue

        if primitive_name == "agents":
            if agent_store is None or agent_runner is None:
                logger.warning("agents primitive enabled but no store/runner provided — skipping")
                continue
            if agent_depth >= MAX_AGENT_DEPTH:
                logger.info("Agent depth %d >= max %d — skipping sub-agent tools", agent_depth, MAX_AGENT_DEPTH)
                continue
            tools.extend(_build_agent_tools(config, agent_store, agent_runner, agent_depth))
            continue

        for tool in _TOOL_CATALOG.get(primitive_name, []):
            if config.tools is not None and tool.name not in config.tools:
                continue

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


def to_gateway_tools(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    """Convert ToolDefinitions to the dict format for route_request."""
    return [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools]


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
                logger.warning("Tool %s handler type error, retrying with raw input", tool_name)
                return str(await tool.handler(**tool_input))
    raise ValueError(f"Unknown tool: {tool_name}")
