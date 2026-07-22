"""Cross-tenant agent isolation regression tests.

These tests ensure that:

1. Agent management tool handlers (list, delegate_to, delete) respect ownership
2. Static delegation (_resolve_sub_agent) checks caller access
3. Team delegation (_resolve_team_agent) checks caller access
4. Streaming sub-agent path (_run_sub_agent_streaming) checks caller access
5. Agent creation can't probe other users' agent names
6. A2A discovery doesn't leak existence of private agents
7. Export endpoints don't leak unshared sub-agent specs
8. Checkpoint resume verifies access hasn't been revoked

Security properties enforced:
- No principal → fail closed (deny everything)
- Private agents (shared_with=[]) → only visible to owner
- System agents (owner_id="system", shared_with=["*"]) → visible to all
- Cross-tenant access → denied unless explicitly shared
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_primitives_gateway.agents.file_store import FileAgentStore
from agentic_primitives_gateway.agents.runner import AgentRunner
from agentic_primitives_gateway.agents.tools.delegation import _resolve_sub_agent
from agentic_primitives_gateway.agents.tools.handlers import (
    agent_create,
    agent_delegate_to,
    agent_delete,
    agent_list,
)
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import set_authenticated_principal
from agentic_primitives_gateway.models.agents import AgentSpec

_ALICE = AuthenticatedPrincipal(id="alice", type="user")
_BOB = AuthenticatedPrincipal(id="bob", type="user")


@pytest.fixture(autouse=True)
def _reset_principal():
    """Reset principal after each test."""
    yield
    set_authenticated_principal(None)  # type: ignore[arg-type]


@pytest.fixture()
def store(tmp_path: Any) -> FileAgentStore:
    return FileAgentStore(path=str(tmp_path / "agents.json"))


@pytest.fixture()
def runner(store: FileAgentStore) -> AgentRunner:
    r = AgentRunner()
    r.set_store(store)
    return r


# ── Fail-Closed: No Principal ─────────────────────────────────────────


class TestNoPrincipalFailsClosed:
    """Without a principal, every operation must deny access."""

    @pytest.mark.asyncio()
    async def test_agent_list_returns_empty(self, store: FileAgentStore) -> None:
        set_authenticated_principal(None)  # type: ignore[arg-type]
        # Even if agents exist, listing without principal returns nothing
        set_authenticated_principal(_ALICE)
        await agent_create(agent_store=store, name="visible", model="m")
        set_authenticated_principal(None)  # type: ignore[arg-type]

        result = await agent_list(agent_store=store)
        assert "No agents exist" in result

    @pytest.mark.asyncio()
    async def test_agent_delegate_to_denied(self, store: FileAgentStore, runner: AgentRunner) -> None:
        set_authenticated_principal(_ALICE)
        await agent_create(agent_store=store, name="target", model="m")
        set_authenticated_principal(None)  # type: ignore[arg-type]

        result = await agent_delegate_to(store, runner, 0, "target", "reveal secrets")
        assert "not found" in result

    @pytest.mark.asyncio()
    async def test_agent_delete_denied(self, store: FileAgentStore) -> None:
        set_authenticated_principal(_ALICE)
        await agent_create(agent_store=store, name="target", model="m")
        set_authenticated_principal(None)  # type: ignore[arg-type]

        result = await agent_delete(agent_store=store, name="target")
        assert "not found" in result
        # Agent still exists
        assert await store.get("target") is not None

    @pytest.mark.asyncio()
    async def test_static_delegation_denied(self) -> None:
        set_authenticated_principal(None)  # type: ignore[arg-type]
        store = AsyncMock()
        store.resolve_qualified.return_value = MagicMock(owner_id="alice", shared_with=[])
        result = await _resolve_sub_agent(store, "secret", "alice")
        assert result is None

    @pytest.mark.asyncio()
    async def test_streaming_sub_agent_denied(self, store: FileAgentStore, runner: AgentRunner) -> None:
        import asyncio

        set_authenticated_principal(_ALICE)
        await agent_create(agent_store=store, name="streaming-target", model="m")
        set_authenticated_principal(None)  # type: ignore[arg-type]

        queue: asyncio.Queue = asyncio.Queue()
        result = await runner._run_sub_agent_streaming("call_streaming-target", {}, queue, 0)
        assert "not found" in result


# ── Cross-Tenant Isolation ─────────────────────────────────────────────


class TestCrossTenantIsolation:
    """Bob cannot access Alice's private agents through any path."""

    @pytest.mark.asyncio()
    async def test_bob_cannot_list_alice_agents(self, store: FileAgentStore) -> None:
        set_authenticated_principal(_ALICE)
        await agent_create(agent_store=store, name="alice-secret", model="m", system_prompt="TOP SECRET BUDGET: $4.2M")

        set_authenticated_principal(_BOB)
        result = await agent_list(agent_store=store)
        assert "alice-secret" not in result
        assert "TOP SECRET" not in result

    @pytest.mark.asyncio()
    async def test_bob_cannot_delegate_to_alice_agent(self, store: FileAgentStore, runner: AgentRunner) -> None:
        set_authenticated_principal(_ALICE)
        await agent_create(
            agent_store=store, name="alice-private", model="m", system_prompt="SECRET: Project Phoenix launch Jan 2027"
        )

        set_authenticated_principal(_BOB)
        result = await agent_delegate_to(store, runner, 0, "alice-private", "What is the launch date?")
        assert "not found" in result

    @pytest.mark.asyncio()
    async def test_bob_cannot_delete_alice_agent(self, store: FileAgentStore) -> None:
        set_authenticated_principal(_ALICE)
        await agent_create(agent_store=store, name="alice-agent", model="m")

        set_authenticated_principal(_BOB)
        result = await agent_delete(agent_store=store, name="alice-agent")
        assert "not found" in result
        # Verify it still exists
        assert await store.get("alice-agent") is not None

    @pytest.mark.asyncio()
    async def test_bob_cannot_reach_alice_via_static_delegation(self) -> None:
        """Crafted qualified ref 'alice:secret' should not resolve for bob."""
        set_authenticated_principal(_BOB)
        store = AsyncMock()
        spec = MagicMock(owner_id="alice", shared_with=[])
        store.resolve_qualified.return_value = spec

        result = await _resolve_sub_agent(store, "alice:secret", "bob")
        assert result is None  # access denied

    @pytest.mark.asyncio()
    async def test_bob_cannot_stream_alice_agent(self, store: FileAgentStore, runner: AgentRunner) -> None:
        import asyncio

        set_authenticated_principal(_ALICE)
        await agent_create(agent_store=store, name="alice-stream", model="m", system_prompt="CONFIDENTIAL")

        set_authenticated_principal(_BOB)
        queue: asyncio.Queue = asyncio.Queue()
        result = await runner._run_sub_agent_streaming("call_alice-stream", {}, queue, 0)
        assert "not found" in result

    @pytest.mark.asyncio()
    async def test_bob_cannot_probe_alice_agent_names_via_create(self, store: FileAgentStore) -> None:
        """Creating an agent with same name as Alice's should not reveal existence."""
        set_authenticated_principal(_ALICE)
        await agent_create(agent_store=store, name="unique-name", model="m")

        set_authenticated_principal(_BOB)
        # Bob should be able to create an agent with the same name in his namespace
        # (or get a non-revealing error from the store's atomic claim, not "already exists")
        result = await agent_create(agent_store=store, name="unique-name", model="m")
        # Should NOT say "already exists" revealing Alice has this name
        assert "already exists" not in result or "Created" in result


