from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentic_primitives_gateway.config import Settings
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.registry import registry


@pytest.fixture(autouse=True)
def _init_multi_provider_registry() -> None:
    """Initialize registry with multiple memory providers for routing tests."""
    test_settings = Settings(
        providers={
            "memory": {
                "default": "primary",
                "backends": {
                    "primary": {
                        "backend": "agentic_primitives_gateway.primitives.memory.in_memory.InMemoryProvider",
                        "config": {},
                    },
                    "secondary": {
                        "backend": "agentic_primitives_gateway.primitives.memory.in_memory.InMemoryProvider",
                        "config": {},
                    },
                },
            },
            "observability": {
                "backend": "agentic_primitives_gateway.primitives.observability.noop.NoopObservabilityProvider",
                "config": {},
            },
            "gateway": {
                "backend": "agentic_primitives_gateway.primitives.gateway.noop.NoopGatewayProvider",
                "config": {},
            },
            "tools": {
                "backend": "agentic_primitives_gateway.primitives.tools.noop.NoopToolsProvider",
                "config": {},
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
        }
    )
    registry.initialize(test_settings)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestProviderDiscovery:
    def test_list_providers(self, client: TestClient) -> None:
        resp = client.get("/api/v1/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["memory"]["default"] == "primary"
        assert set(data["memory"]["available"]) == {"primary", "secondary"}
        assert "identity" in data
        assert "browser" in data

    def test_all_primitives_listed(self, client: TestClient) -> None:
        resp = client.get("/api/v1/providers")
        data = resp.json()
        expected = {
            "memory",
            "observability",
            "gateway",
            "tools",
            "identity",
            "code_interpreter",
            "browser",
            "policy",
            "evaluations",
            "tasks",
        }
        assert set(data.keys()) == expected


class TestProviderRouting:
    def test_default_provider_used(self, client: TestClient) -> None:
        """Without routing headers, the default provider is used."""
        resp = client.post(
            "/api/v1/memory/ns1",
            json={"key": "k1", "content": "on primary"},
        )
        assert resp.status_code == 201

        resp = client.get("/api/v1/memory/ns1/k1")
        assert resp.json()["content"] == "on primary"

    def test_route_to_specific_provider(self, client: TestClient) -> None:
        """X-Provider-Memory header routes to a specific backend."""
        # Store on secondary
        resp = client.post(
            "/api/v1/memory/ns1",
            json={"key": "k1", "content": "on secondary"},
            headers={"x-provider-memory": "secondary"},
        )
        assert resp.status_code == 201

        # Not found on primary (default)
        resp = client.get("/api/v1/memory/ns1/k1")
        assert resp.status_code == 404

        # Found on secondary
        resp = client.get(
            "/api/v1/memory/ns1/k1",
            headers={"x-provider-memory": "secondary"},
        )
        assert resp.status_code == 200
        assert resp.json()["content"] == "on secondary"

    def test_default_header_applies_to_all(self, client: TestClient) -> None:
        """X-Provider header sets default for all primitives."""
        # Store on secondary using the global default header
        resp = client.post(
            "/api/v1/memory/ns1",
            json={"key": "k1", "content": "via global header"},
            headers={"x-provider": "secondary"},
        )
        assert resp.status_code == 201

        # Confirm it went to secondary
        resp = client.get(
            "/api/v1/memory/ns1/k1",
            headers={"x-provider": "secondary"},
        )
        assert resp.json()["content"] == "via global header"

    def test_primitive_header_overrides_default(self, client: TestClient) -> None:
        """X-Provider-Memory overrides X-Provider for memory."""
        client.post(
            "/api/v1/memory/ns1",
            json={"key": "k1", "content": "on primary"},
            headers={"x-provider": "secondary", "x-provider-memory": "primary"},
        )
        # Should be on primary despite X-Provider saying secondary
        resp = client.get("/api/v1/memory/ns1/k1")
        assert resp.status_code == 200
        assert resp.json()["content"] == "on primary"

    def test_unknown_provider_returns_400(self, client: TestClient) -> None:
        """Requesting a non-existent provider returns 400."""
        resp = client.post(
            "/api/v1/memory/ns1",
            json={"key": "k1", "content": "test"},
            headers={"x-provider-memory": "nonexistent"},
        )
        assert resp.status_code == 400
        assert "nonexistent" in resp.json()["detail"]
        assert "primary" in resp.json()["detail"]

    def test_isolation_between_providers(self, client: TestClient) -> None:
        """Data stored on one provider is not visible on another."""
        client.post(
            "/api/v1/memory/ns1",
            json={"key": "primary-only", "content": "hello"},
        )
        client.post(
            "/api/v1/memory/ns1",
            json={"key": "secondary-only", "content": "world"},
            headers={"x-provider-memory": "secondary"},
        )

        # Primary has primary-only, not secondary-only
        assert client.get("/api/v1/memory/ns1/primary-only").status_code == 200
        assert client.get("/api/v1/memory/ns1/secondary-only").status_code == 404

        # Secondary has secondary-only, not primary-only
        assert (
            client.get(
                "/api/v1/memory/ns1/secondary-only",
                headers={"x-provider-memory": "secondary"},
            ).status_code
            == 200
        )
        assert (
            client.get(
                "/api/v1/memory/ns1/primary-only",
                headers={"x-provider-memory": "secondary"},
            ).status_code
            == 404
        )


class TestLegacyConfigCompat:
    def test_legacy_single_provider_format(self) -> None:
        """Legacy config with 'backend' key should still work."""
        from agentic_primitives_gateway.config import PrimitiveProvidersConfig

        cfg = PrimitiveProvidersConfig.model_validate(
            {
                "backend": "agentic_primitives_gateway.primitives.memory.in_memory.InMemoryProvider",
                "config": {},
            }
        )
        assert cfg.default == "default"
        assert "default" in cfg.backends
        assert (
            cfg.backends["default"].backend == "agentic_primitives_gateway.primitives.memory.in_memory.InMemoryProvider"
        )
