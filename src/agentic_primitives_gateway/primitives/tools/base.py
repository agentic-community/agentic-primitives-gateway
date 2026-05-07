from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ToolsProvider(ABC):
    """Abstract base class for tools providers.

    Supports MCP-compatible tool registries like AgentCore Gateway
    and MCP Gateway Registry.

    The ABC auto-wraps ``invoke_tool``, ``register_tool``,
    ``delete_tool``, and ``register_server`` on every subclass via
    ``__init_subclass__`` to emit ``tool.call`` / ``tool.register`` /
    ``tool.delete`` / ``tool.server.register`` audit events at the
    provider boundary.  Subclasses do not emit these themselves — the
    enrichment is inherited.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        from agentic_primitives_gateway.primitives.tools._audit import (
            wrap_delete_tool,
            wrap_invoke_tool,
            wrap_register_server,
            wrap_register_tool,
        )

        own = cls.__dict__
        if "invoke_tool" in own:
            cls.invoke_tool = wrap_invoke_tool(own["invoke_tool"])  # type: ignore[method-assign]
        if "register_tool" in own:
            cls.register_tool = wrap_register_tool(own["register_tool"])  # type: ignore[method-assign]
        if "delete_tool" in own:
            cls.delete_tool = wrap_delete_tool(own["delete_tool"])  # type: ignore[method-assign]
        if "register_server" in own:
            cls.register_server = wrap_register_server(own["register_server"])  # type: ignore[method-assign]

    @abstractmethod
    async def register_tool(self, tool_def: dict[str, Any]) -> None: ...

    @abstractmethod
    async def list_tools(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def invoke_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]: ...

    async def search_tools(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        """Semantic search for tools by description/capability."""
        # Default: fall back to list + filter
        tools = await self.list_tools()
        query_lower = query.lower()
        matched = [
            t
            for t in tools
            if query_lower in t.get("name", "").lower() or query_lower in t.get("description", "").lower()
        ]
        return matched[:max_results]

    async def healthcheck(self) -> bool | str:
        return True

    # ── Tool retrieval & deletion (optional) ─────────────────────────

    async def get_tool(self, tool_name: str) -> dict[str, Any]:
        raise NotImplementedError

    async def delete_tool(self, tool_name: str) -> None:
        raise NotImplementedError

    # ── Server management (optional) ─────────────────────────────────

    async def list_servers(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def get_server(self, server_name: str) -> dict[str, Any]:
        raise NotImplementedError

    async def register_server(self, server_config: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError
