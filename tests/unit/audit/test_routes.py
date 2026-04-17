"""Server tests for the audit admin routes + whoami endpoint."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentic_primitives_gateway.audit.emit import set_audit_router
from agentic_primitives_gateway.audit.models import AuditEvent, AuditOutcome
from agentic_primitives_gateway.audit.router import AuditRouter
from agentic_primitives_gateway.audit.sinks.noop import NoopAuditSink
from agentic_primitives_gateway.auth.middleware import AuthenticationMiddleware
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.middleware import RequestContextMiddleware
from agentic_primitives_gateway.routes import audit as audit_routes
from agentic_primitives_gateway.routes.health import router as health_router


def _make_app(backend: AsyncMock) -> FastAPI:
    """Build a minimal app with auth middleware + audit router installed."""
    app = FastAPI()
    app.include_router(audit_routes.router)
    app.include_router(health_router)
    app.state.auth_backend = backend
    app.add_middleware(AuthenticationMiddleware)
    app.add_middleware(RequestContextMiddleware)
    return app


def _admin_backend() -> AsyncMock:
    backend = AsyncMock()
    backend.authenticate = AsyncMock(
        return_value=AuthenticatedPrincipal(id="alice", type="user", scopes=frozenset({"admin"}))
    )
    return backend


def _non_admin_backend() -> AsyncMock:
    backend = AsyncMock()
    backend.authenticate = AsyncMock(return_value=AuthenticatedPrincipal(id="bob", type="user"))
    return backend


class _FakeReader:
    """In-memory :class:`AuditReader` for route tests.

    Mirrors the protocol shape without any Redis dependency — lets us
    test the route layer in isolation and validates that the route code
    is backend-agnostic.
    """

    def __init__(
        self,
        *,
        entries: list[tuple[str, AuditEvent]] | None = None,
        describe_extra: dict[str, Any] | None = None,
    ) -> None:
        self.name = "fake_reader"
        # entries[i] = (cursor_id, event), stored oldest-first.
        self._entries: list[tuple[str, AuditEvent]] = list(entries or [])
        self._describe = {
            "backend": "fake_reader",
            "stream_name": "test:stream",
            "maxlen": 100_000,
            **(describe_extra or {}),
        }
        # Hooks the tests override to drive ``tail()`` behavior.
        self.tail_script: list[AuditEvent | None] | None = None

    def describe(self) -> dict[str, Any]:
        return dict(self._describe)

    async def count(self) -> int | None:
        return len(self._entries)

    async def list_events(
        self,
        *,
        start: str,
        end: str,
        count: int,
    ) -> tuple[list[AuditEvent], str | None]:
        # Oldest→newest in ``self._entries``; "newest-first" view reverses it.
        reversed_entries = list(reversed(self._entries))
        sliced = reversed_entries[:count]
        events = [evt for _, evt in sliced]
        last_id = sliced[-1][0] if sliced else None
        next_cursor = last_id if len(sliced) == count else None
        return events, next_cursor

    async def tail(self) -> AsyncIterator[AuditEvent | None]:
        # Default: empty forever (tests override via ``tail_script``).
        if self.tail_script is None:
            while True:
                yield None
        for item in self.tail_script:
            yield item
        # After script exhausts, yield keepalives so the consumer can
        # exit cleanly via request.is_disconnected.
        while True:
            yield None

    async def emit(self, event: AuditEvent) -> None:  # pragma: no cover
        pass


def _event_entry(
    entry_id: str,
    *,
    action: str = "auth.success",
    outcome: AuditOutcome = AuditOutcome.SUCCESS,
    actor_id: str | None = "alice",
    correlation_id: str | None = "corr-1",
) -> tuple[str, AuditEvent]:
    """Build an ``(id, AuditEvent)`` pair for ``_FakeReader``."""
    event = AuditEvent(
        action=action,
        outcome=outcome,
        actor_id=actor_id,
        correlation_id=correlation_id,
    )
    return entry_id, event


# ── whoami ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_whoami_returns_admin_principal():
    app = _make_app(_admin_backend())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/auth/whoami")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "alice"
        assert body["is_admin"] is True
        assert body["scopes"] == ["admin"]


@pytest.mark.asyncio
async def test_whoami_returns_non_admin_principal():
    app = _make_app(_non_admin_backend())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/auth/whoami")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "bob"
        assert body["is_admin"] is False


@pytest.mark.asyncio
async def test_whoami_rejects_missing_credentials():
    backend = AsyncMock()
    backend.authenticate = AsyncMock(return_value=None)
    app = _make_app(backend)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/auth/whoami")
        assert resp.status_code == 401


# ── admin gating ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_status_requires_admin():
    app = _make_app(_non_admin_backend())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/audit/status")
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_audit_events_requires_admin():
    app = _make_app(_non_admin_backend())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/audit/events")
        assert resp.status_code == 403


# ── status ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_status_reports_unconfigured_when_no_sink():
    # Install a router whose only sink is a NoopAuditSink (not a stream sink).
    noop_router = AuditRouter([NoopAuditSink()])
    await noop_router.start()
    set_audit_router(noop_router)
    try:
        app = _make_app(_admin_backend())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/audit/status")
            assert resp.status_code == 200
            body = resp.json()
            assert body["stream_sink_configured"] is False
            assert body["stream_name"] is None
            assert body["length"] is None
    finally:
        await noop_router.shutdown(timeout=1.0)
        set_audit_router(None)


@pytest.mark.asyncio
async def test_audit_status_reports_configured_stream():
    # Entries oldest-first; length reported by ``reader.count()``.
    fake = _FakeReader(entries=[_event_entry(f"0-{i}") for i in range(1, 8)])
    with patch.object(audit_routes, "_find_reader", return_value=fake):
        app = _make_app(_admin_backend())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/audit/status")
            assert resp.status_code == 200
            body = resp.json()
            assert body["stream_sink_configured"] is True
            assert body["stream_name"] == "test:stream"
            assert body["length"] == 7
            assert body["maxlen"] == 100_000
            assert body["backend"] == "fake_reader"


@pytest.mark.asyncio
async def test_audit_status_passes_through_backend_metadata():
    """Extra ``describe()`` fields surface as top-level keys on /status."""
    fake = _FakeReader(describe_extra={"table_name": "audit_events", "retention_days": 90})
    with patch.object(audit_routes, "_find_reader", return_value=fake):
        app = _make_app(_admin_backend())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            body = (await client.get("/api/v1/audit/status")).json()
            assert body["table_name"] == "audit_events"
            assert body["retention_days"] == 90


# ── list events ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_events_returns_409_when_reader_not_configured():
    with patch.object(audit_routes, "_find_reader", return_value=None):
        app = _make_app(_admin_backend())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/audit/events")
            assert resp.status_code == 409
            assert "No audit reader" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_list_events_returns_newest_first_and_parses_events():
    # Oldest-first input; reader reverses to newest-first.
    entries = [_event_entry("0-1"), _event_entry("0-2", action="policy.allow")]
    fake = _FakeReader(entries=entries)
    with patch.object(audit_routes, "_find_reader", return_value=fake):
        app = _make_app(_admin_backend())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/audit/events?count=5")
            assert resp.status_code == 200
            body = resp.json()
            assert [e["action"] for e in body["events"]] == ["policy.allow", "auth.success"]
            assert body["scanned"] == 2
            # Batch under the cap → exhausted → next is None.
            assert body["next"] is None


@pytest.mark.asyncio
async def test_list_events_filters_by_action():
    entries = [
        _event_entry("0-1", action="policy.deny", outcome=AuditOutcome.DENY),
        _event_entry("0-2", action="auth.failure", outcome=AuditOutcome.FAILURE),
        _event_entry("0-3", action="policy.deny", outcome=AuditOutcome.DENY),
    ]
    fake = _FakeReader(entries=entries)
    with patch.object(audit_routes, "_find_reader", return_value=fake):
        app = _make_app(_admin_backend())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/audit/events?action=policy.deny")
            assert resp.status_code == 200
            body = resp.json()
            assert len(body["events"]) == 2
            assert all(e["action"] == "policy.deny" for e in body["events"])


@pytest.mark.asyncio
async def test_list_events_filters_by_category_and_outcome():
    entries = [
        _event_entry("0-1", action="policy.deny", outcome=AuditOutcome.DENY),
        _event_entry("0-2", action="policy.allow", outcome=AuditOutcome.ALLOW),
        _event_entry("0-3", action="auth.failure", outcome=AuditOutcome.FAILURE),
    ]
    fake = _FakeReader(entries=entries)
    with patch.object(audit_routes, "_find_reader", return_value=fake):
        app = _make_app(_admin_backend())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/audit/events?action_category=policy&outcome=deny")
            body = resp.json()
            assert len(body["events"]) == 1
            assert body["events"][0]["action"] == "policy.deny"


@pytest.mark.asyncio
async def test_list_events_pagination_cursor_returned_when_batch_full():
    # Over-read batch = count * 5; give the fake exactly that many
    # entries so the reader reports a non-None next cursor.
    batch_size = 5 * 5
    entries = [_event_entry(f"0-{i}") for i in range(1, batch_size + 1)]
    fake = _FakeReader(entries=entries)
    with patch.object(audit_routes, "_find_reader", return_value=fake):
        app = _make_app(_admin_backend())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/audit/events?count=5")
            body = resp.json()
            assert len(body["events"]) == 5
            assert body["next"] is not None


# ── live stream ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_returns_409_when_reader_not_configured():
    with patch.object(audit_routes, "_find_reader", return_value=None):
        app = _make_app(_admin_backend())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/audit/events/stream")
            assert resp.status_code == 409


@pytest.mark.asyncio
async def test_stream_generator_emits_matching_events():
    """Verify the route consumes ``reader.tail()`` and applies filters.

    Driven by a scripted tail — the reader yields one matching and one
    non-matching event, then the fake request signals disconnect.
    """
    allow_id, allow_evt = _event_entry("0-1", action="policy.allow", outcome=AuditOutcome.ALLOW)
    _success_id, success_evt = _event_entry("0-2", action="auth.success")
    fake = _FakeReader()
    fake.tail_script = [allow_evt, success_evt]

    # After the route pulls both scripted events the tail falls through
    # to yielding ``None`` keepalives; the disconnect check exits the loop.
    fake_request = type("FakeRequest", (), {})()
    call_count = {"n": 0}

    async def _is_disconnected() -> bool:
        call_count["n"] += 1
        # Allow a few iterations so both events + the keepalive fire,
        # then signal disconnect.
        return call_count["n"] > 5

    fake_request.is_disconnected = _is_disconnected

    with patch.object(audit_routes, "_find_reader", return_value=fake):
        response = await audit_routes.stream_audit_events(
            request=fake_request,
            action="policy.allow",
            action_category=None,
            outcome=None,
            actor_id=None,
            resource_type=None,
            resource_id=None,
            correlation_id=None,
        )
        chunks: list[str] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk if isinstance(chunk, str) else chunk.decode())
            if len(chunks) >= 5:
                break

    combined = "".join(chunks)
    assert "policy.allow" in combined
    assert "auth.success" not in combined
    # allow_id is unused for this assertion but kept to document the fixture shape.
    assert allow_id == "0-1"


@pytest.mark.asyncio
async def test_stream_generator_emits_keepalive_on_idle():
    """When the reader yields ``None``, the route emits an SSE keepalive."""
    fake = _FakeReader()
    fake.tail_script = []  # default behavior: yield None forever

    fake_request = type("FakeRequest", (), {})()
    call_count = {"n": 0}

    async def _is_disconnected() -> bool:
        call_count["n"] += 1
        return call_count["n"] > 3

    fake_request.is_disconnected = _is_disconnected

    with patch.object(audit_routes, "_find_reader", return_value=fake):
        response = await audit_routes.stream_audit_events(
            request=fake_request,
            action=None,
            action_category=None,
            outcome=None,
            actor_id=None,
            resource_type=None,
            resource_id=None,
            correlation_id=None,
        )
        chunks: list[str] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk if isinstance(chunk, str) else chunk.decode())
            if ": keepalive" in "".join(chunks):
                break

    assert ": keepalive" in "".join(chunks)


@pytest.mark.asyncio
async def test_stream_generator_stops_on_client_disconnect():
    """The route checks disconnect inside the tail loop."""
    fake = _FakeReader()

    fake_request = type("FakeRequest", (), {})()
    fake_request.is_disconnected = AsyncMock(return_value=True)

    with patch.object(audit_routes, "_find_reader", return_value=fake):
        response = await audit_routes.stream_audit_events(
            request=fake_request,
            action=None,
            action_category=None,
            outcome=None,
            actor_id=None,
            resource_type=None,
            resource_id=None,
            correlation_id=None,
        )
        chunks = [chunk async for chunk in response.body_iterator]

    # First keepalive from tail() hits is_disconnected → immediate exit.
    assert chunks == []


# ── helper parity ──────────────────────────────────────────────────────


def test_match_event_action_category_prefix():
    from agentic_primitives_gateway.routes.audit import _match_event

    evt = AuditEvent(action="policy.deny", outcome=AuditOutcome.DENY)
    assert _match_event(
        evt,
        action=None,
        action_category="policy",
        outcome=None,
        actor_id=None,
        resource_type=None,
        resource_id=None,
        correlation_id=None,
    )
    assert not _match_event(
        evt,
        action=None,
        action_category="auth",
        outcome=None,
        actor_id=None,
        resource_type=None,
        resource_id=None,
        correlation_id=None,
    )


def test_match_event_honors_each_filter_field():
    from agentic_primitives_gateway.routes.audit import _match_event

    evt = AuditEvent(
        action="policy.allow",
        outcome=AuditOutcome.ALLOW,
        actor_id="alice",
        correlation_id="trace-1",
    )
    # Mismatch on actor_id.
    assert not _match_event(
        evt,
        action=None,
        action_category=None,
        outcome=None,
        actor_id="bob",
        resource_type=None,
        resource_id=None,
        correlation_id=None,
    )
    # Mismatch on correlation_id.
    assert not _match_event(
        evt,
        action=None,
        action_category=None,
        outcome=None,
        actor_id=None,
        resource_type=None,
        resource_id=None,
        correlation_id="other",
    )
    # All matching filters pass.
    assert _match_event(
        evt,
        action="policy.allow",
        action_category="policy",
        outcome=AuditOutcome.ALLOW,
        actor_id="alice",
        resource_type=None,
        resource_id=None,
        correlation_id="trace-1",
    )


def test_parse_entry_returns_none_for_missing_event_field():
    """The Redis sink's parser is the authoritative malformed-entry handler.

    Moved here from the route layer when the ``AuditReader`` protocol
    absorbed backend-specific parsing — the route layer only sees
    already-parsed ``AuditEvent`` instances.
    """
    from agentic_primitives_gateway.audit.sinks.redis_stream import _parse_entry

    assert _parse_entry("0-1", {}) is None
    assert _parse_entry("0-2", {"event": "not json"}) is None

    evt = AuditEvent(action="auth.success", outcome=AuditOutcome.SUCCESS)
    parsed = _parse_entry("0-3", {"event": evt.model_dump_json()})
    assert parsed is not None
    assert parsed.action == "auth.success"


def test_json_serialization_stable_across_routes():
    """The shape emitted on the stream endpoint should match the list endpoint."""
    evt = AuditEvent(
        action="tool.call",
        outcome=AuditOutcome.SUCCESS,
        actor_id="alice",
        metadata={"tool_name": "do_thing"},
    )
    list_shape = json.dumps(evt.model_dump(mode="json"), default=str)
    # model_dump_json() and json.dumps(model_dump(mode="json")) should produce
    # semantically identical payloads.
    parsed_a = json.loads(evt.model_dump_json())
    parsed_b = json.loads(list_shape)
    assert parsed_a == parsed_b
