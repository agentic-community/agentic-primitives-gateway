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
