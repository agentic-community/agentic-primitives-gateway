"""System tests for the AgentCore tools primitive.

Full stack: AgenticPlatformClient → ASGI → middleware → route → registry →
AgentCoreGatewayProvider → (mocked) httpx.Client MCP JSON-RPC calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentic_primitives_gateway_client import AgenticPlatformClient, AgenticPlatformError


@pytest.fixture
def mock_httpx():
    """Patch ``httpx.Client`` used inside the tools provider's sync functions.

    The provider imports httpx inside the thread-pool functions, so we patch
    the module-level ``httpx.Client`` class.
    """
    with patch("httpx.Client") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_cls.return_value = mock_client
        yield mock_client


def _jsonrpc_response(result: dict) -> MagicMock:
    """Build a mock httpx.Response for a JSON-RPC success."""
    resp = MagicMock()
    resp.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": result}
    resp.raise_for_status.return_value = None
    return resp


# ── List tools ────────────────────────────────────────────────────────


class TestListTools:
    async def test_list_tools(self, client: AgenticPlatformClient, mock_httpx: MagicMock) -> None:
        mock_httpx.post.return_value = _jsonrpc_response(
            {
                "tools": [
                    {
                        "name": "calculator",
                        "description": "Does math",
                        "inputSchema": {"type": "object"},
                    },
                ]
            }
        )

        result = await client.list_tools()

        assert "tools" in result
        assert len(result["tools"]) == 1
        assert result["tools"][0]["name"] == "calculator"


# ── Invoke tool ───────────────────────────────────────────────────────


class TestInvokeTool:
    async def test_invoke_tool(self, client: AgenticPlatformClient, mock_httpx: MagicMock) -> None:
        mock_httpx.post.return_value = _jsonrpc_response({"content": [{"text": "42"}]})

        result = await client.invoke_tool("calculator", {"expression": "6*7"})

        assert result["tool_name"] == "calculator"
        assert result["result"] == "42"

    async def test_invoke_tool_error(self, client: AgenticPlatformClient, mock_httpx: MagicMock) -> None:
        resp = MagicMock()
        resp.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32000, "message": "tool failed"},
        }
        resp.raise_for_status.return_value = None
        mock_httpx.post.return_value = resp

        result = await client.invoke_tool("bad-tool", {})

        assert result["tool_name"] == "bad-tool"
        assert "error" in result


# ── Get tool ──────────────────────────────────────────────────────────


class TestGetTool:
    async def test_get_tool(self, client: AgenticPlatformClient, mock_httpx: MagicMock) -> None:
        mock_httpx.post.return_value = _jsonrpc_response(
            {
                "tools": [
                    {
                        "name": "calculator",
                        "description": "Does math",
                        "inputSchema": {},
                    },
                ]
            }
        )

        result = await client.get_tool("calculator")

        assert result["name"] == "calculator"

    async def test_get_tool_not_found(self, client: AgenticPlatformClient, mock_httpx: MagicMock) -> None:
        mock_httpx.post.return_value = _jsonrpc_response({"tools": []})

        with pytest.raises(AgenticPlatformError) as exc_info:
            await client.get_tool("missing")
        assert exc_info.value.status_code == 404


# ── Search tools ──────────────────────────────────────────────────────


class TestSearchTools:
    async def test_search_tools(self, client: AgenticPlatformClient, mock_httpx: MagicMock) -> None:
        mock_httpx.post.return_value = _jsonrpc_response(
            {
                "tools": [
                    {"name": "calculator", "description": "Does math", "inputSchema": {}},
                    {"name": "weather", "description": "Gets weather", "inputSchema": {}},
                ]
            }
        )

        result = await client.search_tools("calc")

        assert "tools" in result
        # search_tools falls back to list + name/description filter
        assert any(t["name"] == "calculator" for t in result["tools"])


# ── Register tool ─────────────────────────────────────────────────────


class TestRegisterTool:
    async def test_register_tool(self, client: AgenticPlatformClient) -> None:
        result = await client.register_tool(
            {
                "name": "my-tool",
                "description": "A test tool",
                "parameters": {"type": "object"},
            }
        )

        # AgentCore logs a warning but returns the tool def back
        assert result["name"] == "my-tool"


# ── Delete tool (not supported) ──────────────────────────────────────


class TestDeleteTool:
    async def test_delete_tool_not_supported(self, client: AgenticPlatformClient, mock_httpx: MagicMock) -> None:
        # delete_tool raises NotImplementedError on the base ABC
        with pytest.raises(AgenticPlatformError) as exc_info:
            await client.delete_tool("calc")
        assert exc_info.value.status_code == 501
