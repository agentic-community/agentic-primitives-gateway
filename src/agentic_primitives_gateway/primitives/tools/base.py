from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ToolsProvider(ABC):
    """Abstract base class for tools providers.

    Supports MCP-compatible tool registries like AgentCore Gateway
    and MCP Gateway Registry.
    """

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

    async def healthcheck(self) -> bool:
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
