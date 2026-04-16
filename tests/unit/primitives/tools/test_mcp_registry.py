from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from agentic_primitives_gateway.primitives.tools.mcp_registry import MCPRegistryProvider


@patch("agentic_primitives_gateway.primitives.tools.mcp_registry.get_service_credentials")
class TestMCPRegistryProvider:
    """Tests for the MCP Registry tools provider."""

    def setup_method(self):
        # Clear all class-level caches between tests
        MCPRegistryProvider._server_paths.clear()
        MCPRegistryProvider._sessions.clear()
        MCPRegistryProvider._mcp_endpoints.clear()

    def _make_provider(self, **kwargs):
        return MCPRegistryProvider(**kwargs)

    def _mock_httpx_client(self):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        return mock_client

    @pytest.mark.asyncio
    async def test_list_tools_with_healthy_server(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(base_url="http://localhost:8080")

        servers_resp = MagicMock()
        servers_resp.json.return_value = {
            "servers": [
                {
                    "server": {
                        "title": "Calculator",
                        "description": "Math tools",
                        "_meta": {
                            "io.mcpgateway/internal": {
                                "path": "/mcp/calc",
                                "health_status": "healthy",
                                "num_tools": 2,
                            }
                        },
                    }
                }
            ]
        }
        servers_resp.raise_for_status = MagicMock()

        mcp_resp = MagicMock()
        mcp_resp.text = (
            'data: {"result": {"tools": [{"name": "add", "description": "Add numbers", "inputSchema": {}}]}}'
        )
        mcp_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = self._mock_httpx_client()
            mock_client.get.return_value = servers_resp
            mock_client.post.return_value = mcp_resp
            mock_client_cls.return_value = mock_client

            result = await provider.list_tools()

        assert len(result) == 1
        assert result[0]["name"] == "Calculator/add"
        assert result[0]["metadata"]["server"] == "Calculator"

    @pytest.mark.asyncio
    async def test_list_tools_skips_unhealthy_server(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(base_url="http://localhost:8080")

        servers_resp = MagicMock()
        servers_resp.json.return_value = {
            "servers": [
                {
                    "server": {
                        "title": "Down",
                        "description": "Offline",
                        "_meta": {"io.mcpgateway/internal": {"path": "/mcp/down", "health_status": "unhealthy"}},
                    }
                }
            ]
        }
        servers_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = self._mock_httpx_client()
            mock_client.get.return_value = servers_resp
            mock_client_cls.return_value = mock_client

            result = await provider.list_tools()

        assert result == []

    @pytest.mark.asyncio
    async def test_list_tools_fallback_on_mcp_error(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(base_url="http://localhost:8080")

        servers_resp = MagicMock()
        servers_resp.json.return_value = {
            "servers": [
                {
                    "server": {
                        "title": "Broken",
                        "description": "MCP broken",
                        "_meta": {
                            "io.mcpgateway/internal": {
                                "path": "/mcp/broken",
                                "health_status": "healthy",
                                "num_tools": 3,
                            }
                        },
                    }
                }
            ]
        }
        servers_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = self._mock_httpx_client()
            mock_client.get.return_value = servers_resp
            mock_client.post.side_effect = Exception("MCP connection failed")
            mock_client_cls.return_value = mock_client

            result = await provider.list_tools()

        # Falls back to server-level entry
        assert len(result) == 1
        assert result[0]["name"] == "Broken"
        assert "3 tools" in result[0]["description"]

    @pytest.mark.asyncio
    async def test_invoke_tool_with_server_prefix(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(base_url="http://localhost:8080")

        # Pre-populate server path cache
        MCPRegistryProvider._server_paths["Calculator"] = ("/mcp/calc", time.monotonic())

        mcp_resp = MagicMock()
        mcp_resp.text = 'data: {"result": {"content": [{"text": "42"}]}}'
        mcp_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = self._mock_httpx_client()
            mock_client.post.return_value = mcp_resp
            mock_client_cls.return_value = mock_client

            result = await provider.invoke_tool("Calculator/add", {"a": 1, "b": 2})

        assert result["result"] == "42"

    @pytest.mark.asyncio
    async def test_invoke_tool_error_response(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(base_url="http://localhost:8080")
        MCPRegistryProvider._server_paths["Svc"] = ("/mcp/svc", time.monotonic())

        mcp_resp = MagicMock()
        mcp_resp.text = 'data: {"error": {"message": "tool not found", "code": -32601}}'
        mcp_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = self._mock_httpx_client()
            mock_client.post.return_value = mcp_resp
            mock_client_cls.return_value = mock_client

            result = await provider.invoke_tool("Svc/missing", {})

        assert result["error"] == "tool not found"

    @pytest.mark.asyncio
    async def test_resolve_server_path_fetches_from_registry(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(base_url="http://localhost:8080")

        servers_resp = MagicMock()
        servers_resp.json.return_value = {
            "servers": [
                {
                    "server": {
                        "title": "NewSvc",
                        "_meta": {"io.mcpgateway/internal": {"path": "/mcp/new"}},
                    }
                }
            ]
        }
        servers_resp.raise_for_status = MagicMock()

        mcp_resp = MagicMock()
        mcp_resp.text = 'data: {"result": {"content": [{"text": "ok"}]}}'
        mcp_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = self._mock_httpx_client()
            mock_client.get.return_value = servers_resp
            mock_client.post.return_value = mcp_resp
            mock_client_cls.return_value = mock_client

            result = await provider.invoke_tool("NewSvc/tool", {"x": 1})

        assert result["result"] == "ok"

    @pytest.mark.asyncio
    async def test_resolve_server_path_not_found(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(base_url="http://localhost:8080")

        servers_resp = MagicMock()
        servers_resp.json.return_value = {"servers": []}
        servers_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = self._mock_httpx_client()
            mock_client.get.return_value = servers_resp
            mock_client_cls.return_value = mock_client

            with pytest.raises(ValueError, match="not found in registry"):
                await provider.invoke_tool("Missing/tool", {})

    @pytest.mark.asyncio
    async def test_register_tool(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(base_url="http://localhost:8080")

        register_resp = MagicMock()
        register_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = self._mock_httpx_client()
            mock_client.post.return_value = register_resp
            mock_client_cls.return_value = mock_client

            await provider.register_tool({"name": "my-tool", "description": "test"})

        mock_client.post.assert_called_once()
        call_url = mock_client.post.call_args[0][0]
        assert "/api/servers/register" in call_url

    @pytest.mark.asyncio
    async def test_search_tools_semantic_success(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(base_url="http://localhost:8080")

        search_resp = MagicMock()
        search_resp.json.return_value = {
            "results": [
                {"name": "calc", "description": "Calculator", "inputSchema": {}},
            ]
        }
        search_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = self._mock_httpx_client()
            mock_client.get.return_value = search_resp
            mock_client_cls.return_value = mock_client

            result = await provider.search_tools("math", max_results=5)

        assert len(result) == 1
        assert result[0]["name"] == "calc"

    @pytest.mark.asyncio
    async def test_search_tools_fallback_to_list(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(base_url="http://localhost:8080")

        # Semantic search fails
        search_resp = MagicMock()
        search_resp.raise_for_status.side_effect = Exception("404")

        # List tools succeeds
        servers_resp = MagicMock()
        servers_resp.json.return_value = {
            "servers": [
                {
                    "server": {
                        "title": "Weather",
                        "description": "Weather API",
                        "_meta": {"io.mcpgateway/internal": {"path": "/mcp/weather", "health_status": "healthy"}},
                    }
                }
            ]
        }
        servers_resp.raise_for_status = MagicMock()

        mcp_resp = MagicMock()
        mcp_resp.text = (
            'data: {"result": {"tools": [{"name": "forecast", "description": "Weather forecast", "inputSchema": {}}]}}'
        )
        mcp_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = self._mock_httpx_client()

            def get_side_effect(url, **kwargs):
                if "semantic" in url:
                    raise Exception("search not available")
                return servers_resp

            mock_client.get.side_effect = get_side_effect
            mock_client.post.return_value = mcp_resp
            mock_client_cls.return_value = mock_client

            result = await provider.search_tools("weather", max_results=5)

        assert len(result) == 1
        assert "forecast" in result[0]["name"]

    @pytest.mark.asyncio
    async def test_healthcheck_success(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(base_url="http://localhost:8080")

        with patch("httpx.Client") as mock_client_cls:
            mock_client = self._mock_httpx_client()
            health_resp = MagicMock()
            health_resp.status_code = 200
            mock_client.get.return_value = health_resp
            mock_client_cls.return_value = mock_client

            # No token configured → "reachable" (server up, needs user creds)
            assert await provider.healthcheck() == "reachable"

    @pytest.mark.asyncio
    async def test_healthcheck_with_token(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(base_url="http://localhost:8080", token="test-token")

        with patch("httpx.Client") as mock_client_cls:
            mock_client = self._mock_httpx_client()
            health_resp = MagicMock()
            health_resp.status_code = 200
            mock_client.get.return_value = health_resp
            mock_client_cls.return_value = mock_client

            assert await provider.healthcheck() is True

    @pytest.mark.asyncio
    async def test_healthcheck_failure(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(base_url="http://localhost:8080")

        with patch("httpx.Client") as mock_client_cls:
            mock_client = self._mock_httpx_client()
            mock_client.get.side_effect = Exception("connection refused")
            mock_client_cls.return_value = mock_client

            assert await provider.healthcheck() is False

    # ── New methods ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_tool(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(base_url="http://localhost:8080")

        servers_resp = MagicMock()
        servers_resp.json.return_value = {
            "servers": [
                {
                    "server": {
                        "title": "Calculator",
                        "_meta": {
                            "io.mcpgateway/internal": {
                                "path": "/mcp/calc",
                                "health_status": "healthy",
                            }
                        },
                    }
                }
            ]
        }
        servers_resp.raise_for_status = MagicMock()

        mcp_resp = MagicMock()
        mcp_resp.text = '{"result": {"tools": [{"name": "add", "description": "Add numbers", "inputSchema": {}}]}}'
        mcp_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = self._mock_httpx_client()
            mock_client.get.return_value = servers_resp
            mock_client.post.return_value = mcp_resp
            mock_client_cls.return_value = mock_client

            result = await provider.get_tool("Calculator/add")

        assert result["name"] == "Calculator/add"

    @pytest.mark.asyncio
    async def test_get_tool_not_found(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(base_url="http://localhost:8080")

        servers_resp = MagicMock()
        servers_resp.json.return_value = {"servers": []}
        servers_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = self._mock_httpx_client()
            mock_client.get.return_value = servers_resp
            mock_client_cls.return_value = mock_client

            with pytest.raises(KeyError):
                await provider.get_tool("nonexistent")

    @pytest.mark.asyncio
    async def test_delete_tool(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(base_url="http://localhost:8080")

        delete_resp = MagicMock()
        delete_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = self._mock_httpx_client()
            mock_client.delete.return_value = delete_resp
            mock_client_cls.return_value = mock_client

            await provider.delete_tool("Calculator/add")

        mock_client.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_servers(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(base_url="http://localhost:8080")

        servers_resp = MagicMock()
        servers_resp.json.return_value = {
            "servers": [
                {
                    "server": {
                        "title": "Calculator",
                        "description": "Math tools",
                        "_meta": {
                            "io.mcpgateway/internal": {
                                "path": "/mcp/calc",
                                "health_status": "healthy",
                                "num_tools": 3,
                            }
                        },
                    }
                }
            ]
        }
        servers_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = self._mock_httpx_client()
            mock_client.get.return_value = servers_resp
            mock_client_cls.return_value = mock_client

            result = await provider.list_servers()

        assert len(result) == 1
        assert result[0]["name"] == "Calculator"
        assert result[0]["health_status"] == "healthy"
        assert result[0]["tools_count"] == 3

    @pytest.mark.asyncio
    async def test_get_server(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(base_url="http://localhost:8080")

        servers_resp = MagicMock()
        servers_resp.json.return_value = {
            "servers": [
                {
                    "server": {
                        "title": "Calculator",
                        "_meta": {
                            "io.mcpgateway/internal": {
                                "path": "/mcp/calc",
                                "health_status": "healthy",
                                "num_tools": 3,
                            }
                        },
                    }
                }
            ]
        }
        servers_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = self._mock_httpx_client()
            mock_client.get.return_value = servers_resp
            mock_client_cls.return_value = mock_client

            result = await provider.get_server("Calculator")

        assert result["name"] == "Calculator"

    @pytest.mark.asyncio
    async def test_get_server_not_found(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(base_url="http://localhost:8080")

        servers_resp = MagicMock()
        servers_resp.json.return_value = {"servers": []}
        servers_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = self._mock_httpx_client()
            mock_client.get.return_value = servers_resp
            mock_client_cls.return_value = mock_client

            with pytest.raises(KeyError):
                await provider.get_server("nonexistent")

    @pytest.mark.asyncio
    async def test_register_server(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(base_url="http://localhost:8080")

        register_resp = MagicMock()
        register_resp.json.return_value = {"name": "new-server", "status": "registered"}
        register_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = self._mock_httpx_client()
            mock_client.post.return_value = register_resp
            mock_client_cls.return_value = mock_client

            result = await provider.register_server({"name": "new-server", "url": "http://new:9000"})

        assert result["name"] == "new-server"
        mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_parse_sse_json_with_data_prefix(self, mock_get_creds):
        result = MCPRegistryProvider._parse_sse_json('data: {"result": "ok"}')
        assert result == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_parse_sse_json_plain_json(self, mock_get_creds):
        result = MCPRegistryProvider._parse_sse_json('{"result": "ok"}')
        assert result == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_resolve_config_from_client_headers(self, mock_get_creds):
        mock_get_creds.return_value = {"url": "http://override:9090", "token": "client-token"}
        provider = self._make_provider(base_url="http://default:8080", token="default-token")
        base_url, token = provider._resolve_config()
        assert base_url == "http://override:9090"
        assert token == "client-token"

    @pytest.mark.asyncio
    async def test_resolve_config_defaults(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(base_url="http://default:8080", token="default-token")
        base_url, token = provider._resolve_config()
        assert base_url == "http://default:8080"
        assert token == "default-token"

    # ── v2 API format tests ───────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_list_tools_v2_format_with_inline_tool_list(self, mock_get_creds):
        """v2 format: /api/servers returns tool_list inline, no MCP calls needed."""
        mock_get_creds.return_value = None
        provider = self._make_provider(base_url="http://localhost:8080")

        servers_resp = MagicMock()
        servers_resp.status_code = 200
        servers_resp.json.return_value = {
            "servers": [
                {
                    "display_name": "Weather Service",
                    "path": "/weather/",
                    "description": "Weather tools",
                    "health_status": "healthy",
                    "num_tools": 2,
                    "tool_list": [
                        {"name": "get_forecast", "description": "Get forecast", "schema": {"type": "object"}},
                        {"name": "get_current", "description": "Current weather", "schema": {"type": "object"}},
                    ],
                }
            ]
        }
        servers_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = self._mock_httpx_client()
            mock_client.get.return_value = servers_resp
            mock_client_cls.return_value = mock_client

            result = await provider.list_tools()

        assert len(result) == 2
        assert result[0]["name"] == "Weather Service/get_forecast"
        assert result[1]["name"] == "Weather Service/get_current"
        assert result[0]["metadata"]["server"] == "Weather Service"
        # No MCP post calls should have been made (tools came from tool_list)
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_list_tools_v2_skips_unhealthy(self, mock_get_creds):
        """v2 format: unhealthy servers are skipped."""
        mock_get_creds.return_value = None
        provider = self._make_provider(base_url="http://localhost:8080")

        servers_resp = MagicMock()
        servers_resp.status_code = 200
        servers_resp.json.return_value = {
            "servers": [
                {
                    "display_name": "Healthy",
                    "path": "/healthy/",
                    "health_status": "healthy",
                    "num_tools": 1,
                    "tool_list": [{"name": "tool1", "description": "A tool", "schema": {}}],
                },
                {
                    "display_name": "Unhealthy",
                    "path": "/unhealthy/",
                    "health_status": "unhealthy",
                    "num_tools": 1,
                    "tool_list": [{"name": "tool2", "description": "B tool", "schema": {}}],
                },
            ]
        }
        servers_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = self._mock_httpx_client()
            mock_client.get.return_value = servers_resp
            mock_client_cls.return_value = mock_client

            result = await provider.list_tools()

        assert len(result) == 1
        assert result[0]["name"] == "Healthy/tool1"

    @pytest.mark.asyncio
    async def test_list_servers_v2_format(self, mock_get_creds):
        """v2 format: list_servers normalizes correctly."""
        mock_get_creds.return_value = None
        provider = self._make_provider(base_url="http://localhost:8080")

        servers_resp = MagicMock()
        servers_resp.status_code = 200
        servers_resp.json.return_value = {
            "servers": [
                {
                    "display_name": "Calculator",
                    "path": "/calc/",
                    "health_status": "healthy",
                    "num_tools": 3,
                }
            ]
        }
        servers_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = self._mock_httpx_client()
            mock_client.get.return_value = servers_resp
            mock_client_cls.return_value = mock_client

            result = await provider.list_servers()

        assert len(result) == 1
        assert result[0]["name"] == "Calculator"
        assert result[0]["url"] == "/calc/"
        assert result[0]["health_status"] == "healthy"
        assert result[0]["tools_count"] == 3

    @pytest.mark.asyncio
    async def test_fetch_servers_falls_back_to_v1(self, mock_get_creds):
        """/api/servers returns 404, falls back to /v0.1/servers."""
        mock_get_creds.return_value = None
        provider = self._make_provider(base_url="http://localhost:8080")

        not_found_resp = MagicMock()
        not_found_resp.status_code = 404

        v1_resp = MagicMock()
        v1_resp.status_code = 200
        v1_resp.json.return_value = {
            "servers": [
                {
                    "server": {
                        "title": "Calculator",
                        "description": "Math tools",
                        "_meta": {
                            "io.mcpgateway/internal": {
                                "path": "/mcp/calc",
                                "health_status": "healthy",
                                "num_tools": 2,
                            }
                        },
                    }
                }
            ]
        }
        v1_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = self._mock_httpx_client()
            mock_client.get.side_effect = [not_found_resp, v1_resp]
            mock_client_cls.return_value = mock_client

            result = provider._fetch_servers_raw("http://localhost:8080", None)

        assert len(result) == 1
        assert result[0]["title"] == "Calculator"
        assert result[0]["path"] == "/mcp/calc"
        assert result[0]["health_status"] == "healthy"
        # Two GET calls: /api/servers (404) then /v0.1/servers (200)
        assert mock_client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_resolve_server_path_v2_format(self, mock_get_creds):
        """_resolve_server_path works with v2 format."""
        mock_get_creds.return_value = None
        provider = self._make_provider(base_url="http://localhost:8080")

        servers_resp = MagicMock()
        servers_resp.status_code = 200
        servers_resp.json.return_value = {
            "servers": [{"display_name": "Weather", "path": "/weather/", "health_status": "healthy", "num_tools": 1}]
        }
        servers_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = self._mock_httpx_client()
            mock_client.get.return_value = servers_resp
            mock_client_cls.return_value = mock_client

            path = await provider._resolve_server_path("Weather", "http://localhost:8080", None)

        assert path == "/weather/"
