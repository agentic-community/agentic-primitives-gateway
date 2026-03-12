"""Tests for ownership enforcement in agent and team routes."""

from __future__ import annotations

import pytest

from agentic_primitives_gateway.auth.api_key import ApiKeyAuthBackend
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import set_authenticated_principal
from agentic_primitives_gateway.models.agents import AgentSpec


def _make_backend() -> ApiKeyAuthBackend:
    return ApiKeyAuthBackend(
        api_keys=[
            {
                "key": "sk-alice",
                "principal_id": "alice",
                "principal_type": "user",
                "groups": ["engineering"],
                "scopes": [],
            },
            {
                "key": "sk-bob",
                "principal_id": "bob",
                "principal_type": "user",
                "groups": ["marketing"],
                "scopes": [],
            },
            {
                "key": "sk-admin",
                "principal_id": "admin-user",
                "principal_type": "user",
                "groups": [],
                "scopes": ["admin"],
            },
        ]
    )


@pytest.fixture()
def agent_store(tmp_path):
    from agentic_primitives_gateway.agents.store import FileAgentStore

    return FileAgentStore(path=str(tmp_path / "agents.json"))


@pytest.fixture()
def team_store(tmp_path):
    from agentic_primitives_gateway.agents.team_store import FileTeamStore

    return FileTeamStore(path=str(tmp_path / "teams.json"))


class TestAgentOwnership:
    @pytest.mark.asyncio
    async def test_create_sets_owner_id(self, agent_store):
        """Creating an agent sets owner_id from the authenticated principal."""
        from agentic_primitives_gateway.routes import agents as agent_routes

        original_store = agent_routes._store
        agent_routes._store = agent_store

        try:
            # Simulate authenticated request
            set_authenticated_principal(AuthenticatedPrincipal(id="alice", type="user"))
            from agentic_primitives_gateway.models.agents import CreateAgentRequest

            request = CreateAgentRequest(name="my-agent", model="test-model")
            result = await agent_routes.create_agent(request)
            assert result.owner_id == "alice"
            assert result.shared_with == []
        finally:
            agent_routes._store = original_store

    @pytest.mark.asyncio
    async def test_create_with_shared_with(self, agent_store):
        """Creating an agent respects shared_with from the request."""
        from agentic_primitives_gateway.routes import agents as agent_routes

        original_store = agent_routes._store
        agent_routes._store = agent_store

        try:
            set_authenticated_principal(AuthenticatedPrincipal(id="alice", type="user"))
            from agentic_primitives_gateway.models.agents import CreateAgentRequest

            request = CreateAgentRequest(
                name="shared-agent",
                model="test-model",
                shared_with=["engineering"],
            )
            result = await agent_routes.create_agent(request)
            assert result.owner_id == "alice"
            assert result.shared_with == ["engineering"]
        finally:
            agent_routes._store = original_store

    @pytest.mark.asyncio
    async def test_list_filters_by_access(self, agent_store):
        """list_for_user only returns agents the user can access."""
        # Create agents with different owners
        await agent_store.create(AgentSpec(name="alice-agent", model="m", owner_id="alice", shared_with=[]))
        await agent_store.create(AgentSpec(name="bob-agent", model="m", owner_id="bob", shared_with=[]))
        await agent_store.create(
            AgentSpec(name="shared-agent", model="m", owner_id="carol", shared_with=["engineering"])
        )
        await agent_store.create(AgentSpec(name="public-agent", model="m", owner_id="system", shared_with=["*"]))

        alice = AuthenticatedPrincipal(id="alice", type="user", groups=frozenset({"engineering"}))
        bob = AuthenticatedPrincipal(id="bob", type="user", groups=frozenset())

        alice_agents = await agent_store.list_for_user(alice)
        alice_names = {a.name for a in alice_agents}
        assert "alice-agent" in alice_names  # owner
        assert "shared-agent" in alice_names  # group access
        assert "public-agent" in alice_names  # wildcard
        assert "bob-agent" not in alice_names  # no access

        bob_agents = await agent_store.list_for_user(bob)
        bob_names = {a.name for a in bob_agents}
        assert "bob-agent" in bob_names  # owner
        assert "public-agent" in bob_names  # wildcard
        assert "alice-agent" not in bob_names  # no access
        assert "shared-agent" not in bob_names  # not in engineering

    @pytest.mark.asyncio
    async def test_get_checks_access(self, agent_store):
        """get_agent returns 403 if the user doesn't have access."""
        await agent_store.create(AgentSpec(name="private-agent", model="m", owner_id="alice", shared_with=[]))

        from agentic_primitives_gateway.routes import agents as agent_routes

        original_store = agent_routes._store
        agent_routes._store = agent_store

        try:
            # Bob tries to access Alice's private agent
            set_authenticated_principal(AuthenticatedPrincipal(id="bob", type="user"))
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as exc_info:
                await agent_routes.get_agent("private-agent")
            assert exc_info.value.status_code == 403
        finally:
            agent_routes._store = original_store

    @pytest.mark.asyncio
    async def test_update_requires_owner(self, agent_store):
        """Only the owner (or admin) can update an agent."""
        await agent_store.create(
            AgentSpec(
                name="alice-agent",
                model="m",
                owner_id="alice",
                shared_with=["engineering"],
            )
        )

        from agentic_primitives_gateway.routes import agents as agent_routes

        original_store = agent_routes._store
        agent_routes._store = agent_store

        try:
            # Bob is in engineering (can use) but not owner (can't edit)
            set_authenticated_principal(
                AuthenticatedPrincipal(id="bob", type="user", groups=frozenset({"engineering"}))
            )
            from fastapi import HTTPException

            from agentic_primitives_gateway.models.agents import UpdateAgentRequest

            with pytest.raises(HTTPException) as exc_info:
                await agent_routes.update_agent(
                    "alice-agent",
                    UpdateAgentRequest(description="hacked"),
                )
            assert exc_info.value.status_code == 403
        finally:
            agent_routes._store = original_store

    @pytest.mark.asyncio
    async def test_delete_requires_owner(self, agent_store):
        """Only the owner (or admin) can delete an agent."""
        await agent_store.create(AgentSpec(name="alice-agent", model="m", owner_id="alice", shared_with=["*"]))

        from agentic_primitives_gateway.routes import agents as agent_routes

        original_store = agent_routes._store
        agent_routes._store = agent_store

        try:
            set_authenticated_principal(AuthenticatedPrincipal(id="bob", type="user"))
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as exc_info:
                await agent_routes.delete_agent("alice-agent")
            assert exc_info.value.status_code == 403
        finally:
            agent_routes._store = original_store

    @pytest.mark.asyncio
    async def test_admin_can_update_any(self, agent_store):
        """Admin scope grants update access to any agent."""
        await agent_store.create(AgentSpec(name="alice-agent", model="m", owner_id="alice", shared_with=[]))

        from agentic_primitives_gateway.routes import agents as agent_routes

        original_store = agent_routes._store
        agent_routes._store = agent_store

        try:
            set_authenticated_principal(
                AuthenticatedPrincipal(id="admin-user", type="user", scopes=frozenset({"admin"}))
            )
            from agentic_primitives_gateway.models.agents import UpdateAgentRequest

            result = await agent_routes.update_agent(
                "alice-agent",
                UpdateAgentRequest(description="admin edit"),
            )
            assert result.description == "admin edit"
        finally:
            agent_routes._store = original_store

    @pytest.mark.asyncio
    async def test_admin_can_delete_any(self, agent_store):
        """Admin scope grants delete access to any agent."""
        await agent_store.create(AgentSpec(name="alice-agent", model="m", owner_id="alice", shared_with=[]))

        from agentic_primitives_gateway.routes import agents as agent_routes

        original_store = agent_routes._store
        agent_routes._store = agent_store

        try:
            set_authenticated_principal(
                AuthenticatedPrincipal(id="admin-user", type="user", scopes=frozenset({"admin"}))
            )
            result = await agent_routes.delete_agent("alice-agent")
            assert result == {"status": "deleted"}
        finally:
            agent_routes._store = original_store


