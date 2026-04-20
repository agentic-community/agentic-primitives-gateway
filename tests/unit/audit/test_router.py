from __future__ import annotations

import asyncio

import pytest

from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.models import AuditEvent, AuditOutcome
from agentic_primitives_gateway.audit.router import AuditRouter


class _RecordingSink(AuditSink):
    def __init__(self, name: str = "rec") -> None:
        self.name = name
        self.events: list[AuditEvent] = []
        self.closed = False

    async def emit(self, event: AuditEvent) -> None:
        self.events.append(event)

    async def close(self) -> None:
        self.closed = True


class _SlowSink(AuditSink):
    def __init__(self, name: str, delay: float) -> None:
        self.name = name
        self._delay = delay
        self.emitted = 0

    async def emit(self, event: AuditEvent) -> None:
        await asyncio.sleep(self._delay)
        self.emitted += 1


class _FailingSink(AuditSink):
    def __init__(self, name: str = "fail") -> None:
        self.name = name
        self.attempts = 0

    async def emit(self, event: AuditEvent) -> None:
        self.attempts += 1
        raise RuntimeError("boom")


def _event(action: str = "auth.success") -> AuditEvent:
    return AuditEvent(action=action, outcome=AuditOutcome.SUCCESS)


@pytest.mark.asyncio
async def test_fan_out_delivers_to_every_sink():
    a = _RecordingSink("a")
    b = _RecordingSink("b")
    router = AuditRouter([a, b])
    await router.start()
    try:
        for _ in range(3):
            router.emit(_event())
        # Let workers drain.
        await asyncio.sleep(0.05)
    finally:
        await router.shutdown()

    assert len(a.events) == 3
    assert len(b.events) == 3
    assert a.closed and b.closed


@pytest.mark.asyncio
async def test_emit_before_start_is_dropped():
    a = _RecordingSink()
    router = AuditRouter([a])
    router.emit(_event())  # not started yet
    await router.start()
    try:
        router.emit(_event())
        await asyncio.sleep(0.02)
    finally:
        await router.shutdown()
    assert len(a.events) == 1


@pytest.mark.asyncio
async def test_failing_sink_does_not_block_others():
    good = _RecordingSink("good")
    bad = _FailingSink("bad")
    router = AuditRouter([good, bad])
    await router.start()
    try:
        router.emit(_event())
        router.emit(_event())
        await asyncio.sleep(0.05)
    finally:
        await router.shutdown()

    assert len(good.events) == 2
    assert bad.attempts == 2


@pytest.mark.asyncio
async def test_slow_sink_isolated_by_timeout():
    slow = _SlowSink("slow", delay=1.0)
    fast = _RecordingSink("fast")
    router = AuditRouter([slow, fast], sink_timeout_seconds=0.05)
    await router.start()
    try:
        router.emit(_event())
        await asyncio.sleep(0.2)
    finally:
        await router.shutdown(timeout=0.5)

    # Fast sink received it immediately; slow sink timed out (asyncio.wait_for
    # cancels the coroutine before ``emitted`` is incremented).
    assert len(fast.events) == 1
    assert slow.emitted == 0


@pytest.mark.asyncio
async def test_queue_full_drops_without_raising():
    # queue_size=1 so the second emit with a slow worker should drop.
    slow = _SlowSink("slow", delay=0.2)
    router = AuditRouter([slow], queue_size=1, sink_timeout_seconds=1.0)
    await router.start()
    try:
        router.emit(_event("a"))
        router.emit(_event("b"))  # possibly dropped depending on scheduling
        router.emit(_event("c"))  # definitely dropped — queue full
        await asyncio.sleep(0.5)
    finally:
        await router.shutdown(timeout=1.0)

    # At least one event delivered; others dropped silently (no exception).
    assert slow.emitted >= 1


@pytest.mark.asyncio
async def test_shutdown_drains_pending_events():
    sink = _RecordingSink()
    router = AuditRouter([sink])
    await router.start()
    for _ in range(5):
        router.emit(_event())
    # Shut down without sleeping first — the sentinel drain path should
    # still deliver all queued events.
    await router.shutdown(timeout=2.0)
    assert len(sink.events) == 5
    assert sink.closed


def test_duplicate_names_rejected():
    a = _RecordingSink("dup")
    b = _RecordingSink("dup")
    with pytest.raises(ValueError):
        AuditRouter([a, b])


def test_empty_sinks_rejected():
    with pytest.raises(ValueError):
        AuditRouter([])
