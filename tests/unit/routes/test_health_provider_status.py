"""Tests for authenticated provider status endpoint and helpers in routes/health.py.

Covers:
- _check_provider_authenticated() — runs healthcheck on main event loop with user creds
- provider_status() endpoint — authenticated provider healthcheck
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import set_authenticated_principal
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.routes.health import _check_provider_authenticated


def _admin_principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(id="admin", type="user", scopes=frozenset({"admin"}))


# ── _check_provider_authenticated tests ──────────────────────────────


class TestCheckProviderAuthenticated:
    @pytest.mark.asyncio
    async def test_healthy_provider_returns_ok(self):
        """Provider returning True produces status 'ok'."""
        mock_provider = AsyncMock()
        mock_provider.healthcheck = AsyncMock(return_value=True)

        mock_prim = MagicMock()
        mock_prim.get.return_value = mock_provider

        with patch("agentic_primitives_gateway.routes.health.registry") as mock_registry:
            mock_registry.get_primitive.return_value = mock_prim
            result = await _check_provider_authenticated("memory", "default")

        prim, prov, key, status = result
        assert prim == "memory"
        assert prov == "default"
        assert key == "memory/default"
        assert status == "ok"

    @pytest.mark.asyncio
    async def test_unhealthy_provider_returns_down(self):
        """Provider returning False produces status 'down'."""
        mock_provider = AsyncMock()
        mock_provider.healthcheck = AsyncMock(return_value=False)

        mock_prim = MagicMock()
        mock_prim.get.return_value = mock_provider

        with patch("agentic_primitives_gateway.routes.health.registry") as mock_registry:
            mock_registry.get_primitive.return_value = mock_prim
            result = await _check_provider_authenticated("memory", "default")

        _, _, _, status = result
        assert status == "down"

    @pytest.mark.asyncio
    async def test_provider_returns_string_status(self):
        """Provider returning a string status passes it through."""
        mock_provider = AsyncMock()
        mock_provider.healthcheck = AsyncMock(return_value="reachable")

        mock_prim = MagicMock()
        mock_prim.get.return_value = mock_provider

        with patch("agentic_primitives_gateway.routes.health.registry") as mock_registry:
            mock_registry.get_primitive.return_value = mock_prim
            result = await _check_provider_authenticated("memory", "default")

        _, _, _, status = result
        assert status == "reachable"

    @pytest.mark.asyncio
    async def test_provider_exception_returns_down(self):
        """Provider raising an exception produces status 'down'."""
        mock_provider = AsyncMock()
        mock_provider.healthcheck = AsyncMock(side_effect=RuntimeError("connection failed"))

        mock_prim = MagicMock()
        mock_prim.get.return_value = mock_provider

        with patch("agentic_primitives_gateway.routes.health.registry") as mock_registry:
            mock_registry.get_primitive.return_value = mock_prim
            result = await _check_provider_authenticated("memory", "default")

        _, _, _, status = result
        assert status == "down"

    @pytest.mark.asyncio
    async def test_provider_timeout_returns_down(self):
        """Provider that exceeds timeout produces status 'down'."""

        async def slow_healthcheck():
            await asyncio.sleep(100)
            return True

        mock_provider = MagicMock()
        mock_provider.healthcheck = slow_healthcheck

        mock_prim = MagicMock()
        mock_prim.get.return_value = mock_provider

        with (
            patch("agentic_primitives_gateway.routes.health.registry") as mock_registry,
            patch("agentic_primitives_gateway.routes.health._HEALTHCHECK_TIMEOUT", 0.01),
        ):
            mock_registry.get_primitive.return_value = mock_prim
            result = await _check_provider_authenticated("memory", "default")

        _, _, _, status = result
        assert status == "down"

    @pytest.mark.asyncio
    async def test_returns_correct_key_format(self):
        """Key format is 'primitive/provider_name'."""
        mock_provider = AsyncMock()
        mock_provider.healthcheck = AsyncMock(return_value=True)

        mock_prim = MagicMock()
        mock_prim.get.return_value = mock_provider

        with patch("agentic_primitives_gateway.routes.health.registry") as mock_registry:
            mock_registry.get_primitive.return_value = mock_prim
            result = await _check_provider_authenticated("observability", "langfuse")

        prim, prov, key, _ = result
        assert prim == "observability"
        assert prov == "langfuse"
        assert key == "observability/langfuse"


# ── provider_status endpoint tests ───────────────────────────────────


class TestProviderStatusEndpoint:
    def _client(self) -> TestClient:
        return TestClient(app, raise_server_exceptions=False)

    def test_returns_checks_for_all_providers(self):
        """Endpoint returns checks dict with all registered providers."""
        set_authenticated_principal(_admin_principal())
        client = self._client()
        resp = client.get("/api/v1/providers/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "checks" in data
        # The conftest registers noop providers for all primitives
        checks = data["checks"]
        assert isinstance(checks, dict)
        # At minimum we should have entries (conftest registers 10 primitives)
        assert len(checks) > 0

    def test_checks_contain_primitive_provider_keys(self):
        """Check keys are in 'primitive/provider_name' format."""
        set_authenticated_principal(_admin_principal())
        client = self._client()
        resp = client.get("/api/v1/providers/status")
        data = resp.json()
        for key in data["checks"]:
            assert "/" in key, f"Key {key} should contain '/'"

    def test_noop_providers_are_healthy(self):
        """All noop providers from conftest should report ok."""
        set_authenticated_principal(_admin_principal())
        client = self._client()
        resp = client.get("/api/v1/providers/status")
        data = resp.json()
        checks = data["checks"]
        for key, status in checks.items():
            assert status in ("ok", "reachable"), f"Provider {key} status={status}, expected ok or reachable"

    def test_with_mocked_unhealthy_provider(self):
        """Unhealthy provider shows 'down' in response."""

        async def mock_check(primitive, provider_name):
            if primitive == "memory":
                return primitive, provider_name, f"{primitive}/{provider_name}", "down"
            return primitive, provider_name, f"{primitive}/{provider_name}", "ok"

        set_authenticated_principal(_admin_principal())
        with patch(
            "agentic_primitives_gateway.routes.health._check_provider_authenticated",
            side_effect=mock_check,
        ):
            client = self._client()
            resp = client.get("/api/v1/providers/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["checks"]["memory/default"] == "down"

    def test_handles_exception_in_check(self):
        """BaseException results from gather are silently skipped."""

        async def mock_check(primitive, provider_name):
            if primitive == "memory":
                raise RuntimeError("unexpected failure")
            return primitive, provider_name, f"{primitive}/{provider_name}", "ok"

        set_authenticated_principal(_admin_principal())
        with patch(
            "agentic_primitives_gateway.routes.health._check_provider_authenticated",
            side_effect=mock_check,
        ):
            client = self._client()
            resp = client.get("/api/v1/providers/status")
        assert resp.status_code == 200
        data = resp.json()
        # memory should NOT be in checks since it raised an exception
        memory_keys = [k for k in data["checks"] if k.startswith("memory/")]
        assert len(memory_keys) == 0
