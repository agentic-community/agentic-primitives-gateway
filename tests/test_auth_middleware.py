from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from agentic_primitives_gateway.auth.api_key import ApiKeyAuthBackend
from agentic_primitives_gateway.auth.middleware import AuthenticationMiddleware
from agentic_primitives_gateway.auth.noop import NoopAuthBackend
from agentic_primitives_gateway.context import get_authenticated_principal


def _make_app(auth_backend=None):
    """Create a minimal FastAPI app with auth middleware."""
    from fastapi import FastAPI

    app = FastAPI()

    @app.get("/api/v1/test")
    async def test_endpoint():
        principal = get_authenticated_principal()
        if principal is None:
            return {"principal": None}
        return {
            "principal_id": principal.id,
            "principal_type": principal.type,
            "groups": sorted(principal.groups),
            "scopes": sorted(principal.scopes),
        }

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/ui/some-page")
    async def ui_page():
        return {"page": "test"}

    @app.get("/metrics")
    async def metrics():
        return "ok"

    app.add_middleware(AuthenticationMiddleware)

    if auth_backend is not None:
        app.state.auth_backend = auth_backend

    return app


class TestAuthMiddlewareNoop:
    """Tests with noop auth backend."""

    @pytest.mark.asyncio
    async def test_noop_sets_admin_principal(self):
        app = _make_app(NoopAuthBackend())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/test")
            assert resp.status_code == 200
            data = resp.json()
            assert data["principal_id"] == "noop"
            assert data["principal_type"] == "user"
            assert "admin" in data["scopes"]

    @pytest.mark.asyncio
    async def test_no_backend_sets_noop(self):
        """When no auth backend is configured, noop (admin) pass-through."""
        app = _make_app(auth_backend=None)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/test")
            assert resp.status_code == 200
            data = resp.json()
            # No backend = noop fallback (dev mode, full access)
            assert data["principal_id"] == "noop"


class TestAuthMiddlewareApiKey:
    """Tests with API key auth backend."""

    def _make_backend(self) -> ApiKeyAuthBackend:
        return ApiKeyAuthBackend(
            api_keys=[
                {
                    "key": "sk-valid",
                    "principal_id": "alice",
                    "principal_type": "user",
                    "groups": ["engineering"],
                    "scopes": ["admin"],
                }
            ]
        )

    @pytest.mark.asyncio
    async def test_valid_key_authenticates(self):
        app = _make_app(self._make_backend())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/test",
                headers={"Authorization": "Bearer sk-valid"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["principal_id"] == "alice"
            assert data["principal_type"] == "user"
            assert data["groups"] == ["engineering"]
            assert data["scopes"] == ["admin"]

    @pytest.mark.asyncio
    async def test_invalid_key_returns_401(self):
        app = _make_app(self._make_backend())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/test",
                headers={"Authorization": "Bearer bad-key"},
            )
            assert resp.status_code == 401
            assert resp.json()["detail"] == "Invalid or missing credentials"
            assert resp.headers["www-authenticate"] == "Bearer"

    @pytest.mark.asyncio
    async def test_missing_credentials_returns_401(self):
        app = _make_app(self._make_backend())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/test")
            assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_x_api_key_header_works(self):
        app = _make_app(self._make_backend())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/test",
                headers={"X-Api-Key": "sk-valid"},
            )
            assert resp.status_code == 200
            assert resp.json()["principal_id"] == "alice"


class TestAuthMiddlewareExemptPaths:
    """Exempt paths should skip auth entirely."""

    def _make_backend(self) -> ApiKeyAuthBackend:
        """Backend that requires auth — no valid keys means all non-exempt requests fail."""
        return ApiKeyAuthBackend(api_keys=[])

    @pytest.mark.asyncio
    async def test_healthz_exempt(self):
        app = _make_app(self._make_backend())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_ui_exempt(self):
        app = _make_app(self._make_backend())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/ui/some-page")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_metrics_exempt(self):
        app = _make_app(self._make_backend())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/metrics")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_api_endpoint_not_exempt(self):
        app = _make_app(self._make_backend())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/test")
            assert resp.status_code == 401


class TestAuthMiddlewareWithEnforcement:
    """Test that auth principal flows into enforcement middleware."""

    @pytest.mark.asyncio
    async def test_authenticated_principal_used_by_enforcement(self):
        """When a real auth backend authenticates, the enforcement middleware
        sees the principal from context, not from headers."""
        from fastapi import FastAPI

        from agentic_primitives_gateway.auth.middleware import AuthenticationMiddleware
        from agentic_primitives_gateway.enforcement.middleware import PolicyEnforcementMiddleware

        app = FastAPI()

        @app.get("/api/v1/memory/{namespace}")
        async def memory_list(namespace: str):
            return {"status": "ok"}

        # Middleware order: PolicyEnforcement → Auth → handler
        app.add_middleware(PolicyEnforcementMiddleware)
        app.add_middleware(AuthenticationMiddleware)

        backend = ApiKeyAuthBackend(
            api_keys=[
                {
                    "key": "sk-alice",
                    "principal_id": "alice",
                    "principal_type": "user",
                }
            ]
        )
        app.state.auth_backend = backend

        enforcer = AsyncMock()
        enforcer.authorize = AsyncMock(return_value=True)
        app.state.enforcer = enforcer

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get(
                "/api/v1/memory/ns1",
                headers={"Authorization": "Bearer sk-alice"},
            )

        enforcer.authorize.assert_called_once()
        call_kwargs = enforcer.authorize.call_args
        assert call_kwargs.kwargs["principal"] == 'User::"alice"'
