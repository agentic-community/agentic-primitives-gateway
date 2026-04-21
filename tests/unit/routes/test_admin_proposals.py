"""Cover the admin proposals routes — cross-namespace review queue for
agent + team pending version proposals under the approval gate."""

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


class TestAdminAgentProposals:
    async def test_empty_queue(self, stores, client):
        resp = await client.get("/api/v1/admin/agents/proposals")
        assert resp.status_code == 200
        assert resp.json()["versions"] == []

    async def test_pending_proposals_listed(self, stores, client):
        # Seed an agent with switch off so v1 auto-deploys, then flip the
        # switch on to create a draft, propose it, and check the admin queue.
        await client.post(
            "/api/v1/agents",
            json={
                "name": "r",
                "model": "m",
                "system_prompt": "hi",
                "primitives": {"memory": {"enabled": False}},
            },
        )
        settings.governance.require_admin_approval_for_deploy = True
        create = await client.post(
            "/api/v1/agents/r/versions",
            json={"description": "v2", "commit_message": "tweak"},
        )
        vid = create.json()["version_id"]
        await client.post(f"/api/v1/agents/r/versions/{vid}/propose")

        resp = await client.get("/api/v1/admin/agents/proposals")
        assert resp.status_code == 200
        versions = resp.json()["versions"]
        assert any(v["version_id"] == vid for v in versions)


class TestAdminTeamProposals:
    async def test_empty_queue(self, stores, client):
        resp = await client.get("/api/v1/admin/teams/proposals")
        assert resp.status_code == 200
        assert resp.json()["versions"] == []

    async def test_pending_team_proposals_listed(self, stores, client):
        for agent in ("planner", "synthesizer", "worker1"):
            await client.post(
                "/api/v1/agents",
                json={
                    "name": agent,
                    "model": "m",
                    "system_prompt": "p",
                    "primitives": {"memory": {"enabled": False}},
                },
            )
        await client.post(
            "/api/v1/teams",
            json={
                "name": "crew",
                "planner": "planner",
                "synthesizer": "synthesizer",
                "workers": ["worker1"],
            },
        )
        settings.governance.require_admin_approval_for_deploy = True
        create = await client.post(
            "/api/v1/teams/crew/versions",
            json={"commit_message": "v2", "workers": ["worker1"]},
        )
        vid = create.json()["version_id"]
        await client.post(f"/api/v1/teams/crew/versions/{vid}/propose")

        resp = await client.get("/api/v1/admin/teams/proposals")
        assert resp.status_code == 200
        assert any(v["version_id"] == vid for v in resp.json()["versions"])
