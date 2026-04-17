from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.emit import set_audit_router
from agentic_primitives_gateway.audit.middleware import AuditMiddleware
from agentic_primitives_gateway.audit.models import AuditAction, AuditEvent
from agentic_primitives_gateway.audit.router import AuditRouter
from agentic_primitives_gateway.middleware import RequestContextMiddleware


class _CollectorSink(AuditSink):
    def __init__(self) -> None:
        self.name = "collector"
        self.events: list[AuditEvent] = []

    async def emit(self, event: AuditEvent) -> None:
        self.events.append(event)


@pytest.fixture
async def audit_app():
    sink = _CollectorSink()
    router = AuditRouter([sink])
    await router.start()
    set_audit_router(router)

    app = FastAPI()

    @app.get("/api/v1/ok")
    async def ok():
        return {"status": "ok"}

    @app.get("/api/v1/boom")
    async def boom():
        raise ValueError("nope")

    @app.exception_handler(ValueError)
    async def _value_error(request, exc):  # type: ignore[no-untyped-def]
        from starlette.responses import JSONResponse

        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/healthz")
    async def hz():
        return {"status": "ok"}

    app.add_middleware(AuditMiddleware)
    app.add_middleware(RequestContextMiddleware)

    try:
        yield app, sink, router
    finally:
        await router.shutdown(timeout=1.0)
        set_audit_router(None)


@pytest.mark.asyncio
async def test_emits_http_request_for_200(audit_app):
    app, sink, _router = audit_app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/api/v1/ok", headers={"x-correlation-id": "corr-1"})
    # Allow the worker to drain.
    await asyncio.sleep(0.05)

    matches = [e for e in sink.events if e.action == AuditAction.HTTP_REQUEST]
    assert len(matches) == 1
    event = matches[0]
    assert event.http_status == 200
    assert event.http_method == "GET"
    assert event.http_path == "/api/v1/ok"
    assert event.outcome == "success"
    assert event.correlation_id == "corr-1"
    assert event.request_id  # populated by RequestContextMiddleware
    assert event.duration_ms is not None and event.duration_ms >= 0


@pytest.mark.asyncio
async def test_emits_failure_for_4xx(audit_app):
    app, sink, _router = audit_app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/api/v1/boom")
    await asyncio.sleep(0.05)

    matches = [e for e in sink.events if e.action == AuditAction.HTTP_REQUEST]
    assert len(matches) == 1
    assert matches[0].http_status == 400
    assert matches[0].outcome == "failure"


@pytest.mark.asyncio
async def test_exempt_paths_not_audited(audit_app):
    app, sink, _router = audit_app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/healthz")
    await asyncio.sleep(0.05)

    matches = [e for e in sink.events if e.action == AuditAction.HTTP_REQUEST]
    assert len(matches) == 0
