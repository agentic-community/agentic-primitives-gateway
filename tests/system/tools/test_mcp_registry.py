"""System tests for the MCP Registry tools primitive.

Full stack: AgenticPlatformClient -> ASGI -> middleware -> route -> registry ->
MCPRegistryProvider -> (mocked) httpx.Client.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import agentic_primitives_gateway.config as _config_module
from agentic_primitives_gateway.config import Settings
from agentic_primitives_gateway.primitives.tools.mcp_registry import MCPRegistryProvider
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway_client import AgenticPlatformClient, AgenticPlatformError

# ── Registry override ────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _init_registry():
    """Initialise registry with MCP Registry tools provider (noop for everything else)."""
    # Clear server path cache between tests
    MCPRegistryProvider._server_paths.clear()

    test_settings = Settings(
        allow_server_credentials="always",
        providers={
            "memory": {
                "backend": "agentic_primitives_gateway.primitives.memory.noop.NoopMemoryProvider",
                "config": {},
            },
            "observability": {
                "backend": "agentic_primitives_gateway.primitives.observability.noop.NoopObservabilityProvider",
                "config": {},
            },
            "llm": {
                "backend": "agentic_primitives_gateway.primitives.llm.noop.NoopLLMProvider",
                "config": {},
            },
            "tools": {
                "backend": ("agentic_primitives_gateway.primitives.tools.mcp_registry.MCPRegistryProvider"),
                "config": {
                    "base_url": "http://mcp-test:8080",
                    "token": "test-jwt",
                },
            },
            "identity": {
                "backend": "agentic_primitives_gateway.primitives.identity.noop.NoopIdentityProvider",
                "config": {},
            },
            "code_interpreter": {
                "backend": "agentic_primitives_gateway.primitives.code_interpreter.noop.NoopCodeInterpreterProvider",
                "config": {},
            },
            "browser": {
                "backend": "agentic_primitives_gateway.primitives.browser.noop.NoopBrowserProvider",
                "config": {},
            },
        },
    )
    orig_settings = _config_module.settings
    _config_module.settings = test_settings
    registry.initialize(test_settings)

    yield

    _config_module.settings = orig_settings


# ── Helpers ──────────────────────────────────────────────────────────


@pytest.fixture
def mock_httpx():
    """Patch ``httpx.Client`` used inside the MCP Registry provider's sync functions.

    The provider imports httpx inside thread-pool functions, so we patch
    the module-level ``httpx.Client`` class.
    """
    with patch("httpx.Client") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_cls.return_value = mock_client
        yield mock_client


def _servers_response() -> dict:
    """Standard servers response with one healthy server."""
    return {
        "servers": [
            {
                "server": {
                    "title": "calculator",
                    "name": "calculator",
                    "description": "Math server",
                    "_meta": {
                        "io.mcpgateway/internal": {
                            "path": "/mcp/calculator",
                            "health_status": "healthy",
                            "num_tools": 2,
                        }
                    },
                }
            }
        ]
    }


def _http_response(data: dict | list | str, status_code: int = 200) -> MagicMock:
    """Build a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status.return_value = None
    if isinstance(data, str):
        resp.text = data
        resp.json.return_value = json.loads(data)
    else:
        resp.json.return_value = data
        resp.text = json.dumps(data)
    return resp


def _sse_response(result: dict) -> MagicMock:
    """Build a mock httpx.Response for SSE-wrapped JSON-RPC result."""
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "result": result})
    sse_text = f"data: {payload}"
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.text = sse_text
    resp.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": result}
    return resp


# ── List tools ───────────────────────────────────────────────────────


class TestListTools:
    async def test_list_tools(self, client: AgenticPlatformClient, mock_httpx: MagicMock) -> None:
        servers_resp = _http_response(_servers_response())
        mcp_tools_resp = _sse_response(
            {
                "tools": [
                    {
                        "name": "add",
                        "description": "Adds two numbers",
                        "inputSchema": {"type": "object"},
                    },
                ]
            }
        )

        mock_httpx.get.return_value = servers_resp
        mock_httpx.post.return_value = mcp_tools_resp

        result = await client.list_tools()

        assert "tools" in result
        assert len(result["tools"]) == 1
        assert result["tools"][0]["name"] == "calculator/add"


