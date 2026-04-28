"""Tests for user-scoped session isolation.

Verifies that:
- resolve_actor_id produces user-scoped IDs for authenticated users
- resolve_actor_id requires a principal (no anonymous fallback)
- The runner uses user-scoped actor_id for history load/store
- Session endpoints use user-scoped actor_id
- Two authenticated users have isolated conversations on the same agent
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agentic_primitives_gateway.agents.namespace import resolve_actor_id
from agentic_primitives_gateway.auth.models import ANONYMOUS_PRINCIPAL, AuthenticatedPrincipal
from agentic_primitives_gateway.models.agents import AgentSpec


def _spec(name: str, owner_id: str = "system") -> AgentSpec:
    return AgentSpec(name=name, model="m", owner_id=owner_id)


class TestResolveActorId:
    def test_anonymous_always_scoped(self):
        result = resolve_actor_id(_spec("my-agent"), ANONYMOUS_PRINCIPAL)
        assert result == "system:my-agent:u:anonymous"

    def test_none_principal_raises(self):
        with pytest.raises(AttributeError):
            resolve_actor_id(_spec("my-agent"), None)  # type: ignore[arg-type]

    def test_authenticated_user_scoped(self):
        p = AuthenticatedPrincipal(id="alice", type="user")
        result = resolve_actor_id(_spec("my-agent"), p)
        assert result == "system:my-agent:u:alice"

    def test_noop_principal_scoped(self):
        from agentic_primitives_gateway.auth.models import NOOP_PRINCIPAL

        result = resolve_actor_id(_spec("my-agent"), NOOP_PRINCIPAL)
        assert result == "system:my-agent:u:noop"

    def test_different_users_different_actor_ids(self):
        alice = AuthenticatedPrincipal(id="alice", type="user")
        bob = AuthenticatedPrincipal(id="bob", type="user")
        assert resolve_actor_id(_spec("agent"), alice) != resolve_actor_id(_spec("agent"), bob)

    def test_same_user_different_agents(self):
        p = AuthenticatedPrincipal(id="alice", type="user")
        assert resolve_actor_id(_spec("agent-a"), p) != resolve_actor_id(_spec("agent-b"), p)

    def test_different_owners_different_actor_ids(self):
        p = AuthenticatedPrincipal(id="alice", type="user")
        assert resolve_actor_id(_spec("agent", "alice"), p) != resolve_actor_id(_spec("agent", "bob"), p)

    def test_service_principal_scoped(self):
        p = AuthenticatedPrincipal(id="ci-bot", type="service")
        result = resolve_actor_id(_spec("my-agent"), p)
        assert result == "system:my-agent:u:ci-bot"


class TestRunnerUsesActorId:
    """Verify the runner passes user-scoped actor_id to memory operations."""

    @pytest.mark.asyncio
    async def test_init_context_sets_actor_id_for_authenticated(self):
        from agentic_primitives_gateway.agents.runner import AgentRunner
        from agentic_primitives_gateway.context import set_authenticated_principal
        from agentic_primitives_gateway.models.agents import AgentSpec

        runner = AgentRunner()
        spec = AgentSpec(name="test-agent", model="m")

        alice = AuthenticatedPrincipal(id="alice", type="user")
        set_authenticated_principal(alice)

        with patch("agentic_primitives_gateway.agents.runner.registry"):
            ctx = await runner._init_context(spec, "hello", "sess-1", 0)

        assert ctx.actor_id == "system:test-agent:u:alice"

    @pytest.mark.asyncio
    async def test_init_context_sets_actor_id_noop(self):
        from agentic_primitives_gateway.agents.runner import AgentRunner
        from agentic_primitives_gateway.auth.models import NOOP_PRINCIPAL
        from agentic_primitives_gateway.context import set_authenticated_principal
        from agentic_primitives_gateway.models.agents import AgentSpec

        runner = AgentRunner()
        spec = AgentSpec(name="test-agent", model="m")

        set_authenticated_principal(NOOP_PRINCIPAL)

        with patch("agentic_primitives_gateway.agents.runner.registry"):
            ctx = await runner._init_context(spec, "hello", "sess-1", 0)

        assert ctx.actor_id == "system:test-agent:u:noop"

    @pytest.mark.asyncio
    async def test_finalize_stores_with_actor_id(self):
        """_finalize calls _store_turn with the user-scoped actor_id."""
        from agentic_primitives_gateway.agents.runner import AgentRunner, _RunContext
        from agentic_primitives_gateway.models.agents import AgentSpec

        runner = AgentRunner()
        spec = AgentSpec(name="test-agent", model="m")

        ctx = _RunContext(
            spec=spec,
            session_id="sess-1",
            actor_id="test-agent:u:alice",
            trace_id="t",
            memory_ns="ns",
            knowledge_ns="test-corpus",
            depth=0,
            prev_overrides={},
            content="response text",
        )

        with (
            patch.object(runner, "_cleanup_sessions", new_callable=AsyncMock),
            patch.object(runner, "_store_turn", new_callable=AsyncMock) as mock_store,
            patch.object(runner, "_trace_conversation", new_callable=AsyncMock),
        ):
            await runner._finalize(ctx, "user message")

        mock_store.assert_called_once_with("test-agent:u:alice", "sess-1", "user message", "response text")


class TestSessionIsolation:
    """Verify two users have isolated conversations via in-memory provider."""

    @pytest.mark.asyncio
    async def test_two_users_isolated_sessions(self):
        """Two users chatting with the same agent don't see each other's history."""
        from agentic_primitives_gateway.primitives.memory.in_memory import InMemoryProvider

        provider = InMemoryProvider()

        # Alice stores a turn
        alice_actor = "agent:u:alice"
        await provider.create_event(
            actor_id=alice_actor,
            session_id="sess-1",
            messages=[("Alice's question", "user"), ("Alice's answer", "assistant")],
        )

        # Bob stores a turn
        bob_actor = "agent:u:bob"
        await provider.create_event(
            actor_id=bob_actor,
            session_id="sess-1",
            messages=[("Bob's question", "user"), ("Bob's answer", "assistant")],
        )

        # Alice only sees her sessions
        alice_sessions = await provider.list_sessions(alice_actor)
        assert len(alice_sessions) == 1
        alice_turns = await provider.get_last_turns(actor_id=alice_actor, session_id="sess-1", k=10)
        assert any("Alice's question" in msg.get("text", "") for turn in alice_turns for msg in turn)
        assert not any("Bob's question" in msg.get("text", "") for turn in alice_turns for msg in turn)

        # Bob only sees his sessions
        bob_sessions = await provider.list_sessions(bob_actor)
        assert len(bob_sessions) == 1
        bob_turns = await provider.get_last_turns(actor_id=bob_actor, session_id="sess-1", k=10)
        assert any("Bob's question" in msg.get("text", "") for turn in bob_turns for msg in turn)
        assert not any("Alice's question" in msg.get("text", "") for turn in bob_turns for msg in turn)

        # Anonymous sees nothing from either
        anon_sessions = await provider.list_sessions("agent")
        assert len(anon_sessions) == 0

    @pytest.mark.asyncio
    async def test_noop_users_share_sessions(self):
        """Noop auth users all share the same actor_id (noop)."""
        from agentic_primitives_gateway.primitives.memory.in_memory import InMemoryProvider

        provider = InMemoryProvider()
        noop_actor = "my-agent:u:noop"

        await provider.create_event(
            actor_id=noop_actor,
            session_id="sess-1",
            messages=[("hello", "user"), ("hi", "assistant")],
        )

        sessions = await provider.list_sessions(noop_actor)
        assert len(sessions) == 1
        turns = await provider.get_last_turns(actor_id=noop_actor, session_id="sess-1", k=10)
        assert len(turns) == 1

        # Different actor_id sees nothing
        sessions = await provider.list_sessions("my-agent:u:alice")
        assert len(sessions) == 0
