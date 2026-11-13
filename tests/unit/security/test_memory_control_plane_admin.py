"""Memory control-plane routes are admin-only.

Resources (e.g. AgentCore Memory) are account-level backend objects with no
gateway owner, and these calls run under the provider's AWS identity — often
the operator role — so a regular user must not reach them with an arbitrary
``memory_id``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.middleware.base import BaseHTTPMiddleware

from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import set_authenticated_principal
from agentic_primitives_gateway.routes import memory


def _app(scopes: frozenset[str]) -> FastAPI:
    class _Auth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            set_authenticated_principal(AuthenticatedPrincipal(id="u1", type="user", scopes=scopes))
            return await call_next(request)

    app = FastAPI()
    app.include_router(memory.router)
    app.add_middleware(_Auth)
    return app


_CALLS = [
    ("post", "/api/v1/memory/resources", {"name": "m"}),
    ("get", "/api/v1/memory/resources", None),
    ("get", "/api/v1/memory/resources/other-memory-id", None),
    ("delete", "/api/v1/memory/resources/other-memory-id", None),
    ("get", "/api/v1/memory/resources/other-memory-id/strategies", None),
    ("post", "/api/v1/memory/resources/other-memory-id/strategies", {"strategy": {"type": "semantic"}}),
    ("delete", "/api/v1/memory/resources/other-memory-id/strategies/s1", None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "body"), _CALLS)
async def test_non_admin_is_forbidden_and_provider_not_called(method, path, body):
    backend = AsyncMock()
    with patch.object(memory, "registry") as reg:
        reg.memory = backend
        async with AsyncClient(transport=ASGITransport(app=_app(frozenset())), base_url="http://t") as c:
            resp = await c.request(method, path, json=body)
    assert resp.status_code == 403
    assert backend.mock_calls == []


@pytest.mark.asyncio
async def test_admin_can_delete_resource():
    backend = AsyncMock()
    with patch.object(memory, "registry") as reg:
        reg.memory = backend
        async with AsyncClient(transport=ASGITransport(app=_app(frozenset({"admin"}))), base_url="http://t") as c:
            resp = await c.delete("/api/v1/memory/resources/m-1")
    assert resp.status_code == 204
    backend.delete_memory_resource.assert_awaited_once_with(memory_id="m-1")
