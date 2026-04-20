"""``network.access.denied`` audit events on SSRF-guard rejections.

Today only the X-Cred-* URL-shaped key filter emits this event.  A
future browser-SSRF guard would add its own emit path and test module.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.emit import set_audit_router
from agentic_primitives_gateway.audit.models import AuditAction, AuditEvent, AuditOutcome
from agentic_primitives_gateway.audit.router import AuditRouter
from agentic_primitives_gateway.middleware import RequestContextMiddleware


class _CollectorSink(AuditSink):
    def __init__(self) -> None:
        self.name = "collector"
        self.events: list[AuditEvent] = []

    async def emit(self, event: AuditEvent) -> None:
        self.events.append(event)


@pytest.fixture
async def audit_router():
    sink = _CollectorSink()
    router = AuditRouter([sink])
    await router.start()
    set_audit_router(router)
    try:
        yield sink
    finally:
        await router.shutdown(timeout=1.0)
        set_audit_router(None)


# ── X-Cred-* URL-shaped key rejection ──────────────────────────────────


@pytest.mark.asyncio
async def test_forbidden_xcred_header_emits_audit_event(audit_router):
    app = FastAPI()

    @app.get("/check")
    async def check() -> dict[str, int]:
        return {"ok": 1}

    app.add_middleware(RequestContextMiddleware)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/check",
            headers={
                "X-Cred-Langfuse-Base-Url": "http://169.254.169.254",
                "X-Cred-Langfuse-Public-Key": "pk-x",
            },
        )
        assert resp.status_code == 200

    await asyncio.sleep(0.05)
    events = [e for e in audit_router.events if e.action == AuditAction.NETWORK_ACCESS_DENIED]
    assert len(events) == 1
    event = events[0]
    assert event.outcome == AuditOutcome.DENY
    assert event.reason == "blocked_cred_key"
    assert event.metadata["service"] == "langfuse"
    assert event.metadata["key"] == "base_url"
    assert event.http_path == "/check"


@pytest.mark.asyncio
async def test_legitimate_xcred_secret_emits_no_denial(audit_router):
    app = FastAPI()

    @app.get("/check")
    async def check() -> dict[str, int]:
        return {"ok": 1}

    app.add_middleware(RequestContextMiddleware)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get(
            "/check",
            headers={
                "X-Cred-Langfuse-Public-Key": "pk-x",
                "X-Cred-Langfuse-Secret-Key": "sk-x",
            },
        )
    await asyncio.sleep(0.05)
    assert not [e for e in audit_router.events if e.action == AuditAction.NETWORK_ACCESS_DENIED]
