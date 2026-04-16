"""Tests for credential resolution middleware."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from agentic_primitives_gateway.auth.noop import NoopAuthBackend
from agentic_primitives_gateway.context import (
    get_access_token,
    get_aws_credentials,
    get_service_credentials,
)
from agentic_primitives_gateway.credentials.middleware import CredentialResolutionMiddleware
from agentic_primitives_gateway.credentials.models import ResolvedCredentials
from agentic_primitives_gateway.credentials.noop import NoopCredentialResolver
from agentic_primitives_gateway.middleware import RequestContextMiddleware


def _make_app(credential_resolver=None, auth_backend=None):
    """Create a minimal FastAPI app with credential resolution middleware."""
    from fastapi import FastAPI

    from agentic_primitives_gateway.auth.middleware import AuthenticationMiddleware

    app = FastAPI()

    @app.get("/api/v1/test")
    async def test_endpoint():
        aws = get_aws_credentials()
        langfuse = get_service_credentials("langfuse")
        token = get_access_token()
        return {
            "has_aws": aws is not None,
            "langfuse": langfuse,
            "has_access_token": token is not None,
        }

    # Middleware order: RequestContext → Auth → CredentialResolution → handler
    # (Starlette runs last-added outermost)
    app.add_middleware(CredentialResolutionMiddleware)
    app.add_middleware(AuthenticationMiddleware)
    app.add_middleware(RequestContextMiddleware)

    app.state.auth_backend = auth_backend or NoopAuthBackend()
    if credential_resolver is not None:
        app.state.credential_resolver = credential_resolver

    return app


class TestCredentialResolutionMiddleware:
    @pytest.mark.asyncio
    async def test_no_resolver_passes_through(self):
        app = _make_app(credential_resolver=None)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/test")
            assert resp.status_code == 200
            data = resp.json()
            assert data["has_aws"] is False
            assert data["langfuse"] is None

    @pytest.mark.asyncio
    async def test_noop_resolver_passes_through(self):
        app = _make_app(credential_resolver=NoopCredentialResolver())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/test")
            assert resp.status_code == 200
            data = resp.json()
            assert data["langfuse"] is None

    @pytest.mark.asyncio
    async def test_oidc_resolver_populates_service_creds(self):
        resolver = AsyncMock()
        resolver.resolve = AsyncMock(
            return_value=ResolvedCredentials(
                service_credentials={"langfuse": {"public_key": "pk-abc", "secret_key": "sk-xyz"}}
            )
        )

        app = _make_app(credential_resolver=resolver)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/test",
                headers={"Authorization": "Bearer my-jwt-token"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["langfuse"] == {"public_key": "pk-abc", "secret_key": "sk-xyz"}

    @pytest.mark.asyncio
    async def test_explicit_headers_take_priority(self):
        """X-Cred-* headers should prevent OIDC resolution."""
        resolver = AsyncMock()
        resolver.resolve = AsyncMock(
            return_value=ResolvedCredentials(service_credentials={"langfuse": {"public_key": "from-oidc"}})
        )

        app = _make_app(credential_resolver=resolver)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/test",
                headers={
                    "Authorization": "Bearer my-jwt",
                    "X-AWS-Access-Key-Id": "AKID",
                    "X-AWS-Secret-Access-Key": "secret",
                    "X-Cred-Langfuse-Public-Key": "from-headers",
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            # Headers win — resolver should not even be called
            assert data["has_aws"] is True
            assert data["langfuse"]["public_key"] == "from-headers"

    @pytest.mark.asyncio
    async def test_access_token_stored(self):
        resolver = AsyncMock()
        resolver.resolve = AsyncMock(return_value=None)

        app = _make_app(credential_resolver=resolver)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/test",
                headers={"Authorization": "Bearer test-token-123"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["has_access_token"] is True

    @pytest.mark.asyncio
    async def test_resolver_returns_none_passes_through(self):
        resolver = AsyncMock()
        resolver.resolve = AsyncMock(return_value=None)

        app = _make_app(credential_resolver=resolver)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/test",
                headers={"Authorization": "Bearer my-jwt"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["langfuse"] is None
