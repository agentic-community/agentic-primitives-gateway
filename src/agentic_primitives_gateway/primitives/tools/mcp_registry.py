from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Any, ClassVar

from agentic_primitives_gateway.context import get_service_credentials
from agentic_primitives_gateway.primitives.tools.base import ToolsProvider

logger = logging.getLogger(__name__)


class MCPRegistryProvider(ToolsProvider):
    """Tools provider backed by MCP Gateway Registry.

    Connects to a self-hosted MCP Gateway Registry instance for centralized
    MCP tool discovery and invocation. Supports semantic search, server
    registration, and MCP JSON-RPC tool calls.

    See: https://github.com/agentic-community/mcp-gateway-registry

    Credentials (JWT token) can come from:
    1. Client headers: X-Cred-Mcp-Registry-Token, X-Cred-Mcp-Registry-Url
    2. Provider config: base_url, token

    Provider config example::

        backend: agentic_primitives_gateway.primitives.tools.mcp_registry.MCPRegistryProvider
        config:
          base_url: "https://mcp-registry.internal:8080"
          token: ""  # JWT token, or pass via client headers
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        token: str | None = None,
        verify_ssl: bool = True,
        **kwargs: Any,
    ) -> None:
        import os

        self._default_base_url = base_url or os.environ.get("MCP_REGISTRY_URL", "http://localhost:8080")
        self._default_token = token or os.environ.get("MCP_REGISTRY_TOKEN")
        self._verify_ssl = verify_ssl
        logger.info("MCP Registry tools provider initialized (base_url=%s)", self._default_base_url)

    def _resolve_config(self) -> tuple[str, str | None]:
        """Resolve base URL and token from context or defaults.

        Headers: X-Cred-Mcp-Registry-Url and X-Cred-Mcp-Registry-Token
        are parsed by the middleware as service='mcp', keys='registry_url'
        and 'registry_token' (the first dash splits the service name).
        """
        creds = get_service_credentials("mcp_registry") or {}
        base_url = (creds.get("url") or self._default_base_url).rstrip("/")
        token = creds.get("token") or self._default_token
        return base_url, token

    def _headers(self, token: str | None) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    def _parse_sse_json(text: str) -> dict[str, Any]:
        """Parse SSE response to extract JSON data."""
        import json

        for line in text.strip().split("\n"):
            if line.startswith("data: "):
                result: dict[str, Any] = json.loads(line[6:])
                return result
        # Try parsing as plain JSON
        fallback: dict[str, Any] = json.loads(text)
        return fallback

    # Cache of server title -> path mappings
    _server_paths: ClassVar[dict[str, str]] = {}

    async def _resolve_server_path(self, server_title: str, base_url: str, token: str | None) -> str:
        """Resolve a server title to its MCP proxy path."""
        if server_title in self._server_paths:
            return self._server_paths[server_title]

        # Fetch servers to build the mapping
        def _fetch() -> None:
            import httpx

            with httpx.Client(timeout=15, verify=self._verify_ssl) as http:
                resp = http.get(f"{base_url}/v0.1/servers", headers=self._headers(token))
                resp.raise_for_status()
                data = resp.json()

            for entry in data.get("servers", []):
                server = entry.get("server", entry)
                title = server.get("title", server.get("name", ""))
                meta = server.get("_meta", {})
                internal = meta.get("io.mcpgateway/internal", {})
                path = internal.get("path", "")
                if title and path:
                    self._server_paths[title] = path

        await self._run_sync(_fetch)

        if server_title in self._server_paths:
            return self._server_paths[server_title]

        raise ValueError(f"Server '{server_title}' not found in registry")

    async def _run_sync(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(func, *args, **kwargs))

    async def list_tools(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        base_url, token = self._resolve_config()

        def _list() -> list[dict[str, Any]]:
            import httpx

            with httpx.Client(timeout=30, verify=self._verify_ssl) as http:
                # Get servers
                resp = http.get(f"{base_url}/v0.1/servers", headers=self._headers(token))
                resp.raise_for_status()
                data = resp.json()

            tools: list[dict[str, Any]] = []
            servers = data.get("servers", data) if isinstance(data, dict) else data
            for entry in servers:
                server = entry.get("server", entry) if isinstance(entry, dict) else entry
                server_title = server.get("title", server.get("name", ""))
                server_desc = server.get("description", "")
                meta = server.get("_meta", {})
                internal = meta.get("io.mcpgateway/internal", {})
                path = internal.get("path", "")
                health = internal.get("health_status", "unknown")

                if not path or health != "healthy":
                    continue

                # Fetch actual tools via MCP proxy
                try:
                    with httpx.Client(timeout=15, verify=self._verify_ssl) as http:
                        mcp_resp = http.post(
                            f"{base_url}{path}",
                            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                            headers=self._headers(token),
                        )
                        mcp_resp.raise_for_status()
                        mcp_data = self._parse_sse_json(mcp_resp.text)

                    for t in mcp_data.get("result", {}).get("tools", []):
                        tools.append(
                            {
                                "name": f"{server_title}/{t.get('name', '')}",
                                "description": t.get("description", ""),
                                "parameters": t.get("inputSchema", {}),
                                "metadata": {
                                    "server": server_title,
                                    "server_path": path,
                                },
                            }
                        )
                except Exception as e:
                    logger.warning("Failed to fetch tools from %s%s: %s", base_url, path, e)
                    # Fall back to server-level entry
                    tools.append(
                        {
                            "name": server_title,
                            "description": f"{server_desc} ({internal.get('num_tools', 0)} tools)",
                            "parameters": {},
                            "metadata": {"server_path": path},
                        }
                    )

            return tools

        result: list[dict[str, Any]] = await self._run_sync(_list)
        return result

    async def invoke_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        base_url, token = self._resolve_config()

        # tool_name format: "server_title/tool_name"
        parts = tool_name.split("/", 1)
        if len(parts) == 2:
            server_title, actual_tool = parts
        else:
            server_title = tool_name
            actual_tool = tool_name

        # Resolve server path from title
        server_path = await self._resolve_server_path(server_title, base_url, token)

        def _invoke() -> dict[str, Any]:
            import httpx

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": actual_tool,
                    "arguments": params,
                },
            }

            with httpx.Client(timeout=60, verify=self._verify_ssl) as client:
                resp = client.post(
                    f"{base_url}{server_path}",
                    json=payload,
                    headers=self._headers(token),
                )
                resp.raise_for_status()
                result = self._parse_sse_json(resp.text)

            if "error" in result:
                return {"error": result["error"].get("message", str(result["error"]))}

            content = result.get("result", {}).get("content", [])
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("text"):
                    text_parts.append(block["text"])
                elif isinstance(block, str):
                    text_parts.append(block)

            return {
                "result": "\n".join(text_parts) if text_parts else result.get("result"),
            }

        result: dict[str, Any] = await self._run_sync(_invoke)
        return result

    async def register_tool(self, tool_def: dict[str, Any]) -> None:
        base_url, token = self._resolve_config()

        def _register() -> None:
            import httpx

            with httpx.Client(timeout=30, verify=self._verify_ssl) as client:
                resp = client.post(
                    f"{base_url}/api/servers/register",
                    json=tool_def,
                    headers=self._headers(token),
                )
                resp.raise_for_status()

        await self._run_sync(_register)

    async def search_tools(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        base_url, token = self._resolve_config()

        def _search() -> list[dict[str, Any]] | None:
            import httpx

            try:
                with httpx.Client(timeout=30, verify=self._verify_ssl) as client:
                    resp = client.get(
                        f"{base_url}/api/search/semantic",
                        params={"query": query, "max_results": max_results},
                        headers=self._headers(token),
                    )
                    resp.raise_for_status()
                    results = resp.json()

                tools: list[dict[str, Any]] = []
                for item in results if isinstance(results, list) else results.get("results", []):
                    tools.append(
                        {
                            "name": item.get("name", ""),
                            "description": item.get("description", ""),
                            "parameters": item.get("inputSchema", {}),
                            "metadata": item,
                        }
                    )
                return tools
            except Exception:
                logger.debug("Semantic search not available, falling back to list+filter")
                return None

        result = await self._run_sync(_search)
        if result is not None:
            matched_tools: list[dict[str, Any]] = result
            return matched_tools

        # Fall back to list all tools and filter by query
        all_tools = await self.list_tools()
        query_lower = query.lower()
        matched = [
            t
            for t in all_tools
            if query_lower in t.get("name", "").lower() or query_lower in t.get("description", "").lower()
        ]
        return matched[:max_results]

    async def healthcheck(self) -> bool:
        base_url, token = self._resolve_config()
        try:
            import httpx

            with httpx.Client(timeout=5, verify=self._verify_ssl) as client:
                resp = client.get(f"{base_url}/health", headers=self._headers(token))
                return resp.status_code < 500
        except Exception:
            return False
