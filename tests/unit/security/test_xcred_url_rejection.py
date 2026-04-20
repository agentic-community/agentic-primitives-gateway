"""Vuln 2: URL/host keys in X-Cred-* headers are rejected (SSRF guard)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentic_primitives_gateway.context import get_service_credentials
from agentic_primitives_gateway.middleware import RequestContextMiddleware, _is_forbidden_cred_key


class TestForbiddenKeyPredicate:
    @pytest.mark.parametrize(
        "key",
        [
            "base_url",
            "server_url",
            "hub_url",
            "endpoint_url",
            "endpoint",
            "url",
            "uri",
            "host",
            "origin",
            "port",
            "HOST",  # case-insensitive
            "BASE_URL",
        ],
    )
    def test_url_shaped_keys_rejected(self, key: str):
        assert _is_forbidden_cred_key(key) is True

    @pytest.mark.parametrize(
        "key",
        [
            "public_key",
            "secret_key",
            "api_key",
            "token",
            "admin_client_id",
            "admin_client_secret",
            "memory_id",
        ],
    )
    def test_credential_keys_accepted(self, key: str):
        assert _is_forbidden_cred_key(key) is False


@pytest.mark.asyncio
async def test_middleware_strips_url_shaped_x_cred_headers():
    """A request with X-Cred-Langfuse-Base-Url should not populate service creds."""
    app = FastAPI()

    @app.get("/check")
    async def check() -> dict[str, object]:
        creds = get_service_credentials("langfuse") or {}
        return {"creds": creds}

    app.add_middleware(RequestContextMiddleware)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/check",
            headers={
                "X-Cred-Langfuse-Base-Url": "http://169.254.169.254",
                "X-Cred-Langfuse-Public-Key": "pk-x",
                "X-Cred-Langfuse-Secret-Key": "sk-x",
            },
        )
    body = resp.json()
    # base_url is stripped; legitimate secrets pass through.
    assert "base_url" not in body["creds"]
    assert body["creds"].get("public_key") == "pk-x"
    assert body["creds"].get("secret_key") == "sk-x"
