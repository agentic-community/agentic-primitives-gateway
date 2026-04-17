"""Server tests for the audit admin routes + whoami endpoint."""

from __future__ import annotations

import json
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


class _FakeRedisSink:
    """Stand-in for RedisStreamAuditSink that exposes the same accessors."""

    def __init__(self, *, entries: list[tuple[str, dict[str, str]]] | None = None) -> None:
        self.name = "redis_stream"
        self._stream = "gateway:audit"
        self._maxlen = 100_000
        self.redis = AsyncMock()
        self.redis.xlen = AsyncMock(return_value=len(entries or []))
        self.redis.xrevrange = AsyncMock(return_value=list(reversed(entries or [])))

    @property
    def stream(self) -> str:
        return self._stream

    @property
    def maxlen(self) -> int:
        return self._maxlen

    async def emit(self, event: AuditEvent) -> None:  # pragma: no cover
        pass


def _event_entry(
    entry_id: str,
    *,
    action: str = "auth.success",
    outcome: AuditOutcome = AuditOutcome.SUCCESS,
    actor_id: str | None = "alice",
    correlation_id: str | None = "corr-1",
) -> tuple[str, dict[str, str]]:
    event = AuditEvent(
        action=action,
        outcome=outcome,
        actor_id=actor_id,
        correlation_id=correlation_id,
    )
    return entry_id, {"event": event.model_dump_json()}


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
    fake_sink = _FakeRedisSink(entries=[_event_entry(f"0-{i}") for i in range(1, 8)])
    with patch.object(audit_routes, "_find_stream_sink", return_value=fake_sink):
        app = _make_app(_admin_backend())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/audit/status")
            assert resp.status_code == 200
            body = resp.json()
            assert body["stream_sink_configured"] is True
            assert body["stream_name"] == "gateway:audit"
            assert body["length"] == 7
            assert body["maxlen"] == 100_000


# ── list events ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_events_returns_409_when_sink_not_configured():
    with patch.object(audit_routes, "_find_stream_sink", return_value=None):
        app = _make_app(_admin_backend())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/audit/events")
            assert resp.status_code == 409
            assert "not configured" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_list_events_returns_newest_first_and_parses_events():
    # Simulate xrevrange returning two entries newest-first.
    entries = [_event_entry("0-2", action="policy.allow"), _event_entry("0-1")]
    fake_sink = _FakeRedisSink()
    fake_sink.redis.xrevrange = AsyncMock(return_value=entries)

    with patch.object(audit_routes, "_find_stream_sink", return_value=fake_sink):
        app = _make_app(_admin_backend())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/audit/events?count=5")
            assert resp.status_code == 200
            body = resp.json()
            assert [e["action"] for e in body["events"]] == ["policy.allow", "auth.success"]
            assert body["scanned"] == 2
            # Batch under the cap → stream exhausted → next is None.
            assert body["next"] is None


@pytest.mark.asyncio
async def test_list_events_filters_by_action():
    entries = [
        _event_entry("0-3", action="policy.deny", outcome=AuditOutcome.DENY),
        _event_entry("0-2", action="auth.failure", outcome=AuditOutcome.FAILURE),
        _event_entry("0-1", action="policy.deny", outcome=AuditOutcome.DENY),
    ]
    fake_sink = _FakeRedisSink()
    fake_sink.redis.xrevrange = AsyncMock(return_value=entries)

    with patch.object(audit_routes, "_find_stream_sink", return_value=fake_sink):
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
        _event_entry("0-3", action="policy.deny", outcome=AuditOutcome.DENY),
        _event_entry("0-2", action="policy.allow", outcome=AuditOutcome.ALLOW),
        _event_entry("0-1", action="auth.failure", outcome=AuditOutcome.FAILURE),
    ]
    fake_sink = _FakeRedisSink()
    fake_sink.redis.xrevrange = AsyncMock(return_value=entries)

    with patch.object(audit_routes, "_find_stream_sink", return_value=fake_sink):
        app = _make_app(_admin_backend())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/audit/events?action_category=policy&outcome=deny")
            body = resp.json()
            assert len(body["events"]) == 1
            assert body["events"][0]["action"] == "policy.deny"


