"""Route-level ownership tests for teams.

Mirrors ``tests/unit/auth/test_ownership.py`` which already covers the
agents side.  Intent: a user who is neither the owner, in a shared
group, nor an admin cannot read, update, or delete another user's
team — verified against the real route handlers, not just the store.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentic_primitives_gateway.agents.file_store import FileTeamStore
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import set_authenticated_principal
from agentic_primitives_gateway.models.teams import TeamSpec


@pytest.fixture
async def team_store(tmp_path: Any) -> FileTeamStore:
    return FileTeamStore(path=str(tmp_path / "teams.json"))


class TestTeamOwnership:
    @pytest.mark.asyncio
    async def test_get_checks_access(self, team_store):
        """Bob cannot read Alice's private team (qualified → 403, bare → 404).

        Matches agent behavior in ``test_ownership.py::test_get_checks_access``:
        bare lookup returns 404 because caller-scoped resolution only
        looks in Bob's and the system namespace; qualified lookup
        (``alice:private-team``) runs the share rules and returns 403.
        """
        await team_store.create(
            TeamSpec(
                name="private-team",
                planner="p",
                synthesizer="s",
                workers=["w"],
                owner_id="alice",
                shared_with=[],
            )
        )

        from agentic_primitives_gateway.routes import teams as team_routes

        original_store = team_routes._store
        team_routes._store = team_store

        try:
            set_authenticated_principal(AuthenticatedPrincipal(id="bob", type="user"))
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as exc_info:
                await team_routes.get_team("private-team")
            assert exc_info.value.status_code == 404

            with pytest.raises(HTTPException) as exc_info:
                await team_routes.get_team("alice:private-team")
            assert exc_info.value.status_code == 403
        finally:
            team_routes._store = original_store

    @pytest.mark.asyncio
    async def test_update_requires_owner(self, team_store):
        """A user in the shared group can use the team but cannot update it."""
        await team_store.create(
            TeamSpec(
                name="eng-team",
                planner="p",
                synthesizer="s",
                workers=["w"],
                owner_id="alice",
                shared_with=["engineering"],
            )
        )

        from agentic_primitives_gateway.routes import teams as team_routes

        original_store = team_routes._store
        team_routes._store = team_store

        try:
            set_authenticated_principal(
                AuthenticatedPrincipal(id="bob", type="user", groups=frozenset({"engineering"}))
            )
            from fastapi import HTTPException

            from agentic_primitives_gateway.models.teams import UpdateTeamRequest

            with pytest.raises(HTTPException) as exc_info:
                await team_routes.update_team(
                    "alice:eng-team",
                    UpdateTeamRequest(description="hacked"),
                )
            assert exc_info.value.status_code == 403
        finally:
            team_routes._store = original_store

    @pytest.mark.asyncio
    async def test_delete_requires_owner(self, team_store):
        """Even on a wildcard-shared team, non-owners can't delete."""
        await team_store.create(
            TeamSpec(
                name="public-team",
                planner="p",
                synthesizer="s",
                workers=["w"],
                owner_id="alice",
                shared_with=["*"],
            )
        )

        from agentic_primitives_gateway.routes import teams as team_routes

        original_store = team_routes._store
        team_routes._store = team_store

        try:
            set_authenticated_principal(AuthenticatedPrincipal(id="bob", type="user"))
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as exc_info:
                await team_routes.delete_team("alice:public-team")
            assert exc_info.value.status_code == 403
        finally:
            team_routes._store = original_store

    @pytest.mark.asyncio
    async def test_admin_can_update_any(self, team_store):
        await team_store.create(
            TeamSpec(
                name="t",
                planner="p",
                synthesizer="s",
                workers=["w"],
                owner_id="alice",
                shared_with=[],
            )
        )

        from agentic_primitives_gateway.routes import teams as team_routes

        original_store = team_routes._store
        team_routes._store = team_store

        try:
            set_authenticated_principal(
                AuthenticatedPrincipal(id="admin-user", type="user", scopes=frozenset({"admin"}))
            )
            from agentic_primitives_gateway.models.teams import UpdateTeamRequest

            result = await team_routes.update_team(
                "alice:t",
                UpdateTeamRequest(description="admin edit"),
            )
            assert result.description == "admin edit"
        finally:
            team_routes._store = original_store


class TestListFiltersByOwnership:
    """Route-level list assertions.

    The store-level `list_for_user` is already tested; this guards the
    route handler calling into it with the correct principal.  Without
    it, a route that forgot to filter would silently leak every user's
    private teams to every caller.
    """

    @pytest.mark.asyncio
    async def test_teams_list_excludes_other_users_private(self, team_store):
        await team_store.create(
            TeamSpec(
                name="alice-private",
                planner="p",
                synthesizer="s",
                workers=["w"],
                owner_id="alice",
                shared_with=[],
            )
        )
        await team_store.create(
            TeamSpec(
                name="bob-private",
                planner="p",
                synthesizer="s",
                workers=["w"],
                owner_id="bob",
                shared_with=[],
            )
        )
        await team_store.create(
            TeamSpec(
                name="public",
                planner="p",
                synthesizer="s",
                workers=["w"],
                owner_id="system",
                shared_with=["*"],
            )
        )

        from agentic_primitives_gateway.routes import teams as team_routes

        original_store = team_routes._store
        team_routes._store = team_store

        try:
            set_authenticated_principal(AuthenticatedPrincipal(id="bob", type="user"))
            result = await team_routes.list_teams()
            names = {t.name for t in result.teams}
            assert "bob-private" in names
            assert "public" in names
            assert "alice-private" not in names  # <-- the invariant
        finally:
            team_routes._store = original_store

    @pytest.mark.asyncio
    async def test_agents_list_excludes_other_users_private(self, tmp_path):
        from agentic_primitives_gateway.agents.file_store import FileAgentStore
        from agentic_primitives_gateway.models.agents import AgentSpec
        from agentic_primitives_gateway.routes import agents as agent_routes

        store = FileAgentStore(path=str(tmp_path / "agents.json"))
        await store.create(AgentSpec(name="alice-private", model="m", owner_id="alice", shared_with=[]))
        await store.create(AgentSpec(name="bob-private", model="m", owner_id="bob", shared_with=[]))
        await store.create(AgentSpec(name="public", model="m", owner_id="system", shared_with=["*"]))

        original_store = agent_routes._store
        agent_routes._store = store

        try:
            set_authenticated_principal(AuthenticatedPrincipal(id="bob", type="user"))
            result = await agent_routes.list_agents()
            names = {a.name for a in result.agents}
            assert "bob-private" in names
            assert "public" in names
            assert "alice-private" not in names
        finally:
            agent_routes._store = original_store
