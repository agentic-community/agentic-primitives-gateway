"""Coverage for team versioning routes — version lifecycle, fork, lineage,
approval gate.  Mirrors ``tests/unit/agents/test_version_routes.py`` for the
team equivalent endpoints.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from agentic_primitives_gateway.agents.file_store import FileAgentStore, FileTeamStore
from agentic_primitives_gateway.config import settings
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.routes.agents import set_agent_store
from agentic_primitives_gateway.routes.teams import set_team_store


@pytest.fixture(autouse=True)
def _reset_switch() -> Any:
    orig = settings.governance.require_admin_approval_for_deploy
    yield
    settings.governance.require_admin_approval_for_deploy = orig


@pytest.fixture
async def stores(tmp_path: Any) -> tuple[FileAgentStore, FileTeamStore]:
    agent_store = FileAgentStore(path=str(tmp_path / "agents.json"))
    team_store = FileTeamStore(path=str(tmp_path / "teams.json"))
    team_store.bind_agent_store(agent_store)
    set_agent_store(agent_store)
    set_team_store(team_store)
    return agent_store, team_store


@pytest.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_team(client: AsyncClient, name: str = "crew") -> dict[str, Any]:
    # Seed referenced agents so team validation passes.
    for agent in ("planner", "synthesizer", "worker1"):
        resp = await client.post(
            "/api/v1/agents",
            json={
                "name": agent,
                "model": "m",
                "system_prompt": "p",
                "primitives": {"memory": {"enabled": False}},
            },
        )
        assert resp.status_code in (201, 409), resp.text

    resp = await client.post(
        "/api/v1/teams",
        json={
            "name": name,
            "planner": "planner",
            "synthesizer": "synthesizer",
            "workers": ["worker1"],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestTeamVersions:
    async def test_list_versions_returns_deployed_v1(self, stores, client):
        await _seed_team(client, "t1")
        resp = await client.get("/api/v1/teams/t1/versions")
        assert resp.status_code == 200
        versions = resp.json()["versions"]
        assert len(versions) == 1
        assert versions[0]["status"] == "deployed"
        assert versions[0]["version_number"] == 1

    async def test_get_specific_version(self, stores, client):
        await _seed_team(client, "t2")
        list_resp = await client.get("/api/v1/teams/t2/versions")
        vid = list_resp.json()["versions"][0]["version_id"]
        resp = await client.get(f"/api/v1/teams/t2/versions/{vid}")
        assert resp.status_code == 200
        assert resp.json()["version_id"] == vid

    async def test_create_version_auto_deploys_when_switch_off(self, stores, client):
        await _seed_team(client, "t3")
        resp = await client.post(
            "/api/v1/teams/t3/versions",
            json={"commit_message": "tweak", "workers": ["worker1"]},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "deployed"
        assert body["version_number"] == 2
        assert body["commit_message"] == "tweak"

    async def test_create_version_stays_draft_when_switch_on(self, stores, client):
        await _seed_team(client, "t4")
        settings.governance.require_admin_approval_for_deploy = True
        resp = await client.post(
            "/api/v1/teams/t4/versions",
            json={"commit_message": "needs review", "workers": ["worker1"]},
        )
        assert resp.status_code == 201
        assert resp.json()["status"] == "draft"

    async def test_put_rejected_with_409_under_approval_gate(self, stores, client):
        await _seed_team(client, "t5")
        settings.governance.require_admin_approval_for_deploy = True
        resp = await client.put(
            "/api/v1/teams/t5",
            json={"description": "new"},
        )
        assert resp.status_code == 409
        assert "versions_url" in resp.json()["detail"]


class TestTeamApprovalFlow:
    async def test_propose_approve_deploy_cycle(self, stores, client):
        await _seed_team(client, "t6")
        settings.governance.require_admin_approval_for_deploy = True

        # Draft
        create = await client.post(
            "/api/v1/teams/t6/versions",
            json={"commit_message": "v2", "workers": ["worker1"]},
        )
        vid = create.json()["version_id"]
        assert create.json()["status"] == "draft"

        # Propose
        prop = await client.post(f"/api/v1/teams/t6/versions/{vid}/propose")
        assert prop.status_code == 200
        assert prop.json()["status"] == "proposed"

        # Approve (records approver; status stays "proposed" until deploy)
        appr = await client.post(f"/api/v1/teams/t6/versions/{vid}/approve")
        assert appr.status_code == 200
        assert appr.json()["approved_by"] is not None

        # Deploy
        dep = await client.post(f"/api/v1/teams/t6/versions/{vid}/deploy")
        assert dep.status_code == 200
        assert dep.json()["status"] == "deployed"

    async def test_reject_version(self, stores, client):
        await _seed_team(client, "t7")
        settings.governance.require_admin_approval_for_deploy = True
        create = await client.post(
            "/api/v1/teams/t7/versions",
            json={"commit_message": "bad", "workers": ["worker1"]},
        )
        vid = create.json()["version_id"]
        await client.post(f"/api/v1/teams/t7/versions/{vid}/propose")

        resp = await client.post(
            f"/api/v1/teams/t7/versions/{vid}/reject",
            json={"reason": "missing coverage"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"


class TestTeamFork:
    async def test_fork_creates_new_identity(self, stores, client):
        await _seed_team(client, "upstream")
        resp = await client.post(
            "/api/v1/teams/upstream/fork",
            json={"target_name": "mine", "commit_message": "forked"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["team_name"] == "mine"
        assert body["forked_from"]["name"] == "upstream"

    async def test_lineage_shows_fork_edge(self, stores, client):
        await _seed_team(client, "source")
        await client.post(
            "/api/v1/teams/source/fork",
            json={"target_name": "fork1"},
        )
        resp = await client.get("/api/v1/teams/source/lineage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["root_identity"]["name"] == "source"
        # At least one node with a forks_out entry
        assert any(n["forks_out"] for n in data["nodes"])


class TestTeamExport:
    async def test_export_returns_python_script(self, stores, client):
        await _seed_team(client, "exportable")
        resp = await client.get("/api/v1/teams/exportable/export")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/x-python")
        assert "exportable" in resp.text
