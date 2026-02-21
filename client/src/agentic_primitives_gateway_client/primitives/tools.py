"""Tools helper for the Agentic Primitives Gateway client.

Provides a convenience wrapper around the tools (registration, invocation,
search) endpoints.

Usage (async)::

    from agentic_primitives_gateway_client import AgenticPlatformClient, Tools

    client = AgenticPlatformClient("http://localhost:8000", ...)
    tools = Tools(client)

    await tools.register(name="my-tool", description="Does things", parameters={})
    result = await tools.invoke("my-tool", {"arg": "value"})
    found = await tools.search("things")

Usage (sync)::

    tools.register_sync(name="my-tool", description="Does things", parameters={})
    result = tools.invoke_sync("my-tool", {"arg": "value"})
"""

from __future__ import annotations

import asyncio
from typing import Any

from agentic_primitives_gateway_client.client import AgenticPlatformClient


class Tools:
    """Helper for the tools primitive — register, invoke, search tools."""

    def __init__(self, client: AgenticPlatformClient) -> None:
        self._client = client
        self._loop: asyncio.AbstractEventLoop | None = None

    async def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Register a tool with the server."""
        tool_def: dict[str, Any] = {
            "name": name,
            "description": description,
            "parameters": parameters or {},
            "metadata": metadata or {},
        }
        return await self._client.register_tool(tool_def)

    async def list_tools(self) -> list[dict[str, Any]]:
        """List registered tools."""
        result = await self._client.list_tools()
        tools: list[dict[str, Any]] = result.get("tools", [])
        return tools

    async def invoke(self, tool_name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Invoke a tool by name."""
        return await self._client.invoke_tool(tool_name, params or {})

    async def search(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        """Search for tools by query."""
        result = await self._client.search_tools(query, max_results)
        tools: list[dict[str, Any]] = result.get("tools", [])
        return tools

    # ── Sync wrappers ───────────────────────────────────────────────

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _sync(self, coro: Any) -> Any:
        return self._get_loop().run_until_complete(coro)

    def register_sync(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = self._sync(self.register(name, description, parameters, metadata))
        return result

    def list_tools_sync(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._sync(self.list_tools())
        return result

    def invoke_sync(self, tool_name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        result: dict[str, Any] = self._sync(self.invoke(tool_name, params))
        return result

    def search_sync(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._sync(self.search(query, max_results))
        return result
