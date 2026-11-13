"""Tests for resource-level access control."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from agentic_primitives_gateway.auth.access import (
    check_access,
    check_owner_or_admin,
    has_transitive_pool_access,
    require_access,
    require_owner_or_admin,
    require_pool_access,
    require_pool_delete,
    validate_agent_pool_references,
    validate_team_pool_reference,
)
from agentic_primitives_gateway.auth.models import ANONYMOUS_PRINCIPAL, AuthenticatedPrincipal
from agentic_primitives_gateway.models.agents import AgentSpec, PrimitiveConfig
from agentic_primitives_gateway.models.teams import TeamSpec


def _user(
    id: str = "alice",
    groups: frozenset[str] | None = None,
    scopes: frozenset[str] | None = None,
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        id=id,
        type="user",
        groups=groups or frozenset(),
        scopes=scopes or frozenset(),
    )


class TestCheckAccess:
    def test_owner_has_access(self):
        assert check_access(_user("alice"), "alice", []) is True

    def test_non_owner_no_groups_denied(self):
        assert check_access(_user("bob"), "alice", []) is False

    def test_wildcard_grants_access(self):
        assert check_access(_user("bob"), "alice", ["*"]) is True

    def test_group_membership_grants_access(self):
        p = _user("bob", groups=frozenset({"engineering"}))
        assert check_access(p, "alice", ["engineering"]) is True

    def test_wrong_group_denied(self):
        p = _user("bob", groups=frozenset({"marketing"}))
        assert check_access(p, "alice", ["engineering"]) is False

    def test_admin_scope_grants_access(self):
        p = _user("bob", scopes=frozenset({"admin"}))
        assert check_access(p, "alice", []) is True

    def test_anonymous_with_wildcard(self):
        assert check_access(ANONYMOUS_PRINCIPAL, "alice", ["*"]) is True

    def test_anonymous_without_wildcard(self):
        assert check_access(ANONYMOUS_PRINCIPAL, "alice", []) is False

    def test_multiple_groups_one_matches(self):
        p = _user("bob", groups=frozenset({"marketing", "engineering"}))
        assert check_access(p, "alice", ["engineering"]) is True

    def test_multiple_shared_groups_one_matches(self):
        p = _user("bob", groups=frozenset({"engineering"}))
        assert check_access(p, "alice", ["marketing", "engineering"]) is True


class TestCheckOwnerOrAdmin:
    def test_owner(self):
        assert check_owner_or_admin(_user("alice"), "alice") is True

    def test_non_owner(self):
        assert check_owner_or_admin(_user("bob"), "alice") is False

    def test_admin(self):
        p = _user("bob", scopes=frozenset({"admin"}))
        assert check_owner_or_admin(p, "alice") is True


class TestRequireAccess:
    def test_allowed_returns_principal(self):
        p = _user("alice")
        result = require_access(p, "alice", [])
        assert result is p

    def test_denied_raises_403(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            require_access(_user("bob"), "alice", [])
        assert exc_info.value.status_code == 403

    def test_none_principal_raises_403(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            require_access(None, "alice", [])
        assert exc_info.value.status_code == 403


class TestRequireOwnerOrAdmin:
    def test_owner_returns_principal(self):
        p = _user("alice")
        result = require_owner_or_admin(p, "alice")
        assert result is p

    def test_non_owner_raises_403(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            require_owner_or_admin(_user("bob"), "alice")
        assert exc_info.value.status_code == 403

    def test_admin_returns_principal(self):
        p = _user("bob", scopes=frozenset({"admin"}))
        result = require_owner_or_admin(p, "alice")
        assert result is p

    def test_none_principal_raises_403(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            require_owner_or_admin(None, "alice")
        assert exc_info.value.status_code == 403


# ── Transitive pool access ───────────────────────────────────────────


def _agent(name: str, owner: str, shared_with: list[str], pools: list[str] | None) -> AgentSpec:
    return AgentSpec(
        name=name,
        model="noop/stub",
        owner_id=owner,
        shared_with=shared_with,
        primitives={"memory": PrimitiveConfig(shared_namespaces=pools)},
    )


def _team(name: str, owner: str, shared_with: list[str], shared_memory: str | None) -> TeamSpec:
    return TeamSpec(
        name=name,
        planner="p",
        synthesizer="s",
        workers=["w"],
        owner_id=owner,
        shared_with=shared_with,
        shared_memory_namespace=shared_memory,
    )


class _FakeStore:
    """Minimal async store that mimics ``list_for_user`` ACL semantics."""

    def __init__(self, specs: list) -> None:
        self._specs = specs

    async def list(self) -> list:
        return list(self._specs)

    async def list_for_user(self, principal: AuthenticatedPrincipal) -> list:
        if principal.is_admin:
            return list(self._specs)
        return [
            s
            for s in self._specs
            if s.owner_id == principal.id or "*" in s.shared_with or principal.groups & set(s.shared_with)
        ]


class TestHasTransitivePoolAccess:
    async def test_admin_always_allowed(self):
        store = _FakeStore([])
        admin = _user("root", scopes=frozenset({"admin"}))
        assert await has_transitive_pool_access(
            "any-pool", principal=admin, agent_store=store, team_store=_FakeStore([])
        )

    async def test_owner_of_declaring_agent_allowed(self):
        agents = _FakeStore([_agent("a", owner="alice", shared_with=[], pools=["pool:alice:shared-p"])])
        teams = _FakeStore([])
        assert await has_transitive_pool_access(
            "pool:alice:shared-p", principal=_user("alice"), agent_store=agents, team_store=teams
        )

    async def test_sharee_of_declaring_agent_allowed(self):
        """Bob can reach pool P because he has access to Alice's shared agent."""
        agents = _FakeStore([_agent("a", owner="alice", shared_with=["*"], pools=["pool:alice:shared-p"])])
        teams = _FakeStore([])
        assert await has_transitive_pool_access(
            "pool:alice:shared-p", principal=_user("bob"), agent_store=agents, team_store=teams
        )

    async def test_unrelated_pool_denied(self):
        agents = _FakeStore([_agent("a", owner="alice", shared_with=["*"], pools=["other-p"])])
        teams = _FakeStore([])
        assert not await has_transitive_pool_access(
            "shared-p", principal=_user("bob"), agent_store=agents, team_store=teams
        )

    async def test_agent_without_memory_primitive_ignored(self):
        spec = AgentSpec(name="a", model="noop/stub", owner_id="alice", shared_with=["*"])
        agents = _FakeStore([spec])
        teams = _FakeStore([])
        assert not await has_transitive_pool_access(
            "shared-p", principal=_user("bob"), agent_store=agents, team_store=teams
        )

    async def test_orphan_pool_is_admin_only(self):
        """Pool exists in backend but no visible spec declares it → deny."""
        agents = _FakeStore([])
        teams = _FakeStore([])
        assert not await has_transitive_pool_access(
            "orphan", principal=_user("bob"), agent_store=agents, team_store=teams
        )

    async def test_team_declaring_pool_allowed(self):
        agents = _FakeStore([])
        teams = _FakeStore([_team("t", owner="alice", shared_with=["*"], shared_memory="team:alice:team-p")])
        assert await has_transitive_pool_access(
            "team:alice:team-p", principal=_user("bob"), agent_store=agents, team_store=teams
        )

    async def test_team_without_matching_pool_denied(self):
        agents = _FakeStore([])
        teams = _FakeStore([_team("t", owner="alice", shared_with=["*"], shared_memory="other")])
        assert not await has_transitive_pool_access(
            "team-p", principal=_user("bob"), agent_store=agents, team_store=teams
        )

    async def test_agent_not_visible_to_user_ignored(self):
        """Private agent of another user does not grant transitive access."""
        agents = _FakeStore([_agent("a", owner="alice", shared_with=[], pools=["pool:alice:private-p"])])
        teams = _FakeStore([])
        assert not await has_transitive_pool_access(
            "pool:alice:private-p", principal=_user("bob"), agent_store=agents, team_store=teams
        )

    async def test_unshared_declaration_cannot_claim_other_team_pool(self):
        agents = _FakeStore([_agent("unshared", "bob", [], ["team:alice:work"])])
        teams = _FakeStore([_team("victim", "alice", [], "team:alice:work")])
        assert not await has_transitive_pool_access(
            "team:alice:work", principal=_user("bob"), agent_store=agents, team_store=teams
        )
        with pytest.raises(HTTPException) as denied:
            await require_pool_delete("team:alice:work", principal=_user("bob"), agent_store=agents, team_store=teams)
        assert denied.value.status_code == 403

    async def test_declaring_unowned_legacy_literal_cannot_claim_rest_access(self):
        agents = _FakeStore([_agent("unshared", "bob", [], ["operator-pool"])])
        assert not await has_transitive_pool_access(
            "operator-pool", principal=_user("bob"), agent_store=agents, team_store=_FakeStore([])
        )

    async def test_operator_shared_pool_retains_legacy_namespace(self):
        agents = _FakeStore([_agent("seed", "system", ["*"], ["operator-pool"])])
        assert await has_transitive_pool_access(
            "operator-pool", principal=_user("bob"), agent_store=agents, team_store=_FakeStore([])
        )


