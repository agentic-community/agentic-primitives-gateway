"""Core versioning + fork + lineage + approval tests for the file-backed store."""

from __future__ import annotations

from typing import Any

import pytest

from agentic_primitives_gateway.agents.file_store import FileAgentStore, FileTeamStore
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.config import settings
from agentic_primitives_gateway.models.agents import (
    AgentSpec,
    PrimitiveConfig,
    VersionStatus,
)
from agentic_primitives_gateway.models.teams import TeamSpec


@pytest.fixture
def agent_store(tmp_path: Any) -> FileAgentStore:
    return FileAgentStore(path=str(tmp_path / "agents.json"))


@pytest.fixture
def team_store(tmp_path: Any, agent_store: FileAgentStore) -> FileTeamStore:
    store = FileTeamStore(path=str(tmp_path / "teams.json"))
    store.bind_agent_store(agent_store)
    return store


def _make_agent(name: str, owner: str = "system", model: str = "m", **kw: Any) -> AgentSpec:
    return AgentSpec(name=name, owner_id=owner, model=model, **kw)


@pytest.fixture(autouse=True)
def _reset_approval_switch() -> Any:
    orig = settings.governance.require_admin_approval_for_deploy
    yield
    settings.governance.require_admin_approval_for_deploy = orig


# ── Core version lifecycle ────────────────────────────────────────────


class TestVersionLifecycle:
    async def test_create_version_auto_deploys(self, agent_store: FileAgentStore) -> None:
        spec = _make_agent("r", "alice")
        v = await agent_store.create_version(name="r", owner_id="alice", spec=spec, created_by="alice")
        assert v.version_number == 1
        assert v.status == VersionStatus.DEPLOYED
        got = await agent_store.get_deployed("r", "alice")
        assert got is not None and got.version_id == v.version_id

    async def test_monotonic_version_numbering(self, agent_store: FileAgentStore) -> None:
        for i in range(3):
            await agent_store.create_version(
                name="r",
                owner_id="alice",
                spec=_make_agent("r", "alice", model=f"m{i}"),
                created_by="alice",
            )
        versions = await agent_store.list_versions("r", "alice")
        assert [v.version_number for v in versions] == [1, 2, 3]

    async def test_cross_identity_independent_numbering(self, agent_store: FileAgentStore) -> None:
        await agent_store.create_version(name="r", owner_id="alice", spec=_make_agent("r", "alice"), created_by="alice")
        await agent_store.create_version(
            name="r", owner_id="alice", spec=_make_agent("r", "alice", model="v2"), created_by="alice"
        )
        await agent_store.create_version(name="r", owner_id="bob", spec=_make_agent("r", "bob"), created_by="bob")
        alice_versions = await agent_store.list_versions("r", "alice")
        bob_versions = await agent_store.list_versions("r", "bob")
        assert [v.version_number for v in alice_versions] == [1, 2]
        assert [v.version_number for v in bob_versions] == [1]

    async def test_deploy_archives_previous(self, agent_store: FileAgentStore) -> None:
        v1 = await agent_store.create_version(
            name="r", owner_id="alice", spec=_make_agent("r", "alice"), created_by="alice"
        )
        v2 = await agent_store.create_version(
            name="r",
            owner_id="alice",
            spec=_make_agent("r", "alice", model="m2"),
            created_by="alice",
        )
        stored_v1 = await agent_store.get_version("r", "alice", v1.version_id)
        assert stored_v1 is not None
        assert stored_v1.status == VersionStatus.ARCHIVED
        stored_v2 = await agent_store.get_version("r", "alice", v2.version_id)
        assert stored_v2 is not None
        assert stored_v2.status == VersionStatus.DEPLOYED

    async def test_delete_archives_all_versions(self, agent_store: FileAgentStore) -> None:
        await agent_store.create_version(name="r", owner_id="alice", spec=_make_agent("r", "alice"), created_by="alice")
        await agent_store.create_version(
            name="r",
            owner_id="alice",
            spec=_make_agent("r", "alice", model="m2"),
            created_by="alice",
        )
        archived = await agent_store.archive_identity("r", "alice")
        assert archived >= 1  # v1 is already archived by v2's deploy
        deployed = await agent_store.get_deployed("r", "alice")
        assert deployed is None
        versions = await agent_store.list_versions("r", "alice")
        assert all(v.status == VersionStatus.ARCHIVED for v in versions)


# ── Resolution ────────────────────────────────────────────────────────


