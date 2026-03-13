"""Tests for reconnect_event_generator in routes/_background.py."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from agentic_primitives_gateway.routes._background import (
    BackgroundRunManager,
    reconnect_event_generator,
)


async def _collect(gen) -> list[str]:
    """Collect all items from an async generator."""
    items: list[str] = []
    async for item in gen:
        items.append(item)
    return items


def _parse_events(raw: list[str]) -> list[dict[str, Any]]:
    """Parse SSE data lines into dicts."""
    return [json.loads(line.removeprefix("data: ").strip()) for line in raw if line.startswith("data: ")]


# ── Tests ────────────────────────────────────────────────────────────


class TestReconnectEventGenerator:
    @pytest.mark.asyncio
    async def test_replays_stored_events_and_stops_on_done(self) -> None:
        """Replays stored events and closes when a 'done' event is seen."""
        events = [
            {"type": "team_start", "team_name": "t1"},
            {"type": "agent_token", "content": "hello"},
            {"type": "done", "response": "finished"},
        ]

        bg = BackgroundRunManager()
        bg.get_events_async = AsyncMock(return_value=events)  # type: ignore[method-assign]
        bg.get_status_async = AsyncMock(return_value="idle")  # type: ignore[method-assign]

        with patch("agentic_primitives_gateway.routes._background.asyncio.sleep", new_callable=AsyncMock):
            raw = await _collect(reconnect_event_generator(bg, "run-1"))

        parsed = _parse_events(raw)
        assert len(parsed) == 3
        assert parsed[0]["type"] == "team_start"
        assert parsed[-1]["type"] == "done"

    @pytest.mark.asyncio
    async def test_throttles_token_events(self) -> None:
        """Token events trigger a small sleep for throttling."""
        events = [
            {"type": "token", "content": "a"},
            {"type": "sub_agent_token", "content": "b"},
            {"type": "done", "response": "ok"},
        ]

        bg = BackgroundRunManager()
        bg.get_events_async = AsyncMock(return_value=events)  # type: ignore[method-assign]
        bg.get_status_async = AsyncMock(return_value="idle")  # type: ignore[method-assign]

        with patch(
            "agentic_primitives_gateway.routes._background.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep:
            await _collect(reconnect_event_generator(bg, "run-1"))

        # Token throttle calls sleep(0.005) for each token-type event
        throttle_calls = [c for c in mock_sleep.call_args_list if c.args == (0.005,)]
        assert len(throttle_calls) == 2  # one for "token", one for "sub_agent_token"

    @pytest.mark.asyncio
    async def test_closes_on_cancelled_status(self) -> None:
        """Generator breaks when status is 'cancelled'."""
        events = [{"type": "team_start", "team_name": "t1"}]

        bg = BackgroundRunManager()
        bg.get_events_async = AsyncMock(return_value=events)  # type: ignore[method-assign]
        bg.get_status_async = AsyncMock(return_value="cancelled")  # type: ignore[method-assign]

        with patch("agentic_primitives_gateway.routes._background.asyncio.sleep", new_callable=AsyncMock):
            raw = await _collect(reconnect_event_generator(bg, "run-1"))

        parsed = _parse_events(raw)
        assert len(parsed) == 1
        assert parsed[0]["type"] == "team_start"

    @pytest.mark.asyncio
    async def test_closes_after_seen_running_then_idle(self) -> None:
        """Once a run transitions from running to idle, closes after short idle window."""
        events = [{"type": "team_start", "team_name": "t1"}]

        call_count = 0

        async def get_status_side_effect(run_id: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return "running"
            return "idle"

        bg = BackgroundRunManager()
        # Events don't grow — same list each time, so idle_count increments
        bg.get_events_async = AsyncMock(return_value=events)  # type: ignore[method-assign]
        bg.get_status_async = AsyncMock(side_effect=get_status_side_effect)  # type: ignore[method-assign]

        with patch("agentic_primitives_gateway.routes._background.asyncio.sleep", new_callable=AsyncMock):
            raw = await _collect(reconnect_event_generator(bg, "run-1"))

        # Should have emitted the initial event on the first poll
        parsed = _parse_events(raw)
        assert len(parsed) >= 1
        assert parsed[0]["type"] == "team_start"

    @pytest.mark.asyncio
    async def test_handles_empty_events_and_polls(self) -> None:
        """When no events exist initially, the generator polls and eventually stops."""
        call_count = 0

        async def growing_events(run_id: str) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return []
            return [{"type": "done", "response": "late"}]

        bg = BackgroundRunManager()
        bg.get_events_async = AsyncMock(side_effect=growing_events)  # type: ignore[method-assign]
        bg.get_status_async = AsyncMock(return_value="running")  # type: ignore[method-assign]

        with patch("agentic_primitives_gateway.routes._background.asyncio.sleep", new_callable=AsyncMock):
            raw = await _collect(reconnect_event_generator(bg, "run-1"))

        parsed = _parse_events(raw)
        assert len(parsed) == 1
        assert parsed[0]["type"] == "done"

    @pytest.mark.asyncio
    async def test_custom_done_event_types(self) -> None:
        """Custom done_event_types parameter is respected."""
        events = [
            {"type": "team_start"},
            {"type": "custom_finish"},
        ]

        bg = BackgroundRunManager()
        bg.get_events_async = AsyncMock(return_value=events)  # type: ignore[method-assign]
        bg.get_status_async = AsyncMock(return_value="idle")  # type: ignore[method-assign]

        with patch("agentic_primitives_gateway.routes._background.asyncio.sleep", new_callable=AsyncMock):
            raw = await _collect(reconnect_event_generator(bg, "run-1", done_event_types=frozenset({"custom_finish"})))

        parsed = _parse_events(raw)
        assert len(parsed) == 2
        assert parsed[-1]["type"] == "custom_finish"

    @pytest.mark.asyncio
    async def test_incremental_event_delivery(self) -> None:
        """Events arriving incrementally are delivered without duplicates."""
        call_count = 0

        async def growing_events(run_id: str) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [{"type": "phase_change", "phase": "planning"}]
            if call_count == 2:
                return [
                    {"type": "phase_change", "phase": "planning"},
                    {"type": "agent_token", "content": "hi"},
                ]
            return [
                {"type": "phase_change", "phase": "planning"},
                {"type": "agent_token", "content": "hi"},
                {"type": "done", "response": "ok"},
            ]

        bg = BackgroundRunManager()
        bg.get_events_async = AsyncMock(side_effect=growing_events)  # type: ignore[method-assign]
        bg.get_status_async = AsyncMock(return_value="running")  # type: ignore[method-assign]

        with patch("agentic_primitives_gateway.routes._background.asyncio.sleep", new_callable=AsyncMock):
            raw = await _collect(reconnect_event_generator(bg, "run-1"))

        parsed = _parse_events(raw)
        # Should get exactly 3 events (no duplicates from re-polling)
        assert len(parsed) == 3
        assert parsed[0]["type"] == "phase_change"
        assert parsed[1]["type"] == "agent_token"
        assert parsed[2]["type"] == "done"
