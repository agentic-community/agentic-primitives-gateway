from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, ClassVar

from agentic_primitives_gateway.context import get_service_credentials
from agentic_primitives_gateway.primitives._sync import SyncRunnerMixin
from agentic_primitives_gateway.primitives.tools.base import ToolsProvider

logger = logging.getLogger(__name__)

# MCP protocol constants
_MCP_PROTOCOL_VERSION = "2025-03-26"
_MCP_CLIENT_INFO = {"name": "agentic-primitives-gateway", "version": "1.0"}


class MCPRegistryProvider(SyncRunnerMixin, ToolsProvider):
    """Tools provider backed by MCP Gateway Registry.

    Connects to a self-hosted MCP Gateway Registry instance for centralized
    MCP tool discovery and invocation. Uses the MCP streamable-http transport
    protocol: ``initialize`` to obtain a session, then ``tools/list`` and
    ``tools/call`` with the session ID header.

    See: https://github.com/agentic-community/mcp-gateway-registry

    Credentials (JWT token) can come from:
    1. Client headers: X-Cred-Mcp-Registry-Token, X-Cred-Mcp-Registry-Url
    2. Provider config: base_url, token
    3. Environment: MCP_REGISTRY_URL, MCP_REGISTRY_TOKEN

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
        self._default_base_url = base_url or os.environ.get("MCP_REGISTRY_URL", "http://localhost:8080")
        self._default_token = token or os.environ.get("MCP_REGISTRY_TOKEN")
        self._verify_ssl = verify_ssl
        logger.info("MCP Registry tools provider initialized (base_url=%s)", self._default_base_url)

    def _resolve_config(self) -> tuple[str, str | None]:
        """Resolve base URL and token from context or defaults."""
        creds = get_service_credentials("mcp_registry") or {}
        base_url = (creds.get("url") or self._default_base_url).rstrip("/")
        token = creds.get("token") or self._default_token
        return base_url, token

    def _headers(self, token: str | None, session_id: str | None = None) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        return headers

    @staticmethod
    def _parse_sse_json(text: str) -> dict[str, Any]:
        """Parse SSE response to extract JSON data."""
        for line in text.strip().split("\n"):
            if line.startswith("data: "):
                result: dict[str, Any] = json.loads(line[6:])
                return result
        # Try parsing as plain JSON
        fallback: dict[str, Any] = json.loads(text)
        return fallback

    # ── MCP streamable-http session management ───────────────────────

    # Cache TTL in seconds — entries older than this are evicted on access.
    _CACHE_TTL: ClassVar[int] = 300

    # Cache: (base_url, path) -> (session_id, timestamp)
    _sessions: ClassVar[dict[tuple[str, str], tuple[str, float]]] = {}

    # Cache: server_path -> (resolved MCP endpoint URL, timestamp)
    _mcp_endpoints: ClassVar[dict[str, tuple[str, float]]] = {}

    def _discover_mcp_endpoint(self, base_url: str, server_path: str, token: str | None) -> str:
        """Discover the MCP streamable-http endpoint for a server.

        Different MCP servers expose their endpoint at different paths.
        We try the path directly first, then with ``/mcp`` suffix.
        The result is cached.
        """
        cached = self._mcp_endpoints.get(server_path)
        if cached is not None:
            endpoint, ts = cached
            if time.monotonic() - ts < self._CACHE_TTL:
                return endpoint
            del self._mcp_endpoints[server_path]

        import httpx

        path = server_path.rstrip("/")
        candidates = [f"{base_url}{path}", f"{base_url}{path}/mcp"]
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": _MCP_CLIENT_INFO,
            },
        }

        now = time.monotonic()
        for endpoint in candidates:
            try:
                with httpx.Client(timeout=15, verify=self._verify_ssl) as client:
                    resp = client.post(endpoint, json=payload, headers=self._headers(token))
                    if resp.status_code == 200:
                        self._mcp_endpoints[server_path] = (endpoint, now)
                        # Also cache the session from this init
                        session_id = resp.headers.get("mcp-session-id", "")
                        if session_id:
                            cache_key = (base_url, server_path)
                            self._sessions[cache_key] = (session_id, now)
                        logger.info("MCP endpoint discovered: %s -> %s", server_path, endpoint)
                        return endpoint
            except Exception:
                continue

        # Default to path/mcp
        fallback = f"{base_url}{path}/mcp"
        self._mcp_endpoints[server_path] = (fallback, now)
        return fallback

    def _init_mcp_session(
        self,
        base_url: str,
        server_path: str,
        token: str | None,
    ) -> str:
        """Initialize an MCP session via the streamable-http transport.

        Returns the session ID from the ``Mcp-Session-Id`` response header.
        Sessions are cached per (base_url, path).
        """
        cache_key = (base_url, server_path)
        cached = self._sessions.get(cache_key)
        if cached is not None:
            session_id, ts = cached
            if time.monotonic() - ts < self._CACHE_TTL:
                return session_id
            del self._sessions[cache_key]

        import httpx

        endpoint = self._discover_mcp_endpoint(base_url, server_path, token)
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": _MCP_CLIENT_INFO,
            },
        }

        with httpx.Client(timeout=30, verify=self._verify_ssl) as client:
            resp = client.post(endpoint, json=payload, headers=self._headers(token))
            resp.raise_for_status()

        session_id = resp.headers.get("mcp-session-id", "")
        if not session_id:
            data = self._parse_sse_json(resp.text)
            session_id = data.get("result", {}).get("sessionId", "")

        if session_id:
            self._sessions[cache_key] = (session_id, time.monotonic())
            logger.debug("MCP session initialized: %s -> %s", server_path, session_id)

        return session_id

    def _mcp_call(
        self,
        base_url: str,
        server_path: str,
        token: str | None,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 60,
    ) -> dict[str, Any]:
        """Make an MCP JSON-RPC call over streamable-http.

        Handles session initialization, retries on session expiry, and SSE parsing.
        """
        import httpx

        session_id = self._init_mcp_session(base_url, server_path, token)
        endpoint = self._discover_mcp_endpoint(base_url, server_path, token)
        payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": method,
            "params": params or {},
        }

        with httpx.Client(timeout=timeout, verify=self._verify_ssl) as client:
            resp = client.post(
                endpoint,
                json=payload,
                headers=self._headers(token, session_id),
            )

            # Session expired — re-initialize and retry once
            if resp.status_code in (400, 404):
                self._sessions.pop((base_url, server_path), None)
                session_id = self._init_mcp_session(base_url, server_path, token)
                resp = client.post(
                    endpoint,
                    json=payload,
                    headers=self._headers(token, session_id),
                )

            resp.raise_for_status()

        result: dict[str, Any] = self._parse_sse_json(resp.text)
        return result

    # ── Server path resolution ────────────────────────────────────────

    # Cache of server title -> (path, timestamp) mappings
    _server_paths: ClassVar[dict[str, tuple[str, float]]] = {}

    async def _resolve_server_path(self, server_title: str, base_url: str, token: str | None) -> str:
        """Resolve a server title to its MCP proxy path."""
        cached = self._server_paths.get(server_title)
        if cached is not None:
            path, ts = cached
            if time.monotonic() - ts < self._CACHE_TTL:
                return path
            del self._server_paths[server_title]

        def _fetch() -> None:
            import httpx

            with httpx.Client(timeout=15, verify=self._verify_ssl) as http:
                resp = http.get(f"{base_url}/v0.1/servers", headers=self._headers(token))
                resp.raise_for_status()
                data = resp.json()

            now = time.monotonic()
            for entry in data.get("servers", []):
                server = entry.get("server", entry)
                title = server.get("title", server.get("name", ""))
                meta = server.get("_meta", {})
                internal = meta.get("io.mcpgateway/internal", {})
                path = internal.get("path", "")
                if title and path:
                    self._server_paths[title] = (path, now)

        await self._run_sync(_fetch)

        cached = self._server_paths.get(server_title)
        if cached is not None:
            return cached[0]

        raise ValueError(f"Server '{server_title}' not found in registry")

    # ── ToolsProvider interface ───────────────────────────────────────

    async def list_tools(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        base_url, token = self._resolve_config()

        def _list() -> list[dict[str, Any]]:
            import httpx

            with httpx.Client(timeout=30, verify=self._verify_ssl) as http:
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

                # Fetch actual tools via MCP streamable-http
                try:
                    mcp_data = self._mcp_call(base_url, path, token, "tools/list", timeout=15)

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
            raise ValueError(
                f"Invalid tool_name '{tool_name}'. "
                "Expected format: 'ServerTitle/tool_name' (e.g. 'AI Registry tools/list_services')"
            )

        try:
            server_path = await self._resolve_server_path(server_title, base_url, token)
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Failed to resolve server '{server_title}': {e}") from e

        def _invoke() -> dict[str, Any]:
            try:
                mcp_result = self._mcp_call(
                    base_url,
                    server_path,
                    token,
                    "tools/call",
                    {"name": actual_tool, "arguments": params},
                )
            except Exception as e:
                return {"error": f"MCP call to '{server_title}/{actual_tool}' failed: {e}"}

            if "error" in mcp_result:
                return {"error": mcp_result["error"].get("message", str(mcp_result["error"]))}

            content = mcp_result.get("result", {}).get("content", [])
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("text"):
                    text_parts.append(block["text"])
                elif isinstance(block, str):
                    text_parts.append(block)

            return {
                "result": "\n".join(text_parts) if text_parts else mcp_result.get("result"),
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

    # ── Tool retrieval & deletion ───────────────────────────────────

    async def get_tool(self, tool_name: str) -> dict[str, Any]:
        tools = await self.list_tools()
        for t in tools:
            if t.get("name") == tool_name:
                return t
        raise KeyError(f"Tool not found: {tool_name}")

    async def delete_tool(self, tool_name: str) -> None:
        base_url, token = self._resolve_config()

        def _delete() -> None:
            import httpx

            with httpx.Client(timeout=30, verify=self._verify_ssl) as client:
                resp = client.delete(
                    f"{base_url}/api/tools/{tool_name}",
                    headers=self._headers(token),
                )
                resp.raise_for_status()

        await self._run_sync(_delete)

    # ── Server management ────────────────────────────────────────────

    async def list_servers(self) -> list[dict[str, Any]]:
        base_url, token = self._resolve_config()

        def _list() -> list[dict[str, Any]]:
            import httpx

            with httpx.Client(timeout=15, verify=self._verify_ssl) as http:
                resp = http.get(f"{base_url}/v0.1/servers", headers=self._headers(token))
                resp.raise_for_status()
                data = resp.json()

            servers: list[dict[str, Any]] = []
            for entry in data.get("servers", data) if isinstance(data, dict) else data:
                server = entry.get("server", entry) if isinstance(entry, dict) else entry
                meta = server.get("_meta", {})
                internal = meta.get("io.mcpgateway/internal", {})
                servers.append(
                    {
                        "name": server.get("title", server.get("name", "")),
                        "url": internal.get("path", ""),
                        "health_status": internal.get("health_status", "unknown"),
                        "tools_count": internal.get("num_tools", 0),
                        "metadata": {k: v for k, v in server.items() if k not in ("title", "name", "_meta")},
                    }
                )
            return servers

        result: list[dict[str, Any]] = await self._run_sync(_list)
        return result

    async def get_server(self, server_name: str) -> dict[str, Any]:
        servers = await self.list_servers()
        for s in servers:
            if s.get("name") == server_name:
                return s
        raise KeyError(f"Server not found: {server_name}")

    async def register_server(self, server_config: dict[str, Any]) -> dict[str, Any]:
        base_url, token = self._resolve_config()

        def _register() -> dict[str, Any]:
            import httpx

            with httpx.Client(timeout=30, verify=self._verify_ssl) as client:
                resp = client.post(
                    f"{base_url}/api/servers/register",
                    json=server_config,
                    headers=self._headers(token),
                )
                resp.raise_for_status()
                result: dict[str, Any] = resp.json()
                return result

        result: dict[str, Any] = await self._run_sync(_register)
        return result

    async def healthcheck(self) -> bool:
        base_url, token = self._resolve_config()
        try:
            import httpx

            with httpx.Client(timeout=5, verify=self._verify_ssl) as client:
                resp = client.get(f"{base_url}/health", headers=self._headers(token))
                return resp.status_code < 500
        except Exception:
            return False
