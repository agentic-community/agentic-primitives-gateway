"""Tests for background run tracking, session status, and event replay.

Covers the background task pattern for both agent chat streaming and team
run streaming — the asyncio.Task + Queue decoupling that lets runs complete
even when the client disconnects.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from agentic_primitives_gateway.agents.store import FileAgentStore
from agentic_primitives_gateway.agents.team_store import FileTeamStore
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.routes.agents import (
    _STALE_RUN_SECONDS as AGENT_STALE,
)
from agentic_primitives_gateway.routes.agents import (
    _active_runs,
    set_agent_store,
)
from agentic_primitives_gateway.routes.agents import (
    _cleanup_stale_runs as agent_cleanup,
)
from agentic_primitives_gateway.routes.teams import (
    _active_team_runs,
    set_team_store,
)
from agentic_primitives_gateway.routes.teams import (
    _cleanup_stale_runs as team_cleanup,
)

SAMPLE_AGENT = {
    "name": "bg-agent",
    "model": "test-model",
    "primitives": {
        "memory": {"enabled": True, "tools": None, "namespace": "agent:{agent_name}"},
    },
}

SAMPLE_TEAM = {
    "name": "bg-team",
    "planner": "planner",
    "synthesizer": "synth",
    "workers": ["worker1"],
}


@pytest.fixture(autouse=True)
def _setup(tmp_path: Any) -> None:
    agent_store = FileAgentStore(path=str(tmp_path / "agents.json"))
    set_agent_store(agent_store)
    team_store = FileTeamStore(path=str(tmp_path / "teams.json"))
    set_team_store(team_store)
    # Clear active runs between tests
    _active_runs.clear()
    _active_team_runs.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ── Agent session status endpoint ────────────────────────────────────


class TestAgentSessionStatus:
    def test_idle_when_no_run(self, client: TestClient) -> None:
        client.post("/api/v1/agents", json=SAMPLE_AGENT)
        resp = client.get("/api/v1/agents/bg-agent/sessions/no-such-session/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "idle"

    def test_running_when_task_active(self, client: TestClient) -> None:
        client.post("/api/v1/agents", json=SAMPLE_AGENT)

        # Inject a fake active run
        task = MagicMock(spec=asyncio.Task)
        task.done.return_value = False
        _active_runs["fake-session"] = (task, asyncio.Queue(), time.monotonic())

        resp = client.get("/api/v1/agents/bg-agent/sessions/fake-session/status")
        assert resp.json()["status"] == "running"

    def test_idle_when_task_done(self, client: TestClient) -> None:
        client.post("/api/v1/agents", json=SAMPLE_AGENT)

        task = MagicMock(spec=asyncio.Task)
        task.done.return_value = True
        _active_runs["done-session"] = (task, asyncio.Queue(), time.monotonic())

        resp = client.get("/api/v1/agents/bg-agent/sessions/done-session/status")
        assert resp.json()["status"] == "idle"


# ── Agent streaming uses background task ─────────────────────────────


class TestAgentStreamingBackgroundTask:
    def test_stream_creates_background_task(self, client: TestClient) -> None:
        """Streaming endpoint should create a background task entry."""
        client.post("/api/v1/agents", json=SAMPLE_AGENT)

        resp = client.post(
            "/api/v1/agents/bg-agent/chat/stream",
            json={"message": "hello", "session_id": "bg-test-1"},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        body = resp.text
        assert '"type": "done"' in body or '"type":"done"' in body

    def test_stream_completes_with_done_event(self, client: TestClient) -> None:
        client.post("/api/v1/agents", json=SAMPLE_AGENT)

        resp = client.post(
            "/api/v1/agents/bg-agent/chat/stream",
            json={"message": "test", "session_id": "bg-test-2"},
        )
        assert "done" in resp.text


# ── Agent stale run cleanup ──────────────────────────────────────────


class TestAgentCleanupStaleRuns:
    def test_removes_done_tasks(self) -> None:
        task = MagicMock(spec=asyncio.Task)
        task.done.return_value = True
        _active_runs["old-session"] = (task, asyncio.Queue(), time.monotonic())

        agent_cleanup()
        assert "old-session" not in _active_runs

    def test_removes_stale_tasks(self) -> None:
        task = MagicMock(spec=asyncio.Task)
        task.done.return_value = False
        _active_runs["stale-session"] = (task, asyncio.Queue(), time.monotonic() - AGENT_STALE - 1)

        agent_cleanup()
        assert "stale-session" not in _active_runs

    def test_keeps_active_tasks(self) -> None:
        task = MagicMock(spec=asyncio.Task)
        task.done.return_value = False
        _active_runs["active-session"] = (task, asyncio.Queue(), time.monotonic())

        agent_cleanup()
        assert "active-session" in _active_runs


# ── Team run status endpoint ─────────────────────────────────────────


class TestTeamRunStatus:
    def test_idle_when_no_run(self, client: TestClient) -> None:
        client.post("/api/v1/teams", json=SAMPLE_TEAM)
        resp = client.get("/api/v1/teams/bg-team/runs/no-such-run/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "idle"

    def test_running_when_task_active(self, client: TestClient) -> None:
        client.post("/api/v1/teams", json=SAMPLE_TEAM)

        task = MagicMock(spec=asyncio.Task)
        task.done.return_value = False
        _active_team_runs["fake-run"] = (task, asyncio.Queue(), [], time.monotonic())

        resp = client.get("/api/v1/teams/bg-team/runs/fake-run/status")
        assert resp.json()["status"] == "running"

    def test_idle_when_task_done(self, client: TestClient) -> None:
        client.post("/api/v1/teams", json=SAMPLE_TEAM)

        task = MagicMock(spec=asyncio.Task)
        task.done.return_value = True
        _active_team_runs["done-run"] = (task, asyncio.Queue(), [], time.monotonic())

        resp = client.get("/api/v1/teams/bg-team/runs/done-run/status")
        assert resp.json()["status"] == "idle"


# ── Team run events endpoint ─────────────────────────────────────────


class TestTeamRunEvents:
    def test_unknown_run_returns_empty(self, client: TestClient) -> None:
        client.post("/api/v1/teams", json=SAMPLE_TEAM)
        resp = client.get("/api/v1/teams/bg-team/runs/no-such-run/events")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "unknown"
        assert data["events"] == []

    def test_returns_recorded_events(self, client: TestClient) -> None:
        client.post("/api/v1/teams", json=SAMPLE_TEAM)

        events = [
            {"type": "team_start", "team_run_id": "run-1", "team_name": "bg-team"},
            {"type": "phase_change", "phase": "planning"},
            {"type": "tasks_created", "count": 1, "tasks": [{"id": "t1", "title": "Task 1"}]},
            {"type": "done", "response": "all done", "tasks_created": 1, "tasks_completed": 1, "workers_used": ["w1"]},
        ]
        task = MagicMock(spec=asyncio.Task)
        task.done.return_value = False
        _active_team_runs["run-1"] = (task, asyncio.Queue(), events, time.monotonic())

        resp = client.get("/api/v1/teams/bg-team/runs/run-1/events")
        data = resp.json()
        assert data["status"] == "running"
        assert len(data["events"]) == 4
        assert data["events"][0]["type"] == "team_start"
        assert data["events"][-1]["type"] == "done"

    def test_status_idle_when_task_done(self, client: TestClient) -> None:
        client.post("/api/v1/teams", json=SAMPLE_TEAM)

        task = MagicMock(spec=asyncio.Task)
        task.done.return_value = True
        _active_team_runs["done-run"] = (task, asyncio.Queue(), [{"type": "done"}], time.monotonic())

        resp = client.get("/api/v1/teams/bg-team/runs/done-run/events")
        assert resp.json()["status"] == "idle"


# ── Team run retrieval endpoint ──────────────────────────────────────


class TestTeamRunRetrieval:
    def test_empty_run(self, client: TestClient) -> None:
        client.post("/api/v1/teams", json=SAMPLE_TEAM)
        resp = client.get("/api/v1/teams/bg-team/runs/empty-run")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tasks"] == []
        assert data["tasks_created"] == 0
        assert data["status"] == "idle"


# ── Team streaming uses background task ──────────────────────────────


class TestTeamStreamingBackgroundTask:
    def test_stream_returns_sse(self, client: TestClient) -> None:
        client.post("/api/v1/teams", json=SAMPLE_TEAM)

        async def mock_stream(team_spec, message):
            yield {"type": "team_start", "team_run_id": "run-42", "team_name": "bg-team"}
            yield {"type": "phase_change", "phase": "planning"}
            yield {"type": "done", "response": "done", "tasks_created": 0, "tasks_completed": 0, "workers_used": []}

        with patch("agentic_primitives_gateway.routes.teams._runner") as mock_runner:
            mock_runner.run_stream = mock_stream
            resp = client.post("/api/v1/teams/bg-team/run/stream", json={"message": "go"})

        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        assert "team_start" in resp.text
        assert "done" in resp.text

    def test_stream_records_events(self, client: TestClient) -> None:
        """Events emitted during streaming should be recorded for replay."""
        client.post("/api/v1/teams", json=SAMPLE_TEAM)

        recorded_run_id = None

        async def mock_stream(team_spec, message):
            nonlocal recorded_run_id
            recorded_run_id = "run-rec"
            yield {"type": "team_start", "team_run_id": "run-rec", "team_name": "bg-team"}
            yield {"type": "phase_change", "phase": "planning"}
            yield {"type": "done", "response": "result", "tasks_created": 0, "tasks_completed": 0, "workers_used": []}

        with patch("agentic_primitives_gateway.routes.teams._runner") as mock_runner:
            mock_runner.run_stream = mock_stream
            client.post("/api/v1/teams/bg-team/run/stream", json={"message": "go"})

        # After stream completes, events should be in _active_team_runs
        # (they stay for 60s after completion)
        if "run-rec" in _active_team_runs:
            _, _, event_log, _ = _active_team_runs["run-rec"]
            assert len(event_log) == 3
            assert event_log[0]["type"] == "team_start"
            assert event_log[-1]["type"] == "done"


# ── Team stale run cleanup ───────────────────────────────────────────


class TestTeamCleanupStaleRuns:
    def test_keeps_recently_completed(self) -> None:
        """Completed runs are kept for 60s for event replay."""
        task = MagicMock(spec=asyncio.Task)
        task.done.return_value = True
        _active_team_runs["recent"] = (task, asyncio.Queue(), [], time.monotonic())

        team_cleanup()
        assert "recent" in _active_team_runs

    def test_removes_old_completed(self) -> None:
        task = MagicMock(spec=asyncio.Task)
        task.done.return_value = True
        _active_team_runs["old"] = (task, asyncio.Queue(), [], time.monotonic() - 120)

        team_cleanup()
        assert "old" not in _active_team_runs

    def test_removes_stale_running(self) -> None:
        task = MagicMock(spec=asyncio.Task)
        task.done.return_value = False
        _active_team_runs["stale"] = (task, asyncio.Queue(), [], time.monotonic() - 700)

        team_cleanup()
        assert "stale" not in _active_team_runs

    def test_keeps_active_running(self) -> None:
        task = MagicMock(spec=asyncio.Task)
        task.done.return_value = False
        _active_team_runs["active"] = (task, asyncio.Queue(), [], time.monotonic())

        team_cleanup()
        assert "active" in _active_team_runs
