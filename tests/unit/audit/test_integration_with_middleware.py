from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.emit import set_audit_router
from agentic_primitives_gateway.audit.middleware import AuditMiddleware
from agentic_primitives_gateway.audit.models import AuditAction, AuditEvent
from agentic_primitives_gateway.audit.router import AuditRouter
from agentic_primitives_gateway.auth.middleware import AuthenticationMiddleware
from agentic_primitives_gateway.enforcement.middleware import PolicyEnforcementMiddleware
from agentic_primitives_gateway.middleware import RequestContextMiddleware


class _CollectorSink(AuditSink):
    def __init__(self) -> None:
        self.name = "collector"
        self.events: list[AuditEvent] = []

    async def emit(self, event: AuditEvent) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_auth_failure_emits_audit_event():
    """An invalid token on a non-exempt path emits auth.failure."""
    sink = _CollectorSink()
    router = AuditRouter([sink])
    await router.start()
    set_audit_router(router)

    app = FastAPI()

    @app.get("/api/v1/memory/{ns}")
    async def list_mem(ns: str):
        return {"status": "ok"}

    # Auth backend that always fails.
    backend = AsyncMock()
    backend.authenticate = AsyncMock(return_value=None)
    app.state.auth_backend = backend

    app.add_middleware(AuthenticationMiddleware)
    app.add_middleware(AuditMiddleware)
    app.add_middleware(RequestContextMiddleware)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/memory/ns1")
            assert resp.status_code == 401
        await asyncio.sleep(0.05)
    finally:
        await router.shutdown(timeout=1.0)
        set_audit_router(None)

    actions = [e.action for e in sink.events]
    assert AuditAction.AUTH_FAILURE in actions
    assert AuditAction.HTTP_REQUEST in actions
    failure_event = next(e for e in sink.events if e.action == AuditAction.AUTH_FAILURE)
    assert failure_event.reason == "invalid_or_missing_credentials"
    assert failure_event.metadata["backend"] == "AsyncMock"


@pytest.mark.asyncio
async def test_policy_deny_emits_audit_event():
    sink = _CollectorSink()
    router = AuditRouter([sink])
    await router.start()
    set_audit_router(router)

    app = FastAPI()

    @app.get("/api/v1/memory/{ns}")
    async def list_mem(ns: str):
        return {"status": "ok"}

    enforcer = AsyncMock()
    enforcer.authorize = AsyncMock(return_value=False)
    app.state.enforcer = enforcer

    app.add_middleware(PolicyEnforcementMiddleware)
    app.add_middleware(AuditMiddleware)
    app.add_middleware(RequestContextMiddleware)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/memory/ns1")
            assert resp.status_code == 403
        await asyncio.sleep(0.05)
    finally:
        await router.shutdown(timeout=1.0)
        set_audit_router(None)

    deny = [e for e in sink.events if e.action == AuditAction.POLICY_DENY]
    assert len(deny) == 1
    assert deny[0].outcome == "deny"
    assert deny[0].reason == "cedar_deny"


@pytest.mark.asyncio
async def test_policy_allow_emits_audit_event():
    sink = _CollectorSink()
    router = AuditRouter([sink])
    await router.start()
    set_audit_router(router)

    app = FastAPI()

    @app.get("/api/v1/memory/{ns}")
    async def list_mem(ns: str):
        return {"status": "ok"}

    enforcer = AsyncMock()
    enforcer.authorize = AsyncMock(return_value=True)
    app.state.enforcer = enforcer

    app.add_middleware(PolicyEnforcementMiddleware)
    app.add_middleware(AuditMiddleware)
    app.add_middleware(RequestContextMiddleware)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/memory/ns1")
            assert resp.status_code == 200
        await asyncio.sleep(0.05)
    finally:
        await router.shutdown(timeout=1.0)
        set_audit_router(None)

    allow = [e for e in sink.events if e.action == AuditAction.POLICY_ALLOW]
    assert len(allow) == 1
    assert allow[0].outcome == "allow"
