from __future__ import annotations

import logging
from typing import Any

from agentic_primitives_gateway.primitives.tools.base import ToolsProvider

logger = logging.getLogger(__name__)


class NoopToolsProvider(ToolsProvider):
    """No-op tools provider that logs calls but does nothing."""

    def __init__(self, **kwargs: Any) -> None:
        logger.info("NoopToolsProvider initialized")

    async def register_tool(self, tool_def: dict[str, Any]) -> None:
        logger.debug("noop register_tool: %s", tool_def)

    async def list_tools(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        logger.debug("noop list_tools: %s", filters)
        return []

    async def invoke_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        logger.debug("noop invoke_tool: %s %s", tool_name, params)
        return {}
