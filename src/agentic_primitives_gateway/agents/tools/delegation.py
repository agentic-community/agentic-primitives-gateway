"""Agent-as-tool delegation — allows agents to call other agents."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from agentic_primitives_gateway.agents.tools.catalog import ToolDefinition
from agentic_primitives_gateway.models.agents import PrimitiveConfig

logger = logging.getLogger(__name__)

MAX_AGENT_DEPTH = 3


async def _resolve_sub_agent(store: Any, ref: str, parent_owner_id: str) -> Any:
    """Resolve a sub-agent reference inside the *parent* agent's namespace.

    Qualified ``owner:name`` refs go straight to ``resolve_qualified``.
    Bare refs first try ``(parent_owner_id, name)``, then fall back to
    ``("system", name)`` so system-built-in sub-agents stay accessible.
    This matches the run-time rule documented in ``docs/concepts/
    agent-versioning.md``: sub-agent delegation resolves in the *running
    agent's* owner namespace, not the caller's.
    """
    if ":" in ref:
        owner_id, _, bare = ref.partition(":")
        return await store.resolve_qualified(owner_id, bare)
    spec = await store.resolve_qualified(parent_owner_id, ref)
    if spec is None and parent_owner_id != "system":
        spec = await store.resolve_qualified("system", ref)
    return spec


def _build_agent_tools(
    config: PrimitiveConfig,
    store: Any,
    runner: Any,
    depth: int,
    parent_owner_id: str = "system",
) -> list[ToolDefinition]:
    """Build delegation tools for sub-agents listed in ``config.tools``.

    ``parent_owner_id`` scopes bare sub-refs to the running agent's owner
    namespace, so Alice's forked ``researcher`` delegating to ``analyst``
    resolves ``(alice, analyst)`` first and only falls back to system.
    """
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

        async def _handler(
            message: str,
            *,
            _name: str = agent_name,
            _parent_owner: str = parent_owner_id,
        ) -> str:
            spec = await _resolve_sub_agent(store, _name, _parent_owner)
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

        # The tool name strips any qualifier — ``alice:analyst`` becomes
        # ``call_analyst`` for prompt ergonomics.  The handler still uses
        # the full ref (``_name``) to resolve the spec.
        display_name = agent_name.split(":", 1)[-1]

        tools.append(
            ToolDefinition(
                name=f"call_{display_name}",
                description=(
                    f"Delegate a task to the '{display_name}' agent. Send it a message and get back its response."
                ),
                primitive="agents",
                input_schema={
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": f"The message/task to send to the {display_name} agent.",
                        },
                    },
                    "required": ["message"],
                },
                handler=_handler,
            )
        )

    return tools
