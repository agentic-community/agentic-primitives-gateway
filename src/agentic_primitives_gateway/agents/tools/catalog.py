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
    agent_create,
    agent_delegate_to,
    agent_delete,
    agent_list,
    agent_list_primitives,
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
    task_add_note,
    task_claim,
    task_create,
    task_get,
    task_get_available,
    task_list,
    task_update,
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
    "task_board": [
        ToolDefinition(
            name="create_task",
            description="Create a new task on the team task board.",
            primitive="task_board",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short title for the task."},
                    "description": {
                        "type": "string",
                        "description": "Detailed description of what to do.",
                        "default": "",
                    },
                    "depends_on": {
                        "type": "string",
                        "description": "Comma-separated task IDs this depends on.",
                        "default": "",
                    },
                    "priority": {"type": "integer", "description": "Priority (higher = more important).", "default": 0},
                    "assigned_to": {
                        "type": "string",
                        "description": "Name of the worker agent this task should be assigned to.",
                        "default": "",
                    },
                },
                "required": ["title"],
            },
            handler=task_create,
        ),
        ToolDefinition(
            name="list_tasks",
            description="List tasks on the board. Optionally filter by status or assignee.",
            primitive="task_board",
            input_schema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Filter by status (pending/claimed/in_progress/done/failed).",
                        "default": "",
                    },
                    "assigned_to": {"type": "string", "description": "Filter by assigned agent.", "default": ""},
                },
                "required": [],
            },
            handler=task_list,
        ),
        ToolDefinition(
            name="get_task",
            description="Get full details of a specific task by ID.",
            primitive="task_board",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task ID."},
                },
                "required": ["task_id"],
            },
            handler=task_get,
        ),
        ToolDefinition(
            name="claim_task",
            description="Claim an available task to work on. Fails if already claimed or dependencies not met.",
            primitive="task_board",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task ID to claim."},
                },
                "required": ["task_id"],
            },
            handler=task_claim,
        ),
        ToolDefinition(
            name="complete_task",
            description="Mark a task as done with a result.",
            primitive="task_board",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task ID to complete."},
                    "result": {"type": "string", "description": "The result/output of this task."},
                },
                "required": ["task_id", "result"],
            },
            handler=task_update,
        ),
        ToolDefinition(
            name="fail_task",
            description="Mark a task as failed with an explanation.",
            primitive="task_board",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task ID that failed."},
                    "result": {"type": "string", "description": "Explanation of why it failed."},
                },
                "required": ["task_id", "result"],
            },
            handler=task_update,
        ),
        ToolDefinition(
            name="add_task_note",
            description="Add a note to a task for other agents to read.",
            primitive="task_board",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task ID."},
                    "content": {"type": "string", "description": "The note content."},
                },
                "required": ["task_id", "content"],
            },
            handler=task_add_note,
        ),
        ToolDefinition(
            name="get_available_tasks",
            description="Get tasks that are available to claim (pending with all dependencies met).",
            primitive="task_board",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=task_get_available,
        ),
    ],
    "agent_management": [
        ToolDefinition(
            name="create_agent",
            description="Create a new specialized agent with a name, model, system prompt, and enabled primitives. Use list_primitives first to see what capabilities are available.",
            primitive="agent_management",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Unique name for the new agent (e.g. 'hn-scraper', 'data-analyst').",
                    },
                    "model": {
                        "type": "string",
                        "description": "LLM model ID.",
                        "default": "us.anthropic.claude-sonnet-4-20250514-v1:0",
                    },
                    "system_prompt": {
                        "type": "string",
                        "description": "System prompt that defines the agent's behavior and focus.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Short description of what this agent does.",
                        "default": "",
                    },
                    "primitives": {
                        "type": "string",
                        "description": 'JSON object of primitives to enable. Example: \'{"memory": {"enabled": true}, "browser": {"enabled": true}}\'',
                        "default": "{}",
                    },
                },
                "required": ["name", "system_prompt"],
            },
            handler=agent_create,
        ),
        ToolDefinition(
            name="list_agents",
            description="List all existing agents with their descriptions and enabled primitives.",
            primitive="agent_management",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=agent_list,
        ),
        ToolDefinition(
            name="list_primitives",
            description="List all available primitives and their tools, so you know what capabilities to give a new agent.",
            primitive="agent_management",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=agent_list_primitives,
        ),
        ToolDefinition(
            name="delete_agent",
            description="Delete an agent you previously created. Use for cleanup of ephemeral agents.",
            primitive="agent_management",
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Name of the agent to delete."}},
                "required": ["name"],
            },
            handler=agent_delete,
        ),
        ToolDefinition(
            name="delegate_to",
            description="Delegate a task to any agent by name. The agent runs its full tool-call loop with its own primitives and returns the result. Works with both pre-existing and newly created agents.",
            primitive="agent_management",
            input_schema={
                "type": "object",
                "properties": {
                    "agent_name": {"type": "string", "description": "Name of the agent to delegate to."},
                    "message": {"type": "string", "description": "The message/task to send to the agent."},
                },
                "required": ["agent_name", "message"],
            },
            handler=agent_delegate_to,
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
    team_run_id: str | None = None,
    agent_name: str | None = None,
) -> list[ToolDefinition]:
    """Build the final tool list for an agent run from its primitive config.

    Each enabled primitive contributes tools from _TOOL_CATALOG. Context
    parameters are bound via functools.partial so the LLM doesn't need to
    provide them:
      - memory tools get ``namespace`` bound (agent-scoped, no session_id)
      - browser/code_interpreter tools get ``session_id`` bound (if session started)
      - agent delegation tools are built dynamically from the agent store

    The "agents" pseudo-primitive is special: its tools are not in the static
    catalog but built at runtime from the agent store. The delegation import
    is deferred to avoid circular imports (delegation → catalog → delegation).
    """
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
            elif primitive_name == "agent_management":
                # Bind agent_store to create/list/delete, and store+runner+depth to delegate_to
                if agent_store is not None:
                    if tool.name in ("create_agent", "list_agents", "delete_agent"):
                        bound_handler = partial(tool.handler, agent_store=agent_store)
                    elif tool.name == "delegate_to" and agent_runner is not None:
                        bound_handler = partial(
                            tool.handler,
                            agent_store=agent_store,
                            agent_runner=agent_runner,
                            depth=agent_depth,
                        )
                    # list_primitives needs no binding
            elif primitive_name == "task_board" and team_run_id:
                bound_handler = partial(tool.handler, team_run_id=team_run_id)
                # Also bind agent_name for tools that need it
                if agent_name and tool.name in ("create_task", "claim_task", "add_task_note", "get_available_tasks"):
                    bound_handler = partial(bound_handler, agent_name=agent_name)
                # Bind status for complete_task/fail_task
                if tool.name == "complete_task":
                    bound_handler = partial(bound_handler, status="done")
                elif tool.name == "fail_task":
                    bound_handler = partial(bound_handler, status="failed")

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