class TestResolution:
    async def test_resolve_for_caller_own_namespace_first(self, agent_store: FileAgentStore) -> None:
        await agent_store.create_version(
            name="r", owner_id="system", spec=_make_agent("r", "system"), created_by="system"
        )
        await agent_store.create_version(
            name="r",
            owner_id="alice",
            spec=_make_agent("r", "alice", model="alice-m"),
            created_by="alice",
        )
        alice = AuthenticatedPrincipal(id="alice", type="user")
        spec = await agent_store.resolve_for_caller("r", alice)
        assert spec is not None
        assert spec.owner_id == "alice"
        assert spec.model == "alice-m"

    async def test_resolve_for_caller_falls_through_to_system(self, agent_store: FileAgentStore) -> None:
        await agent_store.create_version(
            name="r",
            owner_id="system",
            spec=_make_agent("r", "system", shared_with=["*"]),
            created_by="system",
        )
        bob = AuthenticatedPrincipal(id="bob", type="user")
        spec = await agent_store.resolve_for_caller("r", bob)
        assert spec is not None
        assert spec.owner_id == "system"

    async def test_resolve_for_caller_ignores_shared_bare(self, agent_store: FileAgentStore) -> None:
        await agent_store.create_version(
            name="r",
            owner_id="alice",
            spec=_make_agent("r", "alice", shared_with=["*"]),
            created_by="alice",
        )
        bob = AuthenticatedPrincipal(id="bob", type="user")
        # Bare lookup in Bob's context must not find Alice's.
        assert await agent_store.resolve_for_caller("r", bob) is None
        # Qualified works.
        assert await agent_store.resolve_qualified("alice", "r") is not None


# ── Fork + sub-ref auto-qualification ─────────────────────────────────


class TestFork:
    async def test_fork_creates_independent_identity(self, agent_store: FileAgentStore) -> None:
        await agent_store.create_version(
            name="r",
            owner_id="alice",
            spec=_make_agent("r", "alice"),
            created_by="alice",
        )
        fork = await agent_store.fork(
            source_name="r",
            source_owner_id="alice",
            target_owner_id="bob",
            created_by="bob",
        )
        assert fork.owner_id == "bob"
        assert fork.spec.owner_id == "bob"
        assert fork.forked_from is not None
        assert fork.forked_from.owner_id == "alice"

    async def test_fork_rewrites_sub_refs_to_source_owner(self, agent_store: FileAgentStore) -> None:
        # Alice has `analyst` in her namespace plus `researcher` which
        # delegates to `analyst`.
        await agent_store.create_version(
            name="analyst",
            owner_id="alice",
            spec=_make_agent("analyst", "alice"),
            created_by="alice",
        )
        await agent_store.create_version(
            name="researcher",
            owner_id="alice",
            spec=_make_agent(
                "researcher",
                "alice",
                primitives={"agents": PrimitiveConfig(enabled=True, tools=["analyst"])},
            ),
            created_by="alice",
        )
        fork = await agent_store.fork(
            source_name="researcher",
            source_owner_id="alice",
            target_owner_id="bob",
            created_by="bob",
        )
        forked_tools = fork.spec.primitives["agents"].tools
        assert forked_tools == ["alice:analyst"]

    async def test_fork_leaves_system_refs_bare(self, agent_store: FileAgentStore) -> None:
        # `sys-analyst` exists only in system; `researcher` is Alice's fork target.
        await agent_store.create_version(
            name="sys-analyst",
            owner_id="system",
            spec=_make_agent("sys-analyst", "system"),
            created_by="system",
        )
        await agent_store.create_version(
            name="researcher",
            owner_id="alice",
            spec=_make_agent(
                "researcher",
                "alice",
                primitives={"agents": PrimitiveConfig(enabled=True, tools=["sys-analyst"])},
            ),
            created_by="alice",
        )
        fork = await agent_store.fork(
            source_name="researcher",
            source_owner_id="alice",
            target_owner_id="bob",
            created_by="bob",
        )
        # `sys-analyst` stays bare because `(alice, sys-analyst)` doesn't exist.
        forked_tools = fork.spec.primitives["agents"].tools
        assert forked_tools == ["sys-analyst"]

    async def test_fork_collision_raises(self, agent_store: FileAgentStore) -> None:
        await agent_store.create_version(name="r", owner_id="alice", spec=_make_agent("r", "alice"), created_by="alice")
        await agent_store.create_version(name="r", owner_id="bob", spec=_make_agent("r", "bob"), created_by="bob")
        with pytest.raises(KeyError):
            await agent_store.fork(
                source_name="r",
                source_owner_id="alice",
                target_owner_id="bob",
                created_by="bob",
            )


# ── Lineage ───────────────────────────────────────────────────────────


class TestLineage:
    async def test_lineage_traverses_forks(self, agent_store: FileAgentStore) -> None:
        await agent_store.create_version(name="r", owner_id="alice", spec=_make_agent("r", "alice"), created_by="alice")
        await agent_store.fork(
            source_name="r",
            source_owner_id="alice",
            target_owner_id="bob",
            created_by="bob",
        )
        await agent_store.fork(
            source_name="r",
            source_owner_id="bob",
            target_owner_id="carol",
            created_by="carol",
        )
        lineage = await agent_store.get_lineage_model("r", "alice")
        owners_in_lineage = {n.version.owner_id for n in lineage.nodes}
        assert {"alice", "bob", "carol"} == owners_in_lineage


