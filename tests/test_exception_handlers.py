from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.registry import registry


class TestExceptionHandlers:
    """Test the global exception handlers on the FastAPI app."""

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
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_connection_error_returns_503(self):
        with patch.object(
            registry.get_primitive("tools").get()._provider,
            "list_tools",
            new_callable=AsyncMock,
            side_effect=ConnectionError("upstream down"),
        ):
            resp = self.client.get("/api/v1/tools")
            assert resp.status_code == 503
            assert "Service unavailable" in resp.json()["detail"]

    def test_timeout_error_returns_504(self):
        with patch.object(
            registry.get_primitive("tools").get()._provider,
            "list_tools",
            new_callable=AsyncMock,
            side_effect=TimeoutError("too slow"),
        ):
            resp = self.client.get("/api/v1/tools")
            assert resp.status_code == 504
            assert "Gateway timeout" in resp.json()["detail"]

    def test_general_exception_returns_500(self):
        with patch.object(
            registry.get_primitive("tools").get()._provider,
            "list_tools",
            new_callable=AsyncMock,
            side_effect=RuntimeError("something unexpected"),
        ):
            resp = self.client.get("/api/v1/tools")
            assert resp.status_code == 500
            assert "RuntimeError" in resp.json()["detail"]

    def test_value_error_returns_400(self):
        with patch.object(
            registry.get_primitive("tools").get()._provider,
            "invoke_tool",
            new_callable=AsyncMock,
            side_effect=ValueError("bad input"),
        ):
            resp = self.client.post(
                "/api/v1/tools/my-tool/invoke",
                json={"params": {}},
            )
            assert resp.status_code == 400
            assert "bad input" in resp.json()["detail"]
