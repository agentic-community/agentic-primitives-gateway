"""Route-level tests for agent versioning / fork / lineage / approval."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from agentic_primitives_gateway.agents.store import FileAgentStore
from agentic_primitives_gateway.config import settings
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.routes.agents import set_agent_store


@pytest.fixture(autouse=True)
def _reset_switch() -> Any:
    orig = settings.governance.require_admin_approval_for_deploy
    yield
    settings.governance.require_admin_approval_for_deploy = orig


@pytest.fixture
async def store(tmp_path: Any) -> FileAgentStore:
    s = FileAgentStore(path=str(tmp_path / "agents.json"))
    set_agent_store(s)
    return s


@pytest.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _create(client: AsyncClient, name: str, **overrides: Any) -> dict[str, Any]:
    body = {
        "name": name,
        "model": "m",
        "system_prompt": "hi",
        "primitives": {"memory": {"enabled": False}},
    }
    body.update(overrides)
    resp = await client.post("/api/v1/agents", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestCreateVersion:
    async def test_create_version_auto_deploys_when_switch_off(
        self, store: FileAgentStore, client: AsyncClient
    ) -> None:
        await _create(client, "r")
        resp = await client.post(
            "/api/v1/agents/r/versions",
            json={"description": "v2", "commit_message": "tweak"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "deployed"
        assert body["version_number"] == 2

        get = await client.get("/api/v1/agents/r")
        assert get.status_code == 200
        assert get.json()["description"] == "v2"

    async def test_put_returns_409_under_approval_gate(self, store: FileAgentStore, client: AsyncClient) -> None:
        await _create(client, "r")
        settings.governance.require_admin_approval_for_deploy = True

        resp = await client.put(
            "/api/v1/agents/r",
            json={"description": "hacked"},
        )
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert "versions_url" in detail


class TestApprovalFlow:
    async def test_propose_approve_deploy(self, store: FileAgentStore, client: AsyncClient) -> None:
        await _create(client, "r")
        settings.governance.require_admin_approval_for_deploy = True

        # New version lands as draft under the gate
        resp = await client.post(
            "/api/v1/agents/r/versions",
            json={"description": "v2", "commit_message": "tweak"},
        )
        assert resp.status_code == 201
        v = resp.json()
        assert v["status"] == "draft"
        version_id = v["version_id"]

        # Deployed still points at v1
        deployed = await client.get("/api/v1/agents/r")
        assert deployed.json()["description"] != "v2"

        # Propose
        resp = await client.post(f"/api/v1/agents/r/versions/{version_id}/propose")
        assert resp.status_code == 200
        assert resp.json()["status"] == "proposed"

        # Approve (noop auth principal is admin)
        resp = await client.post(f"/api/v1/agents/r/versions/{version_id}/approve")
        assert resp.status_code == 200

        # Deploy
        resp = await client.post(f"/api/v1/agents/r/versions/{version_id}/deploy")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deployed"

        # Deployed now points at v2
        deployed = await client.get("/api/v1/agents/r")
        assert deployed.json()["description"] == "v2"


class TestFork:
    async def test_fork_creates_identity_in_caller_namespace(self, store: FileAgentStore, client: AsyncClient) -> None:
        # Noop auth uses principal id "noop".  Seed an agent "in system" so
        # we can fork it to ``noop``.
        await store.seed_async({"source": {"model": "m", "description": "original"}})

        # Fork from system to the caller's namespace.
        resp = await client.post(
            "/api/v1/agents/system:source/fork",
            json={"target_name": "my-source"},
        )
        assert resp.status_code == 201, resp.text
        version = resp.json()
        assert version["owner_id"] == "noop"
        assert version["agent_name"] == "my-source"
        assert version["forked_from"]["owner_id"] == "system"


class TestVersionsList:
    async def test_list_versions_returns_history(self, store: FileAgentStore, client: AsyncClient) -> None:
        await _create(client, "r")
        await client.post("/api/v1/agents/r/versions", json={"description": "v2"})
        await client.post("/api/v1/agents/r/versions", json={"description": "v3"})

        resp = await client.get("/api/v1/agents/r/versions")
        assert resp.status_code == 200
        versions = resp.json()["versions"]
        assert [v["version_number"] for v in versions] == [1, 2, 3]


class TestLineage:
    async def test_lineage_includes_fork(self, store: FileAgentStore, client: AsyncClient) -> None:
        await _create(client, "r")
        await client.post("/api/v1/agents/r/versions", json={"description": "v2"})
        # Fork noop's r back into system namespace via admin override.
        resp = await client.post("/api/v1/agents/r/fork", json={"target_name": "r-fork"})
        assert resp.status_code == 201

        lineage = await client.get("/api/v1/agents/r/lineage")
        assert lineage.status_code == 200
        nodes = lineage.json()["nodes"]
        owners = {n["version"]["owner_id"] for n in nodes}
        assert "noop" in owners