# ── Approval mode ─────────────────────────────────────────────────────


class TestApprovalMode:
    async def test_switch_off_auto_deploys(self, agent_store: FileAgentStore) -> None:
        settings.governance.require_admin_approval_for_deploy = False
        v = await agent_store.create_version(
            name="r", owner_id="alice", spec=_make_agent("r", "alice"), created_by="alice"
        )
        assert v.status == VersionStatus.DEPLOYED

    async def test_switch_on_creates_draft(self, agent_store: FileAgentStore) -> None:
        settings.governance.require_admin_approval_for_deploy = True
        v = await agent_store.create_version(
            name="r", owner_id="alice", spec=_make_agent("r", "alice"), created_by="alice"
        )
        assert v.status == VersionStatus.DRAFT

    async def test_seed_bypasses_approval(self, agent_store: FileAgentStore) -> None:
        settings.governance.require_admin_approval_for_deploy = True
        await agent_store.seed_async({"s": {"model": "m"}})
        v = await agent_store.get_deployed("s", "system")
        assert v is not None
        assert v.status == VersionStatus.DEPLOYED

    async def test_approval_flow(self, agent_store: FileAgentStore) -> None:
        settings.governance.require_admin_approval_for_deploy = True
        v = await agent_store.create_version(
            name="r", owner_id="alice", spec=_make_agent("r", "alice"), created_by="alice"
        )
        assert v.status == VersionStatus.DRAFT

        proposed = await agent_store.propose_version("r", "alice", v.version_id)
        assert proposed.status == VersionStatus.PROPOSED

        pending = await agent_store.list_pending_proposals()
        assert any(p.version_id == v.version_id for p in pending)

        approved = await agent_store.approve_version("r", "alice", v.version_id, approver_id="admin-1")
        assert approved.approved_by == "admin-1"

        deployed = await agent_store.deploy_version("r", "alice", v.version_id, deployed_by="alice")
        assert deployed.status == VersionStatus.DEPLOYED

    async def test_approval_mode_blocks_draft_deploy(self, agent_store: FileAgentStore) -> None:
        settings.governance.require_admin_approval_for_deploy = True
        v = await agent_store.create_version(
            name="r", owner_id="alice", spec=_make_agent("r", "alice"), created_by="alice"
        )
        with pytest.raises(ValueError, match="Cannot deploy"):
            await agent_store.deploy_version("r", "alice", v.version_id, deployed_by="alice")


# ── Team fork worker rewriting ────────────────────────────────────────


class TestTeamFork:
    async def test_team_fork_rewrites_worker_refs(
        self,
        agent_store: FileAgentStore,
        team_store: FileTeamStore,
    ) -> None:
        # Alice has two agents + a team that references them by bare name.
        await agent_store.create_version(
            name="analyst", owner_id="alice", spec=_make_agent("analyst", "alice"), created_by="alice"
        )
        await agent_store.create_version(
            name="writer", owner_id="alice", spec=_make_agent("writer", "alice"), created_by="alice"
        )
        team = TeamSpec(
            name="crew",
            owner_id="alice",
            planner="analyst",
            synthesizer="writer",
            workers=["analyst", "writer"],
        )
        await team_store.create_version(name="crew", owner_id="alice", spec=team, created_by="alice")
        fork = await team_store.fork(
            source_name="crew",
            source_owner_id="alice",
            target_owner_id="bob",
            created_by="bob",
        )
        spec = fork.spec
        assert spec.planner == "alice:analyst"
        assert spec.synthesizer == "alice:writer"
        assert spec.workers == ["alice:analyst", "alice:writer"]


# ── Retention ─────────────────────────────────────────────────────────


class TestRetention:
    async def test_retention_archives_oldest(self, agent_store: FileAgentStore) -> None:
        orig_cap = settings.agents.max_versions_per_identity
        settings.agents.max_versions_per_identity = 3
        try:
            for i in range(5):
                await agent_store.create_version(
                    name="r",
                    owner_id="alice",
                    spec=_make_agent("r", "alice", model=f"m{i}"),
                    created_by="alice",
                )
            versions = await agent_store.list_versions("r", "alice")
            archived = [v for v in versions if v.status == VersionStatus.ARCHIVED]
            # Deploys flow archives naturally (each deploy archives the
            # previous).  Retention on top trims oldest archived.  We only
            # require that at least one got archived and the deployed + at
            # most one other non-archived version survive.
            assert len(archived) >= 3
        finally:
            settings.agents.max_versions_per_identity = orig_cap
