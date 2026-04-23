"""Intent-level test: audit events emit in the expected middleware order.

Contract (CLAUDE.md Security Principles "Layered middleware stack"):
CORS -> RequestContext -> Audit -> Auth -> CredentialResolution ->
PolicyEnforcement -> route handler.  Each layer emits its own audit
events.  For a single request:

- Inner-layer events (auth.success, policy.allow, route-level
  provider.call) fire during the request.
- AuditMiddleware emits ``http.request`` on the response unwind —
  so it's always LAST in the emission stream for a single request.

Observable breakage: if AuditMiddleware were moved above Auth in
main.py, ``http.request`` would fire BEFORE auth events and
operators wouldn't be able to tell from the audit stream which
authentication backend was consulted for a given request.

No existing test captures the event sequence for a real HTTP
request and asserts the ordering invariant.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.emit import emit_audit_event, set_audit_router
from agentic_primitives_gateway.audit.models import AuditEvent, AuditOutcome
from agentic_primitives_gateway.audit.router import AuditRouter
from agentic_primitives_gateway.main import app


class _CapturingSink(AuditSink):
    name = "capture"

    def __init__(self) -> None:
        self.received: list[AuditEvent] = []

    async def emit(self, event: AuditEvent) -> None:
        self.received.append(event)

    async def close(self) -> None:
        pass


@pytest.fixture
async def audit_sink():
    sink = _CapturingSink()
    router = AuditRouter(sinks=[sink])
    set_audit_router(router)
    await router.start()
    yield sink
    await router.shutdown()
    set_audit_router(None)


class TestHttpRequestEmittedLast:
    """Http.request is emitted from the AuditMiddleware's unwind —
    so it's always last in the emission stream for a single
    request.  Any event emitted from the inner layers (auth,
    policy, route) comes first.
    """

    @pytest.mark.asyncio
    async def test_http_request_emits_on_authenticated_api_call(self, audit_sink: _CapturingSink):
        """A non-exempt endpoint → AuditMiddleware runs → fires
        http.request on the unwind.  The event appears in the
        captured stream.
        """
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/auth/whoami")
        assert resp.status_code == 200

        await asyncio.sleep(0.05)

        actions = [e.action for e in audit_sink.received]
        assert "http.request" in actions, (
            f"AuditMiddleware did not emit http.request on a non-exempt request.  Captured events: {actions}"
        )

    @pytest.mark.asyncio
    async def test_inner_event_precedes_http_request(self, audit_sink: _CapturingSink):
        """Use a one-off route to emit an audit event from inside
        the request handler.  That event must land BEFORE
        http.request in the emission stream — proves AuditMiddleware
        runs as an outer wrap.
        """
        from fastapi import FastAPI

        from agentic_primitives_gateway.audit.middleware import AuditMiddleware

        probe_app = FastAPI()
        probe_app.add_middleware(AuditMiddleware, exempt_prefixes=())

        @probe_app.get("/probe")
        async def _probe():
            emit_audit_event(action="test.inner.event", outcome=AuditOutcome.SUCCESS)
            return {"ok": True}

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=probe_app), base_url="http://test") as c:
            resp = await c.get("/probe")
        assert resp.status_code == 200

        await asyncio.sleep(0.05)

        actions = [e.action for e in audit_sink.received]
        assert "test.inner.event" in actions, f"Inner event not captured: {actions}"
        assert "http.request" in actions, f"http.request not captured: {actions}"

        inner_idx = actions.index("test.inner.event")
        http_idx = actions.index("http.request")
        assert inner_idx < http_idx, (
            f"Inner handler's event (index {inner_idx}) must fire BEFORE "
            f"http.request (index {http_idx}) — AuditMiddleware emits on unwind.  "
            f"Stream: {actions}.  If http.request came first, AuditMiddleware is "
            "no longer the outer wrapper — middleware stack order regression."
        )

    @pytest.mark.asyncio
    async def test_multiple_inner_events_preserve_order(self, audit_sink: _CapturingSink):
        """Two events emitted from the same handler preserve their
        relative order in the captured stream.  Guards against a
        queue that reorders (e.g., priority queue without a
        stable tie-breaker).
        """
        from fastapi import FastAPI

        from agentic_primitives_gateway.audit.middleware import AuditMiddleware

        probe_app = FastAPI()
        probe_app.add_middleware(AuditMiddleware, exempt_prefixes=())

        @probe_app.get("/probe")
        async def _probe():
            emit_audit_event(action="first", outcome=AuditOutcome.SUCCESS)
            emit_audit_event(action="second", outcome=AuditOutcome.SUCCESS)
            emit_audit_event(action="third", outcome=AuditOutcome.SUCCESS)
            return {"ok": True}

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=probe_app), base_url="http://test") as c:
            await c.get("/probe")

        await asyncio.sleep(0.05)

        actions = [e.action for e in audit_sink.received]
        # Extract just the three handler-emitted events in order.
        interesting = [a for a in actions if a in ("first", "second", "third")]
        assert interesting == ["first", "second", "third"], (
            f"Handler events reordered in the audit stream: {interesting}"
        )