class TestAgentSpecDefaults:
    def test_default_is_private(self):
        """New agents default to private (empty shared_with)."""
        spec = AgentSpec(name="test", model="m")
        assert spec.owner_id == "system"
        assert spec.shared_with == []

    def test_custom_ownership(self):
        spec = AgentSpec(
            name="test",
            model="m",
            owner_id="alice",
            shared_with=["engineering"],
        )
        assert spec.owner_id == "alice"
        assert spec.shared_with == ["engineering"]

    def test_explicit_wildcard(self):
        """Wildcard must be explicitly set."""
        spec = AgentSpec(name="test", model="m", shared_with=["*"])
        assert spec.shared_with == ["*"]


class TestTeamSpecDefaults:
    def test_default_is_private(self):
        from agentic_primitives_gateway.models.teams import TeamSpec

        spec = TeamSpec(name="t", planner="p", synthesizer="s", workers=["w"])
        assert spec.owner_id == "system"
        assert spec.shared_with == []


class TestSeedInjectsWildcard:
    def test_file_agent_store_seed_adds_wildcard(self, agent_store):
        """Seeding from config injects shared_with=['*'] by default."""
        agent_store.seed({"seeded": {"model": "m"}})

        import asyncio

        spec = asyncio.get_event_loop().run_until_complete(agent_store.get("seeded"))
        assert spec is not None
        assert spec.shared_with == ["*"]
        assert spec.owner_id == "system"

    def test_file_agent_store_seed_respects_explicit(self, agent_store):
        """Seeding from config respects explicitly set shared_with."""
        agent_store.seed({"private": {"model": "m", "shared_with": ["engineering"]}})

        import asyncio

        spec = asyncio.get_event_loop().run_until_complete(agent_store.get("private"))
        assert spec is not None
        assert spec.shared_with == ["engineering"]

    def test_file_team_store_seed_adds_wildcard(self, team_store):
        """Seeding teams from config injects shared_with=['*'] by default."""
        team_store.seed({"t": {"planner": "p", "synthesizer": "s", "workers": ["w"]}})

        import asyncio

        spec = asyncio.get_event_loop().run_until_complete(team_store.get("t"))
        assert spec is not None
        assert spec.shared_with == ["*"]
