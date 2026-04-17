"""MetricsProxy emits provider.call audit events + wraps async generators."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.emit import set_audit_router
from agentic_primitives_gateway.audit.models import AuditAction, AuditEvent, AuditOutcome
from agentic_primitives_gateway.audit.router import AuditRouter
from agentic_primitives_gateway.metrics import MetricsProxy


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


class _FakeProvider:
    async def do_work(self, x: int) -> int:
        return x * 2

    async def boom(self) -> None:
        raise ValueError("nope")

    async def stream(self) -> AsyncIterator[int]:
        yield 1
        yield 2
        yield 3

    async def failing_stream(self) -> AsyncIterator[int]:
        yield 1
        raise RuntimeError("midway")

    # Non-public and non-async paths must pass through unchanged.
    def _private(self) -> str:
        return "internal"

    async def _private_async(self) -> str:
        return "internal-async"

    def sync_attr(self) -> str:
        return "sync"


@pytest.mark.asyncio
async def test_async_call_success_emits_provider_call(audit_router):
    proxy = MetricsProxy(_FakeProvider(), primitive="memory", provider_name="in_memory")
    result = await proxy.do_work(5)
    assert result == 10

    await asyncio.sleep(0.02)
    events = [e for e in audit_router.events if e.action == AuditAction.PROVIDER_CALL]
    assert len(events) == 1
    event = events[0]
    assert event.outcome == AuditOutcome.SUCCESS
    assert event.resource_id == "memory/in_memory"
    assert event.metadata["primitive"] == "memory"
    assert event.metadata["provider"] == "in_memory"
    assert event.metadata["method"] == "do_work"
    assert event.duration_ms is not None and event.duration_ms >= 0


@pytest.mark.asyncio
async def test_async_call_failure_emits_provider_call_with_error(audit_router):
    proxy = MetricsProxy(_FakeProvider(), primitive="memory", provider_name="in_memory")
    with pytest.raises(ValueError):
        await proxy.boom()

    await asyncio.sleep(0.02)
    events = [e for e in audit_router.events if e.action == AuditAction.PROVIDER_CALL]
    assert len(events) == 1
    assert events[0].outcome == AuditOutcome.FAILURE
    assert events[0].metadata["error_type"] == "ValueError"


@pytest.mark.asyncio
async def test_async_generator_wrapped_and_emits(audit_router):
    proxy = MetricsProxy(_FakeProvider(), primitive="llm", provider_name="test")
    collected: list[Any] = []
    async for item in proxy.stream():
        collected.append(item)
    assert collected == [1, 2, 3]

    await asyncio.sleep(0.02)
    events = [
        e for e in audit_router.events if e.action == AuditAction.PROVIDER_CALL and e.metadata.get("method") == "stream"
    ]
    assert len(events) == 1
    assert events[0].outcome == AuditOutcome.SUCCESS


@pytest.mark.asyncio
async def test_async_generator_failure_still_recorded(audit_router):
    proxy = MetricsProxy(_FakeProvider(), primitive="llm", provider_name="test")
    with pytest.raises(RuntimeError):
        async for _ in proxy.failing_stream():
            pass

    await asyncio.sleep(0.02)
    events = [
        e
        for e in audit_router.events
        if e.action == AuditAction.PROVIDER_CALL and e.metadata.get("method") == "failing_stream"
    ]
    assert len(events) == 1
    assert events[0].outcome == AuditOutcome.FAILURE
    assert events[0].metadata["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_private_methods_are_not_wrapped(audit_router):
    proxy = MetricsProxy(_FakeProvider(), primitive="x", provider_name="y")
    # _private is sync; also starts with underscore → passes through.
    assert proxy._private() == "internal"
    # _private_async starts with underscore → passes through unwrapped.
    assert await proxy._private_async() == "internal-async"
    # sync_attr is sync non-private → passes through unwrapped.
    assert proxy.sync_attr() == "sync"

    await asyncio.sleep(0.02)
    events = [
        e
        for e in audit_router.events
        if e.action == AuditAction.PROVIDER_CALL
        and e.metadata.get("method") in {"_private", "_private_async", "sync_attr"}
    ]
    assert not events
