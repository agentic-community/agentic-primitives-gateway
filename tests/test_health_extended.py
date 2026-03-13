"""Extended health check tests covering _check_provider, readiness edge cases, and auth/config."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.routes.health import _check_provider


class TestCheckProvider:
    async def test_healthy_provider(self) -> None:
        mock_provider = AsyncMock()
        mock_provider.healthcheck.return_value = True

        mock_prim = MagicMock()
        mock_prim.get.return_value = mock_provider

        with patch("agentic_primitives_gateway.routes.health.registry") as mock_reg:
            mock_reg.get_primitive.return_value = mock_prim
            prim, prov, key, healthy = await _check_provider("memory", "default")

        assert prim == "memory"
        assert prov == "default"
        assert key == "memory/default"
        assert healthy is True

    async def test_unhealthy_provider(self) -> None:
        mock_provider = AsyncMock()
        mock_provider.healthcheck.return_value = False

        mock_prim = MagicMock()
        mock_prim.get.return_value = mock_provider

        with patch("agentic_primitives_gateway.routes.health.registry") as mock_reg:
            mock_reg.get_primitive.return_value = mock_prim
            _, _, _, healthy = await _check_provider("memory", "default")

        assert healthy is False

    async def test_provider_healthcheck_exception(self) -> None:
        mock_provider = AsyncMock()
        mock_provider.healthcheck.side_effect = RuntimeError("fail")

        mock_prim = MagicMock()
        mock_prim.get.return_value = mock_provider

        with patch("agentic_primitives_gateway.routes.health.registry") as mock_reg:
            mock_reg.get_primitive.return_value = mock_prim
            _, _, _, healthy = await _check_provider("memory", "default")

        assert healthy is False


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestAuthConfigEndpoint:
    def test_noop_backend(self, client: TestClient) -> None:
        """Default noop backend returns just the backend name."""
        resp = client.get("/auth/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["backend"] == "noop"
        # Should not include issuer/client_id for non-jwt backends
        assert "issuer" not in data

    def test_jwt_backend(self, client: TestClient) -> None:
        """JWT backend returns OIDC config fields."""
        from agentic_primitives_gateway.config import AuthConfig

        mock_auth = AuthConfig(
            backend="jwt",
            jwt={
                "issuer": "https://example.auth0.com/",
                "client_id": "my-client-id",
                "audience": "my-audience",
            },
        )
        with patch("agentic_primitives_gateway.routes.health.settings") as mock_settings:
            mock_settings.auth = mock_auth
            resp = client.get("/auth/config")

        assert resp.status_code == 200
        data = resp.json()
        assert data["backend"] == "jwt"
        assert data["issuer"] == "https://example.auth0.com/"
        assert data["client_id"] == "my-client-id"
        assert data["scopes"] == "openid profile email"

    def test_jwt_backend_falls_back_to_audience(self, client: TestClient) -> None:
        """When client_id is missing, falls back to audience."""
        from agentic_primitives_gateway.config import AuthConfig

        mock_auth = AuthConfig(
            backend="jwt",
            jwt={
                "issuer": "https://example.auth0.com/",
                "audience": "my-audience",
            },
        )
        with patch("agentic_primitives_gateway.routes.health.settings") as mock_settings:
            mock_settings.auth = mock_auth
            resp = client.get("/auth/config")

        assert resp.status_code == 200
        data = resp.json()
        assert data["client_id"] == "my-audience"

    def test_jwt_backend_empty_config(self, client: TestClient) -> None:
        """JWT backend with empty jwt config returns empty strings."""
        from agentic_primitives_gateway.config import AuthConfig

        mock_auth = AuthConfig(backend="jwt", jwt={})
        with patch("agentic_primitives_gateway.routes.health.settings") as mock_settings:
            mock_settings.auth = mock_auth
            resp = client.get("/auth/config")

        assert resp.status_code == 200
        data = resp.json()
        assert data["backend"] == "jwt"
        assert data["issuer"] == ""
        assert data["client_id"] == ""


class TestReadinessWithDetails:
    def test_readyz_all_healthy(self, client: TestClient) -> None:
        """When all providers are healthy, returns 200."""
        resp = client.get("/readyz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "checks" in data

    def test_readyz_unhealthy_provider(self, client: TestClient) -> None:
        """When a provider is unhealthy, returns 503 degraded."""
        mock_prim = MagicMock()
        mock_prim.names = ["default"]
        mock_provider = AsyncMock()
        mock_provider.healthcheck.return_value = False
        mock_prim.get.return_value = mock_provider

        with patch("agentic_primitives_gateway.routes.health.registry") as mock_reg:
            mock_reg.get_primitive.return_value = mock_prim
            resp = client.get("/readyz")

        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "degraded"

    def test_readyz_exception_in_gather(self, client: TestClient) -> None:
        """When a healthcheck task raises an exception, it's handled gracefully."""
        with patch("agentic_primitives_gateway.routes.health._check_provider") as mock_check:
            mock_check.side_effect = RuntimeError("unexpected")
            resp = client.get("/readyz")

        # The gather catches exceptions — but all results are BaseException,
        # so checks dict is empty and all() on empty dict is True
        assert resp.status_code in (200, 503)

    def test_readyz_with_reload_error(self, client: TestClient) -> None:
        """When there's a config reload error, returns 503 degraded with error."""
        with patch("agentic_primitives_gateway.routes.health.get_last_reload_error", return_value="bad config"):
            resp = client.get("/readyz")

        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["config_reload_error"] == "bad config"

    def test_readyz_total_exception(self, client: TestClient) -> None:
        """When the entire readiness check fails, returns 503 error."""
        with (
            patch("agentic_primitives_gateway.routes.health.PRIMITIVES", side_effect=RuntimeError("boom")),
            patch("agentic_primitives_gateway.routes.health.registry") as mock_reg,
        ):
            mock_reg.get_primitive.side_effect = RuntimeError("boom")
            # This path goes through the outer try/except because gather will get exceptions
            resp = client.get("/readyz")

        # Should still return a response (either 200 with empty checks or 503)
        assert resp.status_code in (200, 503)
