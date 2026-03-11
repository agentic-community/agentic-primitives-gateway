from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from agentic_primitives_gateway.agents.team_store import FileTeamStore
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.models.teams import TeamRunPhase, TeamRunResponse
from agentic_primitives_gateway.routes.teams import set_team_store


@pytest.fixture(autouse=True)
def _init_team_store(tmp_path) -> None:
    store = FileTeamStore(path=str(tmp_path / "teams.json"))
    set_team_store(store)


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