@pytest.mark.asyncio
async def test_list_events_pagination_cursor_returned_when_batch_full():
    # Batch size is count * 5 (default count=100 → batch=500).  Give
    # the fake exactly `batch` entries so it looks full and `next` is set.
    batch_size = 5 * 5  # count=5 → batch=25
    entries = [_event_entry(f"0-{i}") for i in range(batch_size, 0, -1)]
    fake_sink = _FakeRedisSink()
    fake_sink.redis.xrevrange = AsyncMock(return_value=entries)

    with patch.object(audit_routes, "_find_stream_sink", return_value=fake_sink):
        app = _make_app(_admin_backend())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/audit/events?count=5")
            body = resp.json()
            assert len(body["events"]) == 5
            assert body["next"] is not None


@pytest.mark.asyncio
async def test_list_events_skips_malformed_entries():
    entries = [
        ("0-2", {"event": "not-json-at-all"}),
        _event_entry("0-1"),
    ]
    fake_sink = _FakeRedisSink()
    fake_sink.redis.xrevrange = AsyncMock(return_value=entries)

    with patch.object(audit_routes, "_find_stream_sink", return_value=fake_sink):
        app = _make_app(_admin_backend())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/audit/events")
            assert resp.status_code == 200
            body = resp.json()
            # The malformed entry is silently skipped.
            assert len(body["events"]) == 1
            assert body["scanned"] == 2


# ── live stream ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_returns_409_when_sink_not_configured():
    with patch.object(audit_routes, "_find_stream_sink", return_value=None):
        app = _make_app(_admin_backend())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/audit/events/stream")
            assert resp.status_code == 409


@pytest.mark.asyncio
async def test_stream_generator_emits_matching_events():
    """Unit-test the generator directly so we avoid the infinite SSE loop.

    The endpoint is a long-running async generator that yields "keepalive"
    comment frames forever when no events are available — not easily tested
    end-to-end via an ASGI client.  Calling the generator directly lets us
    pull N events, then break out cleanly.
    """
    # Fresh entries batch on first xread; subsequent calls return empty.
    fake_sink = _FakeRedisSink()
    call_count = {"n": 0}

    async def _xread(streams: dict[str, Any], count: int, block: int) -> Any:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return [
                (
                    "gateway:audit",
                    [
                        _event_entry("0-1", action="policy.allow", outcome=AuditOutcome.ALLOW),
                        _event_entry("0-2", action="auth.success"),
                    ],
                )
            ]
        # Once all matching events have been emitted, flip the mock
        # disconnect flag so the generator exits cleanly.
        fake_request.is_disconnected = AsyncMock(return_value=True)
        return []

    fake_sink.redis.xread = _xread

    # Minimal request stub with an is_disconnected coroutine the endpoint
    # polls on each tick.  ASGITransport doesn't easily expose disconnect;
    # the generator path is the integration surface we care about.
    fake_request = type("FakeRequest", (), {})()
    fake_request.is_disconnected = AsyncMock(return_value=False)

    with patch.object(audit_routes, "_find_stream_sink", return_value=fake_sink):
        # Call the route function directly to obtain the StreamingResponse.
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
            # Cap collection at a few chunks to stay defensive.
            if len(chunks) >= 5:
                break

    combined = "".join(chunks)
    # Only the policy.allow event emits; auth.success gets filtered.
    assert "policy.allow" in combined
    assert "auth.success" not in combined


@pytest.mark.asyncio
async def test_stream_generator_emits_keepalive_on_idle():
    fake_sink = _FakeRedisSink()
    call_count = {"n": 0}

    async def _xread(streams: dict[str, Any], count: int, block: int) -> Any:
        call_count["n"] += 1
        if call_count["n"] >= 2:
            # After the first keepalive, signal disconnect so the loop exits.
            fake_request.is_disconnected = AsyncMock(return_value=True)
        return []

    fake_sink.redis.xread = _xread

    fake_request = type("FakeRequest", (), {})()
    fake_request.is_disconnected = AsyncMock(return_value=False)

    with patch.object(audit_routes, "_find_stream_sink", return_value=fake_sink):
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
    """Disconnecting clients should not leave the xread loop running."""
    fake_sink = _FakeRedisSink()
    fake_sink.redis.xread = AsyncMock(return_value=[])

    fake_request = type("FakeRequest", (), {})()
    fake_request.is_disconnected = AsyncMock(return_value=True)

    with patch.object(audit_routes, "_find_stream_sink", return_value=fake_sink):
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

    # Immediate disconnect → no frames emitted at all.
    assert chunks == []
    # xread should not have been called (we check disconnect first).
    fake_sink.redis.xread.assert_not_called()


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
    from agentic_primitives_gateway.routes.audit import _parse_entry

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
