from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agentic_primitives_gateway.agents.file_store import FileTeamStore
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.models.teams import TeamRunPhase, TeamRunResponse
from agentic_primitives_gateway.routes import teams as teams_module
from agentic_primitives_gateway.routes._background import BackgroundRunManager
from agentic_primitives_gateway.routes.teams import set_team_bg, set_team_store


@pytest.fixture(autouse=True)
def _init_team_store(tmp_path: Any) -> None:
    store = FileTeamStore(path=str(tmp_path / "teams.json"))
    set_team_store(store)
    # Reset bg manager for each test
    set_team_bg(BackgroundRunManager(stale_seconds=600, grace_seconds=60))


@pytest.fixture
def teams_client() -> TestClient:
    return TestClient(app)


class TestTeamCRUD:
    def test_create_team(self, teams_client: TestClient) -> None:
        resp = teams_client.post(
            "/api/v1/teams",
            json={
                "name": "test-team",
                "description": "A test team",
                "planner": "planner",
                "synthesizer": "synthesizer",
                "workers": ["worker1"],
            },
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "test-team"

    def test_create_team_duplicate(self, teams_client: TestClient) -> None:
        payload = {
            "name": "dup-team",
            "planner": "p",
            "synthesizer": "s",
            "workers": ["w"],
        }
        teams_client.post("/api/v1/teams", json=payload)
        resp = teams_client.post("/api/v1/teams", json=payload)
        assert resp.status_code == 409

    def test_list_teams(self, teams_client: TestClient) -> None:
        teams_client.post(
            "/api/v1/teams",
            json={"name": "t1", "planner": "p", "synthesizer": "s", "workers": ["w"]},
        )
        resp = teams_client.get("/api/v1/teams")
        assert resp.status_code == 200
        assert len(resp.json()["teams"]) >= 1

    def test_get_team(self, teams_client: TestClient) -> None:
        teams_client.post(
            "/api/v1/teams",
            json={"name": "get-team", "planner": "p", "synthesizer": "s", "workers": ["w"]},
        )
        resp = teams_client.get("/api/v1/teams/get-team")
        assert resp.status_code == 200
        assert resp.json()["name"] == "get-team"

    def test_get_team_not_found(self, teams_client: TestClient) -> None:
        resp = teams_client.get("/api/v1/teams/nonexistent")
        assert resp.status_code == 404

    def test_update_team(self, teams_client: TestClient) -> None:
        teams_client.post(
            "/api/v1/teams",
            json={"name": "upd-team", "planner": "p", "synthesizer": "s", "workers": ["w"]},
        )
        resp = teams_client.put("/api/v1/teams/upd-team", json={"description": "updated"})
        assert resp.status_code == 200
        assert resp.json()["description"] == "updated"

    def test_update_team_not_found(self, teams_client: TestClient) -> None:
        resp = teams_client.put("/api/v1/teams/nonexistent", json={"description": "x"})
        assert resp.status_code == 404

    def test_delete_team(self, teams_client: TestClient) -> None:
        teams_client.post(
            "/api/v1/teams",
            json={"name": "del-team", "planner": "p", "synthesizer": "s", "workers": ["w"]},
        )
        resp = teams_client.delete("/api/v1/teams/del-team")
        assert resp.status_code == 200

    def test_delete_team_not_found(self, teams_client: TestClient) -> None:
        resp = teams_client.delete("/api/v1/teams/nonexistent")
        assert resp.status_code == 404


class TestTeamStoreNotInitialized:
    def test_get_store_raises_when_not_initialized(self) -> None:
        set_team_store(None)  # type: ignore[arg-type]
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/teams")
        assert resp.status_code == 500


class TestTeamRun:
    def test_run_team(self, teams_client: TestClient) -> None:
        teams_client.post(
            "/api/v1/teams",
            json={"name": "run-team", "planner": "p", "synthesizer": "s", "workers": ["w"]},
        )
        mock_response = TeamRunResponse(
            response="synthesized",
            team_run_id="run-1",
            team_name="run-team",
            phase=TeamRunPhase.DONE,
            tasks_created=2,
            tasks_completed=2,
            workers_used=["w"],
        )
        with patch("agentic_primitives_gateway.routes.teams._runner") as mock_runner:
            mock_runner.run = AsyncMock(return_value=mock_response)
            resp = teams_client.post("/api/v1/teams/run-team/run", json={"message": "do stuff"})

        assert resp.status_code == 200
        assert resp.json()["response"] == "synthesized"

    def test_run_team_not_found(self, teams_client: TestClient) -> None:
        resp = teams_client.post("/api/v1/teams/nonexistent/run", json={"message": "hi"})
        assert resp.status_code == 404

    def test_run_stream_team(self, teams_client: TestClient) -> None:
        teams_client.post(
            "/api/v1/teams",
            json={"name": "stream-team", "planner": "p", "synthesizer": "s", "workers": ["w"]},
        )

        async def mock_stream(team_spec, message):
            yield {"type": "team_start", "team_name": "stream-team"}
            yield {"type": "done", "response": "streamed"}

        with patch("agentic_primitives_gateway.routes.teams._runner") as mock_runner:
            mock_runner.run_stream = mock_stream
            resp = teams_client.post("/api/v1/teams/stream-team/run/stream", json={"message": "do stuff"})

        assert resp.status_code == 200
        assert "team_start" in resp.text

    def test_run_stream_team_not_found(self, teams_client: TestClient) -> None:
        resp = teams_client.post("/api/v1/teams/nonexistent/run/stream", json={"message": "hi"})
        assert resp.status_code == 404


TEAM_PAYLOAD = {"name": "test-team", "planner": "p", "synthesizer": "s", "workers": ["w"]}


class TestSetTeamBg:
    def test_set_team_bg_replaces_manager(self) -> None:
        new_bg = BackgroundRunManager(stale_seconds=300)
        set_team_bg(new_bg)
        assert teams_module._bg is new_bg
        assert teams_module._active_team_runs is new_bg.runs


class TestGetTeamRunner:
    def test_returns_runner(self) -> None:
        runner = teams_module.get_team_runner()
        assert runner is teams_module._runner


class TestListTeamRuns:
    def test_list_runs_empty(self, teams_client: TestClient) -> None:
        teams_client.post("/api/v1/teams", json=TEAM_PAYLOAD)
        resp = teams_client.get("/api/v1/teams/test-team/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["team_name"] == "test-team"
        assert data["runs"] == []

    def test_list_runs_404(self, teams_client: TestClient) -> None:
        resp = teams_client.get("/api/v1/teams/nonexistent/runs")
        assert resp.status_code == 404

    def test_list_runs_with_local_active_run(self, teams_client: TestClient) -> None:
        teams_client.post("/api/v1/teams", json=TEAM_PAYLOAD)

        # Manually inject a run entry into the bg manager
        loop = asyncio.new_event_loop()
        task = loop.create_task(asyncio.sleep(100))
        queue: asyncio.Queue[dict | None] = asyncio.Queue()
        event_log = [{"type": "team_start", "team_name": "test-team"}]
        import time

        teams_module._bg._runs["run-123"] = (task, queue, event_log, time.monotonic())

        resp = teams_client.get("/api/v1/teams/test-team/runs")
        assert resp.status_code == 200
        runs = resp.json()["runs"]
        assert any(r["team_run_id"] == "run-123" for r in runs)

        task.cancel()
        loop.close()

    def test_list_runs_with_redis_event_store(self, teams_client: TestClient) -> None:
        teams_client.post("/api/v1/teams", json=TEAM_PAYLOAD)

        mock_event_store = AsyncMock()
        mock_event_store.get_index = AsyncMock(return_value=["redis-run-1"])

        teams_module._bg._event_store = mock_event_store
        teams_module._bg.get_status_async = AsyncMock(return_value="idle")

        resp = teams_client.get("/api/v1/teams/test-team/runs")
        assert resp.status_code == 200
        runs = resp.json()["runs"]
        assert any(r["team_run_id"] == "redis-run-1" for r in runs)

        # Verify it used the index with the correct key
        mock_event_store.get_index.assert_called_once_with("team:test-team:runs")

        # Clean up
        teams_module._bg._event_store = None

    def test_list_runs_redis_error_handled(self, teams_client: TestClient) -> None:
        teams_client.post("/api/v1/teams", json=TEAM_PAYLOAD)

        mock_event_store = AsyncMock()
        mock_event_store.get_index = AsyncMock(side_effect=RuntimeError("Redis error"))

        teams_module._bg._event_store = mock_event_store

        resp = teams_client.get("/api/v1/teams/test-team/runs")
        assert resp.status_code == 200

        # Clean up
        teams_module._bg._event_store = None


class TestDeleteTeamRun:
    def test_delete_run(self, teams_client: TestClient) -> None:
        teams_client.post("/api/v1/teams", json=TEAM_PAYLOAD)
        resp = teams_client.delete("/api/v1/teams/test-team/runs/run-xyz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_delete_run_404(self, teams_client: TestClient) -> None:
        resp = teams_client.delete("/api/v1/teams/nonexistent/runs/run-xyz")
        assert resp.status_code == 404

    def test_delete_run_cleans_tasks(self, teams_client: TestClient) -> None:
        teams_client.post("/api/v1/teams", json=TEAM_PAYLOAD)

        mock_task = MagicMock()
        mock_task.id = "t1"
        mock_task.status = "done"

        with patch("agentic_primitives_gateway.routes.teams.registry") as mock_reg:
            mock_reg.tasks.list_tasks = AsyncMock(return_value=[mock_task])
            mock_reg.tasks.update_task = AsyncMock()
            resp = teams_client.delete("/api/v1/teams/test-team/runs/run-xyz")

        assert resp.status_code == 200

    def test_delete_run_with_event_store(self, teams_client: TestClient) -> None:
        teams_client.post("/api/v1/teams", json=TEAM_PAYLOAD)

        mock_event_store = AsyncMock()
        mock_event_store.get_owner = AsyncMock(return_value=None)
        teams_module._bg._event_store = mock_event_store

        resp = teams_client.delete("/api/v1/teams/test-team/runs/run-xyz")
        assert resp.status_code == 200
        mock_event_store.delete.assert_called_once_with("run-xyz")

        # Clean up
        teams_module._bg._event_store = None


class TestStreamTeamRunEvents:
    def test_stream_events(self, teams_client: TestClient) -> None:
        teams_client.post("/api/v1/teams", json=TEAM_PAYLOAD)
        # Mock bg manager to return a done event so the generator exits immediately
        with (
            patch.object(
                teams_module._bg, "get_events_async", new=AsyncMock(return_value=[{"type": "done", "response": "ok"}])
            ),
            patch.object(teams_module._bg, "get_status_async", new=AsyncMock(return_value="idle")),
        ):
            resp = teams_client.get("/api/v1/teams/test-team/runs/run-xyz/stream")
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

    def test_stream_events_404(self, teams_client: TestClient) -> None:
        resp = teams_client.get("/api/v1/teams/nonexistent/runs/run-xyz/stream")
        assert resp.status_code == 404


class TestGetTeamRunStatus:
    def test_status_idle(self, teams_client: TestClient) -> None:
        teams_client.post("/api/v1/teams", json=TEAM_PAYLOAD)
        resp = teams_client.get("/api/v1/teams/test-team/runs/run-xyz/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "idle"

    def test_status_404(self, teams_client: TestClient) -> None:
        resp = teams_client.get("/api/v1/teams/nonexistent/runs/run-xyz/status")
        assert resp.status_code == 404


class TestCancelTeamRun:
    def test_cancel_no_active_run(self, teams_client: TestClient) -> None:
        teams_client.post("/api/v1/teams", json=TEAM_PAYLOAD)
        resp = teams_client.delete("/api/v1/teams/test-team/runs/run-xyz/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_cancel_404(self, teams_client: TestClient) -> None:
        resp = teams_client.delete("/api/v1/teams/nonexistent/runs/run-xyz/cancel")
        assert resp.status_code == 404

    def test_cancel_with_event_store(self, teams_client: TestClient) -> None:
        teams_client.post("/api/v1/teams", json=TEAM_PAYLOAD)

        mock_event_store = AsyncMock()
        mock_event_store.get_owner = AsyncMock(return_value=None)
        teams_module._bg._event_store = mock_event_store

        resp = teams_client.delete("/api/v1/teams/test-team/runs/run-xyz/cancel")
        assert resp.status_code == 200
        mock_event_store.set_status.assert_called_once()
        mock_event_store.append_event.assert_called_once()

        # Clean up
        teams_module._bg._event_store = None

    def test_cancel_marks_tasks_failed(self, teams_client: TestClient) -> None:
        teams_client.post("/api/v1/teams", json=TEAM_PAYLOAD)

        mock_task = MagicMock()
        mock_task.id = "t1"
        mock_task.status = "in_progress"

        with patch("agentic_primitives_gateway.routes.teams.registry") as mock_reg:
            mock_reg.tasks.list_tasks = AsyncMock(return_value=[mock_task])
            mock_reg.tasks.update_task = AsyncMock()
            resp = teams_client.delete("/api/v1/teams/test-team/runs/run-xyz/cancel")

        assert resp.status_code == 200

    def test_cancel_forbidden_for_non_owner(self, teams_client: TestClient) -> None:
        # Create team with shared_with=["*"] so access check passes
        payload = {**TEAM_PAYLOAD, "shared_with": ["*"]}
        teams_client.post("/api/v1/teams", json=payload)

        mock_event_store = AsyncMock()
        mock_event_store.get_owner = AsyncMock(return_value="other-user-id")
        teams_module._bg._event_store = mock_event_store

        from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal

        non_admin = AuthenticatedPrincipal(id="user1", type="user", scopes=frozenset())
        # The team was created under the noop owner namespace; a non-admin
        # user must address it qualified to pass through route resolution.
        with patch("agentic_primitives_gateway.routes.teams.require_principal", return_value=non_admin):
            resp = teams_client.delete("/api/v1/teams/noop:test-team/runs/run-xyz/cancel")
        assert resp.status_code == 403

        # Clean up
        teams_module._bg._event_store = None


class TestGetTeamRunEvents:
    def test_get_events_empty(self, teams_client: TestClient) -> None:
        teams_client.post("/api/v1/teams", json=TEAM_PAYLOAD)
        resp = teams_client.get("/api/v1/teams/test-team/runs/run-xyz/events")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "unknown"
        assert data["events"] == []

    def test_get_events_404(self, teams_client: TestClient) -> None:
        resp = teams_client.get("/api/v1/teams/nonexistent/runs/run-xyz/events")
        assert resp.status_code == 404

    def test_get_events_with_data(self, teams_client: TestClient) -> None:
        teams_client.post("/api/v1/teams", json=TEAM_PAYLOAD)

        teams_module._bg.get_events_async = AsyncMock(return_value=[{"type": "team_start"}, {"type": "done"}])
        teams_module._bg.get_status_async = AsyncMock(return_value="idle")

        resp = teams_client.get("/api/v1/teams/test-team/runs/run-xyz/events")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "idle"
        assert len(data["events"]) == 2


class TestGetTeamRun:
    def test_get_run_empty_tasks(self, teams_client: TestClient) -> None:
        teams_client.post("/api/v1/teams", json=TEAM_PAYLOAD)
        resp = teams_client.get("/api/v1/teams/test-team/runs/run-xyz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["team_run_id"] == "run-xyz"
        assert data["team_name"] == "test-team"
        assert data["status"] == "idle"
        assert data["tasks"] == []

    def test_get_run_404(self, teams_client: TestClient) -> None:
        resp = teams_client.get("/api/v1/teams/nonexistent/runs/run-xyz")
        assert resp.status_code == 404

    def test_get_run_with_tasks(self, teams_client: TestClient) -> None:
        teams_client.post("/api/v1/teams", json=TEAM_PAYLOAD)

        mock_task = MagicMock()
        mock_task.id = "t1"
        mock_task.title = "Do something"
        mock_task.status = "done"
        mock_task.assigned_to = "w"
        mock_task.suggested_worker = "w"
        mock_task.result = "Result here"
        mock_task.priority = 1

        with patch("agentic_primitives_gateway.routes.teams.registry") as mock_reg:
            mock_reg.tasks.list_tasks = AsyncMock(return_value=[mock_task])
            resp = teams_client.get("/api/v1/teams/test-team/runs/run-xyz")

        assert resp.status_code == 200
        data = resp.json()
        assert data["tasks_created"] == 1
        assert data["tasks_completed"] == 1
        assert data["tasks"][0]["title"] == "Do something"

    def test_get_run_with_active_run(self, teams_client: TestClient) -> None:
        teams_client.post("/api/v1/teams", json=TEAM_PAYLOAD)

        # Manually inject an active run
        loop = asyncio.new_event_loop()
        task = loop.create_task(asyncio.sleep(100))
        queue: asyncio.Queue[dict | None] = asyncio.Queue()
        import time

        teams_module._bg._runs["run-active"] = (task, queue, [], time.monotonic())

        resp = teams_client.get("/api/v1/teams/test-team/runs/run-active")
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"

        task.cancel()
        loop.close()

    def test_non_admin_denied_when_run_ownership_unknown(self, teams_client: TestClient) -> None:
        """``_require_run_owner`` default-denies on unknown ownership.

        A bob who has team access (team shared_with includes him or
        "*") and knows alice's team_run_id could otherwise read her
        task board in a window where the ownership record was evicted
        (e.g. replica restart without Redis event store).  The default
        flipped from allow to deny — admins still bypass, non-admins
        without a recorded match get 403.
        """
        from agentic_primitives_gateway.auth.base import AuthBackend
        from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal

        # Seed a team shared with everyone, so resolve_team_spec passes
        # and we isolate the _require_run_owner check itself.  The
        # team is created under the noop (admin) principal, so its
        # owner_id is "noop" — bob addresses it with the qualified
        # ``{owner}:{name}`` form.
        payload = {**TEAM_PAYLOAD, "shared_with": ["*"]}
        teams_client.post("/api/v1/teams", json=payload)

        class BobBackend(AuthBackend):
            async def authenticate(self, request):
                return AuthenticatedPrincipal(id="bob", type="user", scopes=frozenset())

        prev = getattr(app.state, "auth_backend", None)
        app.state.auth_backend = BobBackend()
        try:
            c = TestClient(app, raise_server_exceptions=False)
            # Qualified team name so resolve_team_spec locates the
            # spec bob doesn't own but has access to via shared_with.
            resp = c.get("/api/v1/teams/noop:test-team/runs/alice-run-id")
            assert resp.status_code == 403
        finally:
            app.state.auth_backend = prev
