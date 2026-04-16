"""Tests for distributed team cancellation via _sync_remote_cancel."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_primitives_gateway.agents.team_runner import TeamRunner


class TestSyncRemoteCancel:
    """TeamRunner._sync_remote_cancel checks Redis event store for cancel signals."""

    @pytest.fixture
    def runner(self) -> TeamRunner:
        return TeamRunner()

    @pytest.mark.asyncio
    async def test_no_event_store_noop(self, runner: TeamRunner):
        """Without an event store, _sync_remote_cancel is a no-op."""
        runner._cancel_events["run-1"] = asyncio.Event()
        await runner._sync_remote_cancel("run-1")
        assert not runner._cancel_events["run-1"].is_set()

    @pytest.mark.asyncio
    async def test_non_cancelled_status_noop(self, runner: TeamRunner):
        """If event store status is not 'cancelled', local event stays unset."""
        mock_store = AsyncMock()
        mock_store.get_status = AsyncMock(return_value="running")

        # Inject mock event store via the team_bg
        with _mock_team_bg(mock_store):
            runner._cancel_events["run-1"] = asyncio.Event()
            await runner._sync_remote_cancel("run-1")
            assert not runner._cancel_events["run-1"].is_set()

    @pytest.mark.asyncio
    async def test_cancelled_status_sets_local_event(self, runner: TeamRunner):
        """If event store says 'cancelled', local asyncio.Event gets set."""
        mock_store = AsyncMock()
        mock_store.get_status = AsyncMock(return_value="cancelled")

        with _mock_team_bg(mock_store):
            runner._cancel_events["run-1"] = asyncio.Event()
            await runner._sync_remote_cancel("run-1")
            assert runner._cancel_events["run-1"].is_set()

    @pytest.mark.asyncio
    async def test_already_set_event_not_double_set(self, runner: TeamRunner):
        """If local event is already set, _sync_remote_cancel doesn't error."""
        mock_store = AsyncMock()
        mock_store.get_status = AsyncMock(return_value="cancelled")

        with _mock_team_bg(mock_store):
            evt = asyncio.Event()
            evt.set()
            runner._cancel_events["run-1"] = evt
            await runner._sync_remote_cancel("run-1")
            assert evt.is_set()

    @pytest.mark.asyncio
    async def test_missing_cancel_event_no_error(self, runner: TeamRunner):
        """If no cancel event registered for this run, _sync_remote_cancel doesn't error."""
        mock_store = AsyncMock()
        mock_store.get_status = AsyncMock(return_value="cancelled")

        with _mock_team_bg(mock_store):
            # No cancel event registered for "run-1"
            await runner._sync_remote_cancel("run-1")  # should not raise

    @pytest.mark.asyncio
    async def test_event_store_exception_swallowed(self, runner: TeamRunner):
        """Exceptions from event store are swallowed (best-effort)."""
        mock_store = AsyncMock()
        mock_store.get_status = AsyncMock(side_effect=Exception("Redis down"))

        with _mock_team_bg(mock_store):
            runner._cancel_events["run-1"] = asyncio.Event()
            await runner._sync_remote_cancel("run-1")  # should not raise
            assert not runner._cancel_events["run-1"].is_set()

    @pytest.mark.asyncio
    async def test_idle_status_does_not_cancel(self, runner: TeamRunner):
        """Status 'idle' should not trigger cancellation."""
        mock_store = AsyncMock()
        mock_store.get_status = AsyncMock(return_value="idle")

        with _mock_team_bg(mock_store):
            runner._cancel_events["run-1"] = asyncio.Event()
            await runner._sync_remote_cancel("run-1")
            assert not runner._cancel_events["run-1"].is_set()


def _mock_team_bg(event_store):
    """Context manager that patches the team_bg import to return a mock with the given event store."""
    from unittest.mock import patch

    mock_bg = MagicMock()
    mock_bg._event_store = event_store

    return patch(
        "agentic_primitives_gateway.agents.team_runner.TeamRunner._get_event_store",
        return_value=event_store,
    )
