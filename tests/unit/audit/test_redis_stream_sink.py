from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_primitives_gateway.audit.models import AuditAction, AuditEvent, AuditOutcome


def _make_fake_redis():
    fake = MagicMock()
    fake.xadd = AsyncMock()
    fake.aclose = AsyncMock()
    return fake


@pytest.mark.asyncio
async def test_xadd_called_with_stream_and_maxlen():
    fake = _make_fake_redis()
    with patch("redis.asyncio.from_url", return_value=fake):
        from agentic_primitives_gateway.audit.sinks.redis_stream import RedisStreamAuditSink

        sink = RedisStreamAuditSink(stream="gateway:audit", maxlen=500)

    event = AuditEvent(action=AuditAction.POLICY_ALLOW, outcome=AuditOutcome.ALLOW)
    await sink.emit(event)

    fake.xadd.assert_awaited_once()
    kwargs = fake.xadd.call_args.kwargs
    assert kwargs["name"] == "gateway:audit"
    assert kwargs["maxlen"] == 500
    # approximate defaults to True — drift-friendly MAXLEN
    assert kwargs["approximate"] is True
    # Payload is a single JSON-serialized field.
    assert set(kwargs["fields"].keys()) == {"event"}
    assert '"action":"policy.allow"' in kwargs["fields"]["event"]


@pytest.mark.asyncio
async def test_close_disposes_redis_client():
    fake = _make_fake_redis()
    with patch("redis.asyncio.from_url", return_value=fake):
        from agentic_primitives_gateway.audit.sinks.redis_stream import RedisStreamAuditSink

        sink = RedisStreamAuditSink()

    await sink.close()
    fake.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_implements_audit_reader_protocol():
    """``RedisStreamAuditSink`` participates in the :class:`AuditReader` protocol."""
    fake = _make_fake_redis()
    with patch("redis.asyncio.from_url", return_value=fake):
        from agentic_primitives_gateway.audit.base import AuditReader
        from agentic_primitives_gateway.audit.sinks.redis_stream import RedisStreamAuditSink

        sink = RedisStreamAuditSink()

    assert isinstance(sink, AuditReader)
    desc = sink.describe()
    assert desc["backend"] == "redis_stream"
    assert desc["stream_name"] == "gateway:audit"


@pytest.mark.asyncio
async def test_count_returns_xlen():
    fake = _make_fake_redis()
    fake.xlen = AsyncMock(return_value=42)
    with patch("redis.asyncio.from_url", return_value=fake):
        from agentic_primitives_gateway.audit.sinks.redis_stream import RedisStreamAuditSink

        sink = RedisStreamAuditSink(stream="gateway:audit")

    assert await sink.count() == 42
    fake.xlen.assert_awaited_once_with("gateway:audit")


@pytest.mark.asyncio
async def test_count_returns_none_on_error():
    fake = _make_fake_redis()
    fake.xlen = AsyncMock(side_effect=RuntimeError("redis down"))
    with patch("redis.asyncio.from_url", return_value=fake):
        from agentic_primitives_gateway.audit.sinks.redis_stream import RedisStreamAuditSink

        sink = RedisStreamAuditSink()

    assert await sink.count() is None


@pytest.mark.asyncio
async def test_list_events_parses_and_skips_malformed():
    """Malformed stream entries (corrupted JSON) are silently dropped."""
    evt = AuditEvent(action=AuditAction.POLICY_ALLOW, outcome=AuditOutcome.ALLOW)
    fake = _make_fake_redis()
    fake.xrevrange = AsyncMock(
        return_value=[
            ("0-2", {"event": "not-json"}),
            ("0-1", {"event": evt.model_dump_json()}),
        ]
    )
    with patch("redis.asyncio.from_url", return_value=fake):
        from agentic_primitives_gateway.audit.sinks.redis_stream import RedisStreamAuditSink

        sink = RedisStreamAuditSink(stream="gateway:audit")

    events, next_cursor = await sink.list_events(start="-", end="+", count=10)
    assert len(events) == 1
    assert events[0].action == AuditAction.POLICY_ALLOW
    # Batch not full → exhausted → next is None.
    assert next_cursor is None


@pytest.mark.asyncio
async def test_list_events_returns_cursor_when_batch_full():
    evt = AuditEvent(action=AuditAction.AUTH_SUCCESS, outcome=AuditOutcome.SUCCESS)
    fake = _make_fake_redis()
    fake.xrevrange = AsyncMock(return_value=[(f"0-{i}", {"event": evt.model_dump_json()}) for i in range(1, 6)])
    with patch("redis.asyncio.from_url", return_value=fake):
        from agentic_primitives_gateway.audit.sinks.redis_stream import RedisStreamAuditSink

        sink = RedisStreamAuditSink()

    events, next_cursor = await sink.list_events(start="-", end="+", count=5)
    assert len(events) == 5
    # Cursor is the last entry's ID so the caller can continue backward.
    assert next_cursor == "0-5"