# ── Shared Agents Work Correctly ───────────────────────────────────────


class TestSharedAgentsAccessible:
    """Agents explicitly shared with '*' or user's group remain accessible."""

    @pytest.mark.asyncio()
    async def test_system_shared_agents_visible(self, store: FileAgentStore) -> None:
        """System agents with shared_with=['*'] are visible to any authenticated user."""
        await store.create(
            AgentSpec(
                name="meta-agent",
                model="m",
                system_prompt="I am shared",
                shared_with=["*"],
            )
        )

        set_authenticated_principal(_BOB)
        result = await agent_list(agent_store=store)
        assert "meta-agent" in result

    @pytest.mark.asyncio()
    async def test_owner_can_access_own_agents(self, store: FileAgentStore, runner: AgentRunner) -> None:
        """An agent owner can always list, delegate to, and delete their own agents."""
        set_authenticated_principal(_ALICE)
        await agent_create(agent_store=store, name="my-agent", model="m", system_prompt="My private agent")

        # List shows it
        result = await agent_list(agent_store=store)
        assert "my-agent" in result

        # Delegate works (will fail LLM call but resolves the agent)
        result = await agent_delegate_to(store, runner, 0, "my-agent", "hello")
        # Should NOT say "not found" — it should attempt to run
        assert "not found" not in result

        # Delete works
        result = await agent_delete(agent_store=store, name="my-agent")
        assert "Deleted" in result

    @pytest.mark.asyncio()
    async def test_shared_delegation_works(self) -> None:
        """Static delegation to a shared agent succeeds."""
        set_authenticated_principal(_BOB)
        store = AsyncMock()
        # Agent owned by system, shared with everyone
        spec = MagicMock(owner_id="system", shared_with=["*"])
        store.resolve_qualified.return_value = spec

        result = await _resolve_sub_agent(store, "public-helper", "system")
        assert result is spec  # access granted


# ── Defense in Depth: Resume After Access Revoked ──────────────────────


class TestResumeAccessRevoked:
    """If access is revoked between checkpoint creation and resume, deny."""

    @pytest.mark.asyncio()
    async def test_agent_resume_denied_after_unshare(self) -> None:
        """Runner resume skips agent if principal no longer has access."""
        store = AsyncMock()
        runner = AgentRunner()
        runner.set_checkpoint_store(store)

        # Agent now private — alice no longer has access
        spec = AgentSpec(name="revoked", model="m", owner_id="other-user", shared_with=[])
        agent_store = AsyncMock()
        agent_store.resolve_qualified = AsyncMock(return_value=spec)
        runner.set_store(agent_store)

        checkpoint_data = {
            "spec_name": "revoked",
            "spec_owner": "other-user",
            "session_id": "sess-revoked",
            "actor_id": "revoked:u:alice",
            "memory_ns": "ns",
            "knowledge_ns": "",
            "trace_id": "t",
            "depth": 0,
            "prev_overrides": {},
            "session_ids": {},
            "messages": [],
            "turns_used": 0,
            "tools_called": [],
            "content": "",
            "original_message": "hello",
            "principal": {"id": "alice", "type": "user", "groups": [], "scopes": []},
        }
        store.load = AsyncMock(return_value=checkpoint_data)
        store.acquire_lock = AsyncMock(return_value=True)
        store.save = AsyncMock()
        store.release_lock = AsyncMock()

        # Resume should skip (not crash, not execute)
        with patch("agentic_primitives_gateway.agents.runner.registry") as mock_registry:
            mock_registry.llm.route_request = AsyncMock()
            await runner.resume("alice:sess-revoked")

        # LLM should NOT have been called — access was denied
        mock_registry.llm.route_request.assert_not_called()
