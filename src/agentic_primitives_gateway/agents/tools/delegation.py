"""Agent-as-tool delegation — allows agents to call other agents."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from agentic_primitives_gateway.agents.tools.catalog import ToolDefinition
from agentic_primitives_gateway.models.agents import PrimitiveConfig

logger = logging.getLogger(__name__)

MAX_AGENT_DEPTH = 3


def _build_agent_tools(
    config: PrimitiveConfig,
    store: Any,
    runner: Any,
    depth: int,
) -> list[ToolDefinition]:
    """Build delegation tools for sub-agents listed in config.tools."""
    agent_names: list[str] = config.tools or []
    if not agent_names:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                logger.warning("agents.tools is empty and we're in async context — list agent names explicitly")
                return []
        except RuntimeError:
            return []

    tools: list[ToolDefinition] = []
    for agent_name in agent_names:

        async def _handler(message: str, *, _name: str = agent_name) -> str:
            spec = await store.get(_name)
            if spec is None:
                return f"Agent '{_name}' not found."
            try:
                response = await runner.run(spec, message=message, _depth=depth + 1)
                parts = [response.response]
                if response.artifacts:
                    parts.append("\n\n--- Tool Artifacts ---")
                    for artifact in response.artifacts:
                        parts.append(f"\n[{artifact.tool_name}]")
                        if artifact.tool_input:
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