# ── Invoke tool ──────────────────────────────────────────────────────


class TestInvokeTool:
    async def test_invoke_tool(self, client: AgenticPlatformClient, mock_httpx: MagicMock) -> None:
        # First call resolves server path, second call invokes tool
        servers_resp = _http_response(_servers_response())
        invoke_resp = _sse_response({"content": [{"text": "42"}]})

        mock_httpx.get.return_value = servers_resp
        mock_httpx.post.return_value = invoke_resp

        result = await client.invoke_tool("calculator/add", {"a": 6, "b": 7})

        assert result["tool_name"] == "calculator/add"
        assert result["result"] == "42"


# ── Get tool ─────────────────────────────────────────────────────────


class TestGetTool:
    async def test_get_tool(self, client: AgenticPlatformClient, mock_httpx: MagicMock) -> None:
        servers_resp = _http_response(_servers_response())
        mcp_tools_resp = _sse_response(
            {
                "tools": [
                    {
                        "name": "add",
                        "description": "Adds two numbers",
                        "inputSchema": {"type": "object"},
                    },
                ]
            }
        )

        mock_httpx.get.return_value = servers_resp
        mock_httpx.post.return_value = mcp_tools_resp

        result = await client.get_tool("calculator/add")

        assert result["name"] == "calculator/add"

    async def test_get_tool_not_found(self, client: AgenticPlatformClient, mock_httpx: MagicMock) -> None:
        servers_resp = _http_response(_servers_response())
        mcp_tools_resp = _sse_response({"tools": []})

        mock_httpx.get.return_value = servers_resp
        mock_httpx.post.return_value = mcp_tools_resp

        with pytest.raises(AgenticPlatformError) as exc_info:
            await client.get_tool("calculator/missing")
        assert exc_info.value.status_code == 404


# ── Search tools ─────────────────────────────────────────────────────


class TestSearchTools:
    async def test_search_tools(self, client: AgenticPlatformClient, mock_httpx: MagicMock) -> None:
        search_resp = _http_response(
            {
                "results": [
                    {
                        "name": "calculator/add",
                        "description": "Adds two numbers",
                        "inputSchema": {"type": "object"},
                    },
                ]
            }
        )

        mock_httpx.get.return_value = search_resp

        result = await client.search_tools("add")

        assert "tools" in result
        assert len(result["tools"]) >= 1
        assert result["tools"][0]["name"] == "calculator/add"


# ── Register tool ────────────────────────────────────────────────────


class TestRegisterTool:
    async def test_register_tool(self, client: AgenticPlatformClient, mock_httpx: MagicMock) -> None:
        register_resp = _http_response({}, status_code=201)
        mock_httpx.post.return_value = register_resp

        result = await client.register_tool(
            {
                "name": "my-tool",
                "description": "A test tool",
                "parameters": {"type": "object"},
            }
        )

        # register_tool returns the tool def back
        assert result["name"] == "my-tool"


# ── Delete tool ──────────────────────────────────────────────────────


class TestDeleteTool:
    async def test_delete_tool(self, client: AgenticPlatformClient, mock_httpx: MagicMock) -> None:
        delete_resp = _http_response({}, status_code=200)
        mock_httpx.delete.return_value = delete_resp

        await client.delete_tool("calculator/add")


# ── List servers ─────────────────────────────────────────────────────


class TestListServers:
    async def test_list_servers(self, client: AgenticPlatformClient, mock_httpx: MagicMock) -> None:
        servers_resp = _http_response(_servers_response())
        mock_httpx.get.return_value = servers_resp

        result = await client.list_tool_servers()

        assert "servers" in result
        assert len(result["servers"]) == 1
        assert result["servers"][0]["name"] == "calculator"


# ── Register server ──────────────────────────────────────────────────


class TestRegisterServer:
    async def test_register_server(self, client: AgenticPlatformClient, mock_httpx: MagicMock) -> None:
        register_resp = _http_response({"status": "registered"}, status_code=200)
        mock_httpx.post.return_value = register_resp

        result = await client.register_tool_server(
            {
                "name": "my-server",
                "url": "http://my-server:8080",
                "transport": "sse",
            }
        )

        assert result["status"] == "registered"
