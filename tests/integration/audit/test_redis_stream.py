"""Audit Redis stream sink — round-trip through a real Redis.

Reuses the same env var convention as other Redis tests: set ``REDIS_URL``
(e.g. ``redis://localhost:6379/0``) to enable.  Skipped when Redis is
unreachable so CI stays green when the service isn't running locally.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

from agentic_primitives_gateway.audit.models import AuditAction, AuditEvent, AuditOutcome
from agentic_primitives_gateway.audit.router import AuditRouter

pytestmark = pytest.mark.integration

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


async def _redis_available() -> bool:
    try:
        import redis.asyncio as redis_asyncio

        r = redis_asyncio.from_url(REDIS_URL, decode_responses=True)
        await r.ping()
        await r.aclose()
        return True
    except Exception:
        return False


@pytest.fixture(autouse=True)
async def _skip_without_redis():
    if not await _redis_available():
        pytest.skip(f"Redis not reachable at {REDIS_URL}")


@pytest.fixture
async def audit_stream():
    stream = f"gateway:audit:test:{uuid.uuid4().hex[:8]}"
    yield stream
    # Cleanup
    import redis.asyncio as redis_asyncio

    r = redis_asyncio.from_url(REDIS_URL, decode_responses=True)
    try:
        await r.delete(stream)
    finally:
        await r.aclose()


@pytest.mark.asyncio
async def test_router_writes_events_to_real_redis_stream(audit_stream):
    import asyncio

    import redis.asyncio as redis_asyncio

    from agentic_primitives_gateway.audit.sinks.redis_stream import RedisStreamAuditSink

    sink = RedisStreamAuditSink(redis_url=REDIS_URL, stream=audit_stream, maxlen=1000)
    router = AuditRouter([sink])
    await router.start()
    try:
        router.emit(AuditEvent(action=AuditAction.AUTH_SUCCESS, outcome=AuditOutcome.SUCCESS, actor_id="alice"))
        router.emit(AuditEvent(action=AuditAction.POLICY_DENY, outcome=AuditOutcome.DENY, reason="cedar_deny"))
        # Give the worker a moment to drain.
        await asyncio.sleep(0.2)
    finally:
        await router.shutdown(timeout=2.0)

    # Verify the stream contains both events, in order, with the event payload intact.
    r = redis_asyncio.from_url(REDIS_URL, decode_responses=True)
    try:
        entries = await r.xrange(audit_stream, "-", "+")
    finally:
        await r.aclose()

    assert len(entries) == 2
    payloads = [json.loads(fields["event"]) for _id, fields in entries]
    assert [p["action"] for p in payloads] == ["auth.success", "policy.deny"]
    assert payloads[0]["actor_id"] == "alice"
    assert payloads[1]["reason"] == "cedar_deny"


@pytest.mark.asyncio
async def test_maxlen_trims_the_stream(audit_stream):
    import asyncio

    import redis.asyncio as redis_asyncio

    from agentic_primitives_gateway.audit.sinks.redis_stream import RedisStreamAuditSink

    # Use a small MAXLEN and non-approximate trimming for a predictable test.
    sink = RedisStreamAuditSink(
        redis_url=REDIS_URL,
        stream=audit_stream,
        maxlen=5,
        approximate=False,
    )
    router = AuditRouter([sink])
    await router.start()
    try:
        for _ in range(20):
            router.emit(AuditEvent(action="x", outcome=AuditOutcome.SUCCESS))
        await asyncio.sleep(0.3)
    finally:
        await router.shutdown(timeout=2.0)

    r = redis_asyncio.from_url(REDIS_URL, decode_responses=True)
    try:
        length = await r.xlen(audit_stream)
    finally:
        await r.aclose()

    # Exact trim: MAXLEN=5 should keep 5 entries.
    assert length == 5
