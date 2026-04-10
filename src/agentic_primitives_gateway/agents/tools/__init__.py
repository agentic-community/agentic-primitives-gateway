"""Agent tool system — catalog, handlers, delegation, and public API."""

from agentic_primitives_gateway.agents.tools.catalog import (
    _TOOL_CATALOG,
    ToolDefinition,
    build_tool_list,
    execute_tool,
    to_llm_tools,
)
from agentic_primitives_gateway.agents.tools.delegation import (
    MAX_AGENT_DEPTH,
    _build_agent_tools,
)

__all__ = [
    "MAX_AGENT_DEPTH",
    "_TOOL_CATALOG",
    "ToolDefinition",
    "_build_agent_tools",
    "build_tool_list",
    "execute_tool",
    "to_llm_tools",
]