class TestPoolReferenceValidation:
    async def test_rejects_undeclared_foreign_team_pool_and_legacy_name(self):
        agents = _FakeStore([])
        teams = _FakeStore([_team("victim", "alice", [], "team:alice:work")])
        for pool in ("team:alice:work", "operator-pool"):
            with pytest.raises(HTTPException) as denied:
                await validate_agent_pool_references(_agent("unshared", "bob", [], [pool]), _user("bob"), agents, teams)
            assert denied.value.status_code == 403

    async def test_owner_scoped_agent_and_team_pools_are_shareable_cross_user(self):
        agents = _FakeStore([])
        teams = _FakeStore([])
        agent = _agent("collaborator", "alice", ["*"], ["pool:{agent_owner}:collab"])
        team = _team("collab", "alice", ["*"], "team:{team_owner}:{team_name}")
        await validate_agent_pool_references(agent, _user("alice"), agents, teams)
        await validate_team_pool_reference(team, _user("alice"), agents, teams)
        agents._specs.append(agent)
        teams._specs.append(team)
        await validate_agent_pool_references(
            _agent("reader", "bob", [], ["pool:alice:collab"]), _user("bob"), agents, teams
        )
        await validate_team_pool_reference(_team("reader", "bob", [], "team:alice:collab"), _user("bob"), agents, teams)
        assert await has_transitive_pool_access(
            "team:alice:collab", principal=_user("bob"), agent_store=agents, team_store=teams
        )

    async def test_system_seed_grants_access_only_if_shared(self):
        agents = _FakeStore([_agent("system-agent", "system", ["*"], ["legacy:org-pool"])])
        teams = _FakeStore([])
        await validate_agent_pool_references(
            _agent("reader", "bob", [], ["legacy:org-pool"]), _user("bob"), agents, teams
        )
        agents._specs[0].shared_with = []
        with pytest.raises(HTTPException) as denied:
            await validate_agent_pool_references(
                _agent("reader", "bob", [], ["legacy:org-pool"]), _user("bob"), agents, teams
            )
        assert denied.value.status_code == 403

    async def test_untrusted_system_principal_cannot_claim_operator_pool(self):
        agents = _FakeStore([])
        teams = _FakeStore([])
        with pytest.raises(HTTPException) as denied:
            await validate_agent_pool_references(
                _agent("forged", "system", ["*"], ["legacy:org-pool"]),
                _user("system"),
                agents,
                teams,
            )
        assert denied.value.status_code == 403

    async def test_team_cannot_claim_foreign_unscoped_pool(self):
        agents = _FakeStore([])
        teams = _FakeStore([_team("private", "alice", [], "team:alice:work")])
        for pool in ("team:alice:work", "legacy:unscoped"):
            with pytest.raises(HTTPException) as denied:
                await validate_team_pool_reference(_team("unshared", "bob", [], pool), _user("bob"), agents, teams)
            assert denied.value.status_code == 403

    async def test_substituted_team_name_cannot_alias_private_memory(self):
        from agentic_primitives_gateway.agents.namespace import resolve_team_shared_namespace

        with pytest.raises(ValueError, match="must not contain"):
            resolve_team_shared_namespace(_team("victim:u:alice", "bob", [], "team:{team_name}"))

    async def test_private_pool_rejected_by_models(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PrimitiveConfig(shared_namespaces=["agent:victim:u:alice"])
        with pytest.raises(ValidationError):
            _team("unshared", "bob", [], "agent:victim:u:alice")

    async def test_ambiguous_owner_id_not_accepted_as_prefix(self):
        agents = _FakeStore([])
        teams = _FakeStore([])
        with pytest.raises(HTTPException) as denied:
            await validate_agent_pool_references(
                _agent("attack", "bob", [], ["pool:bob%3Aadmin:secrets"]),
                _user("bob"),
                agents,
                teams,
            )
        assert denied.value.status_code == 403
        await validate_agent_pool_references(
            _agent("safe", "bob:admin", [], ["pool:{agent_owner}:secrets"]),
            _user("bob:admin"),
            agents,
            teams,
        )


class TestRequirePoolAccess:
    async def test_raises_when_not_transitive(self):
        from fastapi import HTTPException

        agents = _FakeStore([])
        teams = _FakeStore([])
        with pytest.raises(HTTPException) as exc_info:
            await require_pool_access("p", principal=_user("bob"), agent_store=agents, team_store=teams)
        assert exc_info.value.status_code == 403

    async def test_returns_none_when_allowed(self):
        agents = _FakeStore([_agent("a", owner="bob", shared_with=[], pools=["pool:bob:p"])])
        teams = _FakeStore([])
        # should not raise
        await require_pool_access("pool:bob:p", principal=_user("bob"), agent_store=agents, team_store=teams)


class TestRequirePoolDelete:
    """Delete is stricter than access: the caller must OWN a declaring spec.

    Sharing an agent grants read/write on its pool, but dropping a key
    from the pool is a destructive op reserved to the agent's owner or
    admin — otherwise a sharee could wipe the owner's data.
    """

    async def test_admin_allowed(self):
        agents = _FakeStore([])
        teams = _FakeStore([])
        admin = _user("root", scopes=frozenset({"admin"}))
        await require_pool_delete("p", principal=admin, agent_store=agents, team_store=teams)

    async def test_owner_of_declaring_agent_allowed(self):
        agents = _FakeStore([_agent("a", owner="alice", shared_with=[], pools=["pool:alice:p"])])
        teams = _FakeStore([])
        await require_pool_delete("pool:alice:p", principal=_user("alice"), agent_store=agents, team_store=teams)

    async def test_sharee_of_declaring_agent_denied(self):
        """Bob has read/write via sharing but cannot delete."""
        from fastapi import HTTPException

        agents = _FakeStore([_agent("a", owner="alice", shared_with=["*"], pools=["pool:alice:p"])])
        teams = _FakeStore([])
        with pytest.raises(HTTPException) as exc_info:
            await require_pool_delete("pool:alice:p", principal=_user("bob"), agent_store=agents, team_store=teams)
        assert exc_info.value.status_code == 403

    async def test_owner_of_team_allowed(self):
        agents = _FakeStore([])
        teams = _FakeStore([_team("t", owner="alice", shared_with=[], shared_memory="team:alice:p")])
        await require_pool_delete("team:alice:p", principal=_user("alice"), agent_store=agents, team_store=teams)

    async def test_team_sharee_denied(self):
        from fastapi import HTTPException

        agents = _FakeStore([])
        teams = _FakeStore([_team("t", owner="alice", shared_with=["*"], shared_memory="team:alice:p")])
        with pytest.raises(HTTPException) as exc_info:
            await require_pool_delete("team:alice:p", principal=_user("bob"), agent_store=agents, team_store=teams)
        assert exc_info.value.status_code == 403
