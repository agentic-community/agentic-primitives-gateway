"""Tests for credential routes."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from agentic_primitives_gateway.auth.noop import NoopAuthBackend
from agentic_primitives_gateway.credentials.noop import NoopCredentialResolver
from agentic_primitives_gateway.credentials.writer.noop import NoopCredentialWriter


def _make_app(writer=None):
    """Create a minimal FastAPI app with credential routes."""
    from fastapi import FastAPI

    from agentic_primitives_gateway.auth.middleware import AuthenticationMiddleware
    from agentic_primitives_gateway.credentials.middleware import CredentialResolutionMiddleware
    from agentic_primitives_gateway.middleware import RequestContextMiddleware
    from agentic_primitives_gateway.routes import credentials

    app = FastAPI()

    if writer is not None:
        credentials.set_credential_writer(writer)

    app.include_router(credentials.router)

    # Middleware order matches main.py
    app.add_middleware(CredentialResolutionMiddleware)
    app.add_middleware(AuthenticationMiddleware)
    app.add_middleware(RequestContextMiddleware)

    app.state.auth_backend = NoopAuthBackend()
    # Need a resolver so the middleware stores the access token
    app.state.credential_resolver = NoopCredentialResolver()

    return app


class TestCredentialRoutes:
    @pytest.mark.asyncio
    async def test_read_credentials_with_noop_writer(self):
        writer = NoopCredentialWriter()
        app = _make_app(writer=writer)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/credentials",
                headers={"Authorization": "Bearer test-token"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["attributes"] == {}

    @pytest.mark.asyncio
    async def test_write_credentials_noop_returns_501(self):
        writer = NoopCredentialWriter()
        app = _make_app(writer=writer)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/api/v1/credentials",
                json={"attributes": {"key": "value"}},
                headers={"Authorization": "Bearer test-token"},
            )
            assert resp.status_code == 501

    @pytest.mark.asyncio
    async def test_write_credentials_with_real_writer(self):
        writer = AsyncMock()
        writer.write = AsyncMock(return_value=None)
        writer.read = AsyncMock(return_value={"key": "value"})

        app = _make_app(writer=writer)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/api/v1/credentials",
                json={"attributes": {"key": "value"}},
                headers={"Authorization": "Bearer test-token"},
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "updated"

    @pytest.mark.asyncio
    async def test_status_endpoint(self):
        app = _make_app(writer=NoopCredentialWriter())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/credentials/status",
                headers={"Authorization": "Bearer test-token"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "source" in data


class TestCredentialErrorPaths:
    """Cover the error branches in credentials routes — IDP 4xx/5xx,
    unexpected exceptions, not-configured, delete, mask."""

    @pytest.mark.asyncio
    async def test_read_returns_502_on_idp_http_error(self):
        import httpx

        writer = AsyncMock()
        response = httpx.Response(status_code=503, text="backend down")
        writer.read = AsyncMock(
            side_effect=httpx.HTTPStatusError("x", request=httpx.Request("GET", "http://x"), response=response)
        )
        app = _make_app(writer=writer)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/credentials",
                headers={"Authorization": "Bearer test-token"},
            )
            assert resp.status_code == 502
            assert "503" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_read_returns_502_on_unexpected_exception(self):
        writer = AsyncMock()
        writer.read = AsyncMock(side_effect=RuntimeError("boom"))
        app = _make_app(writer=writer)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/credentials",
                headers={"Authorization": "Bearer test-token"},
            )
            assert resp.status_code == 502

    @pytest.mark.asyncio
    async def test_read_masks_secrets(self):
        writer = AsyncMock()
        writer.read = AsyncMock(return_value={"apg.langfuse.public_key": "sk-supersecret12345"})
        app = _make_app(writer=writer)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/credentials",
                headers={"Authorization": "Bearer test-token"},
            )
            assert resp.status_code == 200
            # Value should be masked — shouldn't leak the raw secret.
            body = resp.json()["attributes"]
            assert "apg.langfuse.public_key" in body
            assert "supersecret" not in body["apg.langfuse.public_key"]

    @pytest.mark.asyncio
    async def test_write_returns_502_on_idp_error(self):
        import httpx

        writer = AsyncMock()
        response = httpx.Response(status_code=400, text="bad request")
        writer.write = AsyncMock(
            side_effect=httpx.HTTPStatusError("x", request=httpx.Request("PUT", "http://x"), response=response)
        )
        app = _make_app(writer=writer)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/api/v1/credentials",
                json={"attributes": {"apg.langfuse.public_key": "x"}},
                headers={"Authorization": "Bearer test-token"},
            )
            assert resp.status_code == 502

    @pytest.mark.asyncio
    async def test_write_returns_502_on_unexpected_exception(self):
        writer = AsyncMock()
        writer.write = AsyncMock(side_effect=RuntimeError("boom"))
        app = _make_app(writer=writer)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/api/v1/credentials",
                json={"attributes": {"apg.langfuse.public_key": "x"}},
                headers={"Authorization": "Bearer test-token"},
            )
            assert resp.status_code == 502

    @pytest.mark.asyncio
    async def test_delete_credential_success(self):
        writer = AsyncMock()
        writer.delete = AsyncMock(return_value=None)
        app = _make_app(writer=writer)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                "/api/v1/credentials/apg.langfuse.public_key",
                headers={"Authorization": "Bearer test-token"},
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_delete_noop_writer_returns_501(self):
        app = _make_app(writer=NoopCredentialWriter())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                "/api/v1/credentials/apg.langfuse.public_key",
                headers={"Authorization": "Bearer test-token"},
            )
            assert resp.status_code == 501

    @pytest.mark.asyncio
    async def test_delete_returns_502_on_idp_error(self):
        import httpx

        writer = AsyncMock()
        response = httpx.Response(status_code=404, text="not found")
        writer.delete = AsyncMock(
            side_effect=httpx.HTTPStatusError("x", request=httpx.Request("DELETE", "http://x"), response=response)
        )
        app = _make_app(writer=writer)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                "/api/v1/credentials/apg.langfuse.public_key",
                headers={"Authorization": "Bearer test-token"},
            )
            assert resp.status_code == 502

    @pytest.mark.asyncio
    async def test_delete_returns_502_on_unexpected_exception(self):
        writer = AsyncMock()
        writer.delete = AsyncMock(side_effect=RuntimeError("boom"))
        app = _make_app(writer=writer)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                "/api/v1/credentials/apg.langfuse.public_key",
                headers={"Authorization": "Bearer test-token"},
            )
            assert resp.status_code == 502

    @pytest.mark.asyncio
    async def test_no_writer_returns_501(self):
        from agentic_primitives_gateway.routes import credentials

        credentials._writer = None
        app = _make_app(writer=None)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/credentials",
                headers={"Authorization": "Bearer test-token"},
            )
            assert resp.status_code == 501
