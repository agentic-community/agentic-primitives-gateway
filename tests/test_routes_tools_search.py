from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.registry import registry


class TestToolsSearchRoute:
    """Tests for the GET /api/v1/tools/search endpoint."""

    def setup_method(self):
        from agentic_primitives_gateway.config import Settings

        test_settings = Settings(
            providers={
                "memory": {"backend": "agentic_primitives_gateway.primitives.memory.in_memory.InMemoryProvider"},
                "observability": {
                    "backend": "agentic_primitives_gateway.primitives.observability.noop.NoopObservabilityProvider"
                },
                "gateway": {"backend": "agentic_primitives_gateway.primitives.gateway.noop.NoopGatewayProvider"},
                "tools": {"backend": "agentic_primitives_gateway.primitives.tools.noop.NoopToolsProvider"},
                "identity": {"backend": "agentic_primitives_gateway.primitives.identity.noop.NoopIdentityProvider"},
                "code_interpreter": {
                    "backend": "agentic_primitives_gateway.primitives.code_interpreter.noop.NoopCodeInterpreterProvider"
                },
                "browser": {"backend": "agentic_primitives_gateway.primitives.browser.noop.NoopBrowserProvider"},
            }
        )
        registry.initialize(test_settings)
        self.client = TestClient(app)

    def test_search_tools_returns_results(self):
        mock_tools = [
            {"name": "calculator", "description": "Math tool", "parameters": {}, "metadata": {}},
            {"name": "weather", "description": "Weather lookup", "parameters": {}, "metadata": {}},
        ]
        with patch.object(
            registry.get_primitive("tools").get()._provider,
            "search_tools",
            new_callable=AsyncMock,
            return_value=mock_tools,
        ):
            resp = self.client.get("/api/v1/tools/search", params={"query": "calc"})
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["tools"]) == 2
            assert data["tools"][0]["name"] == "calculator"

    def test_search_tools_empty_results(self):
        with patch.object(
            registry.get_primitive("tools").get()._provider,
            "search_tools",
            new_callable=AsyncMock,
            return_value=[],
        ):
            resp = self.client.get("/api/v1/tools/search", params={"query": "nonexistent"})
            assert resp.status_code == 200
            assert resp.json()["tools"] == []

    def test_search_tools_with_max_results(self):
        with patch.object(
            registry.get_primitive("tools").get()._provider,
            "search_tools",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_search:
            resp = self.client.get(
                "/api/v1/tools/search",
                params={"query": "test", "max_results": 5},
            )
            assert resp.status_code == 200
            mock_search.assert_called_once_with("test", 5)

    def test_search_tools_missing_query_returns_422(self):
        resp = self.client.get("/api/v1/tools/search")
        assert resp.status_code == 422
