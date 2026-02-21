from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentic_primitives_gateway.primitives.tools.agentcore import AgentCoreGatewayProvider


@patch("agentic_primitives_gateway.primitives.tools.agentcore.get_service_credentials")
class TestAgentCoreGatewayProvider:
    """Tests for the AgentCore Gateway tools provider."""

    def _make_provider(self, **kwargs):
        return AgentCoreGatewayProvider(**kwargs)

    @pytest.mark.asyncio
    async def test_list_tools_with_gateway_url(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(gateway_url="https://gw.example.com/mcp")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "result": {
                "tools": [
                    {"name": "calc", "description": "Calculator", "inputSchema": {"type": "object"}},
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = await provider.list_tools()

        assert len(result) == 1
        assert result[0]["name"] == "calc"
        assert result[0]["parameters"] == {"type": "object"}

    @pytest.mark.asyncio
    async def test_list_tools_with_gateway_id(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(gateway_id="gw-123", region="us-west-2")

        mock_response = MagicMock()
        mock_response.json.return_value = {"result": {"tools": []}}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            await provider.list_tools()

        call_args = mock_client.post.call_args
        assert "gw-123.gateway.bedrock-agentcore.us-west-2.amazonaws.com" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_list_tools_from_client_headers(self, mock_get_creds):
        mock_get_creds.return_value = {"gateway_url": "https://client-gw.example.com/mcp"}
        provider = self._make_provider()

        mock_response = MagicMock()
        mock_response.json.return_value = {"result": {"tools": []}}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            await provider.list_tools()

        call_args = mock_client.post.call_args
        assert call_args[0][0] == "https://client-gw.example.com/mcp"

    @pytest.mark.asyncio
    async def test_no_gateway_url_raises(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider()

        with pytest.raises(ValueError, match="Gateway URL is required"):
            await provider.list_tools()

    @pytest.mark.asyncio
    async def test_invoke_tool_success(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(gateway_url="https://gw.example.com/mcp")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "result": {
                "content": [{"text": "42"}],
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = await provider.invoke_tool("calc", {"a": 1, "b": 2})

        assert result["result"] == "42"

    @pytest.mark.asyncio
    async def test_invoke_tool_with_error_response(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(gateway_url="https://gw.example.com/mcp")

        mock_response = MagicMock()
        mock_response.json.return_value = {"error": {"message": "Tool not found", "code": -32601}}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = await provider.invoke_tool("missing", {})

        assert result["error"] == "Tool not found"

    @pytest.mark.asyncio
    async def test_invoke_tool_with_access_token(self, mock_get_creds):
        mock_get_creds.return_value = {"gateway_token": "my-token"}
        provider = self._make_provider(gateway_url="https://gw.example.com/mcp")

        mock_response = MagicMock()
        mock_response.json.return_value = {"result": {"content": [{"text": "ok"}]}}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            await provider.invoke_tool("calc", {})

        headers = mock_client.post.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer my-token"

    @pytest.mark.asyncio
    async def test_invoke_tool_string_content_blocks(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(gateway_url="https://gw.example.com/mcp")

        mock_response = MagicMock()
        mock_response.json.return_value = {"result": {"content": ["line1", "line2"]}}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = await provider.invoke_tool("tool", {})

        assert result["result"] == "line1\nline2"

    @pytest.mark.asyncio
    async def test_register_tool_logs_warning(self, mock_get_creds):
        provider = self._make_provider(gateway_url="https://gw.example.com/mcp")
        # Should not raise, just log a warning
        await provider.register_tool({"name": "test", "description": "test"})

    @pytest.mark.asyncio
    async def test_search_tools_falls_back_to_list(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(gateway_url="https://gw.example.com/mcp")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "result": {
                "tools": [
                    {"name": "calc", "description": "Calculator", "inputSchema": {}},
                    {"name": "weather", "description": "Weather API", "inputSchema": {}},
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = await provider.search_tools("calc", max_results=5)

        assert len(result) == 1
        assert result[0]["name"] == "calc"

    @pytest.mark.asyncio
    async def test_healthcheck_success(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(gateway_url="https://gw.example.com/mcp")

        mock_response = MagicMock()
        mock_response.json.return_value = {"result": {"tools": []}}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            assert await provider.healthcheck() is True

    @pytest.mark.asyncio
    async def test_healthcheck_failure(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(gateway_url="https://gw.example.com/mcp")

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.side_effect = Exception("connection failed")
            mock_client_cls.return_value = mock_client

            assert await provider.healthcheck() is False
