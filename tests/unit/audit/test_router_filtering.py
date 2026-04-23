"""Intent-level test: AuditRouter filter actually drops events before fan-out.

Contract (CLAUDE.md "Router-level audit filter"): three knobs drop
events before they reach any sink:

- ``exclude_actions`` — exact action name match.
- ``exclude_action_categories`` — first segment before the first ``.``.
- ``sample_rates`` — per-action fractional keep rate (0.5 keeps
  ~50%).

Dropped events increment ``gateway_audit_events_dropped_total``
with ``sink="__router__"`` / ``reason="filtered"``; they never land
on any sink.

No existing test wires up a realistic AuditRouter with filters and
verifies:
- excluded actions produce zero sink emissions
- excluded categories drop ``<cat>.*`` but not other categories
- sample_rates actually approximate the configured rate across
  many events (not exact — sampling is random — but bounded)
- non-matched events pass through untouched

A regression where the filter short-circuited early or miscomputed
the category prefix would still pass existing tests because they
only verify the happy-path emission.
"""

from __future__ import annotations

import random

import pytest

from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.models import AuditEvent, AuditOutcome
from agentic_primitives_gateway.audit.router import AuditRouter


class _CapturingSink(AuditSink):
    """Sink that records every event it receives for later assertion."""

    name = "capture"

    def __init__(self) -> None:
        self.received: list[AuditEvent] = []

    async def emit(self, event: AuditEvent) -> None:
        self.received.append(event)

    async def close(self) -> None:
        pass


def _ev(action: str) -> AuditEvent:
    return AuditEvent(action=action, outcome=AuditOutcome.SUCCESS)


async def _drain(router: AuditRouter, sink: _CapturingSink) -> None:
    """Wait until the sink queue is fully processed."""
    queue = router._queues[sink.name]
    await queue.join()


class TestExcludeActions:
    @pytest.mark.asyncio
    async def test_excluded_action_never_reaches_sink(self):
        sink = _CapturingSink()
        router = AuditRouter(
            sinks=[sink],
            exclude_actions=("provider.call", "tool.call"),
        )
        await router.start()
        try:
            router.emit(_ev("provider.call"))
            router.emit(_ev("tool.call"))
            router.emit(_ev("agent.run.start"))  # not excluded
            await _drain(router, sink)

            actions = [e.action for e in sink.received]
            assert "provider.call" not in actions
            assert "tool.call" not in actions
            assert "agent.run.start" in actions
        finally:
            await router.shutdown()


class TestExcludeCategories:
    @pytest.mark.asyncio
    async def test_category_prefix_drops_all_members(self):
        sink = _CapturingSink()
        router = AuditRouter(
            sinks=[sink],
            exclude_action_categories=("memory",),
        )
        await router.start()
        try:
            router.emit(_ev("memory.record.create"))
            router.emit(_ev("memory.record.delete"))
            router.emit(_ev("memory.event.create"))
            router.emit(_ev("agent.run.start"))
            router.emit(_ev("policy.allow"))
            await _drain(router, sink)

            actions = [e.action for e in sink.received]
            # All memory.* excluded.
            assert not any(a.startswith("memory.") for a in actions)
            # Other categories untouched.
            assert "agent.run.start" in actions
            assert "policy.allow" in actions
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_category_match_uses_first_segment_only(self):
        """``exclude_action_categories=("agent",)`` drops
        ``agent.run.start`` but NOT ``agent_management.something``
        (no `.` split prefix match).  Important for preventing
        over-broad category filters.
        """
        sink = _CapturingSink()
        router = AuditRouter(
            sinks=[sink],
            exclude_action_categories=("agent",),
        )
        await router.start()
        try:
            router.emit(_ev("agent.run.start"))  # excluded
            router.emit(_ev("agent_management.create"))  # NOT excluded
            await _drain(router, sink)

            actions = [e.action for e in sink.received]
            assert "agent.run.start" not in actions
            assert "agent_management.create" in actions
        finally:
            await router.shutdown()


class TestSampleRates:
    @pytest.mark.asyncio
    async def test_rate_zero_drops_everything(self):
        sink = _CapturingSink()
        router = AuditRouter(sinks=[sink], sample_rates={"provider.call": 0.0})
        await router.start()
        try:
            for _ in range(100):
                router.emit(_ev("provider.call"))
            await _drain(router, sink)
            assert len(sink.received) == 0, f"rate=0.0 should drop all events; got {len(sink.received)}"
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_rate_one_keeps_everything(self):
        sink = _CapturingSink()
        router = AuditRouter(sinks=[sink], sample_rates={"provider.call": 1.0})
        await router.start()
        try:
            for _ in range(50):
                router.emit(_ev("provider.call"))
            await _drain(router, sink)
            assert len(sink.received) == 50, f"rate=1.0 should keep all events; got {len(sink.received)}"
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_rate_half_approximates_half(self):
        """rate=0.5 should keep roughly half of events.  Use a
        seeded RNG so the test is deterministic.  Assertion leaves
        a wide band (30-70 out of 200) to avoid flakiness while
        still catching "all dropped" or "all kept" regressions.
        """
        random.seed(42)
        sink = _CapturingSink()
        router = AuditRouter(sinks=[sink], sample_rates={"provider.call": 0.5})
        await router.start()
        try:
            for _ in range(200):
                router.emit(_ev("provider.call"))
            await _drain(router, sink)
            kept = len(sink.received)
            assert 70 <= kept <= 130, (
                f"rate=0.5 should keep ~100/200; got {kept}.  If 0 or 200, the sampling logic is broken."
            )
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_sample_rate_applies_only_to_listed_action(self):
        """sample_rates={'provider.call': 0.0} drops provider.call
        but NOT other actions — they pass through unaffected.
        """
        sink = _CapturingSink()
        router = AuditRouter(sinks=[sink], sample_rates={"provider.call": 0.0})
        await router.start()
        try:
            for _ in range(20):
                router.emit(_ev("provider.call"))
                router.emit(_ev("agent.run.start"))
            await _drain(router, sink)

            actions = [e.action for e in sink.received]
            assert actions.count("provider.call") == 0
            assert actions.count("agent.run.start") == 20
        finally:
            await router.shutdown()


class TestCombinedFilters:
    @pytest.mark.asyncio
    async def test_exclude_takes_precedence_over_sample(self):
        """If an action appears in both exclude_actions and
        sample_rates, the exclusion wins (event is always dropped,
        never considered for sampling).
        """
        sink = _CapturingSink()
        router = AuditRouter(
            sinks=[sink],
            exclude_actions=("provider.call",),
            sample_rates={"provider.call": 1.0},  # would keep all if consulted
        )
        await router.start()
        try:
            for _ in range(50):
                router.emit(_ev("provider.call"))
            await _drain(router, sink)
            assert len(sink.received) == 0, f"exclude should override sample_rates; got {len(sink.received)}"
        finally:
            await router.shutdown()
