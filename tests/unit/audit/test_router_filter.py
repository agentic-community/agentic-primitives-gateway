"""Router-level filter gate for ``AuditRouter.emit()``.

The filter is the operator's escape hatch for the "emit-everything, drop-
noise" strategy.  These tests lock in the three matching modes (exact
action, category prefix, sample rate) and verify the dropped-events
counter increments.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agentic_primitives_gateway import metrics
from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.models import AuditEvent, AuditOutcome
from agentic_primitives_gateway.audit.router import AuditRouter


class _CollectorSink(AuditSink):
    def __init__(self) -> None:
        self.name = "collector"
        self.events: list[AuditEvent] = []

    async def emit(self, event: AuditEvent) -> None:
        self.events.append(event)


def _make_event(action: str) -> AuditEvent:
    return AuditEvent(action=action, outcome=AuditOutcome.SUCCESS)


def _dropped_count(reason: str) -> float:
    sample = metrics.AUDIT_EVENTS_DROPPED.labels(sink="__router__", reason=reason)
    return sample._value.get()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_exclude_actions_drops_exact_match():
    sink = _CollectorSink()
    router = AuditRouter([sink], exclude_actions=("provider.call",))
    baseline = _dropped_count("filtered")
    await router.start()
    try:
        router.emit(_make_event("provider.call"))
        router.emit(_make_event("agent.create"))
        await asyncio.sleep(0.02)
    finally:
        await router.shutdown(timeout=1.0)

    assert [e.action for e in sink.events] == ["agent.create"]
    assert _dropped_count("filtered") == baseline + 1


@pytest.mark.asyncio
async def test_exclude_action_categories_drops_whole_family():
    sink = _CollectorSink()
    router = AuditRouter([sink], exclude_action_categories=("memory",))
    await router.start()
    try:
        router.emit(_make_event("memory.record.write"))
        router.emit(_make_event("memory.event.append"))
        router.emit(_make_event("agent.create"))
        await asyncio.sleep(0.02)
    finally:
        await router.shutdown(timeout=1.0)

    assert [e.action for e in sink.events] == ["agent.create"]


@pytest.mark.asyncio
async def test_sample_rate_zero_drops_every_event(monkeypatch: Any):
    sink = _CollectorSink()
    router = AuditRouter([sink], sample_rates={"provider.call": 0.0})
    await router.start()
    try:
        for _ in range(5):
            router.emit(_make_event("provider.call"))
        router.emit(_make_event("agent.create"))
        await asyncio.sleep(0.02)
    finally:
        await router.shutdown(timeout=1.0)

    # 0.0 means keep none — only the non-filtered action should land.
    assert [e.action for e in sink.events] == ["agent.create"]


@pytest.mark.asyncio
async def test_sample_rate_one_keeps_every_event():
    sink = _CollectorSink()
    router = AuditRouter([sink], sample_rates={"provider.call": 1.0})
    await router.start()
    try:
        for _ in range(3):
            router.emit(_make_event("provider.call"))
        await asyncio.sleep(0.02)
    finally:
        await router.shutdown(timeout=1.0)

    assert len(sink.events) == 3


@pytest.mark.asyncio
async def test_filter_is_off_by_default():
    sink = _CollectorSink()
    router = AuditRouter([sink])
    await router.start()
    try:
        router.emit(_make_event("provider.call"))
        router.emit(_make_event("memory.record.write"))
        await asyncio.sleep(0.02)
    finally:
        await router.shutdown(timeout=1.0)

    assert {e.action for e in sink.events} == {"provider.call", "memory.record.write"}
