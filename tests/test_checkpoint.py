"""Tests for run checkpointing and resume."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from agentic_primitives_gateway.agents.checkpoint import CheckpointStore
from agentic_primitives_gateway.agents.runner import AgentRunner, _RunContext
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import set_authenticated_principal
from agentic_primitives_gateway.models.agents import AgentSpec


class InMemoryCheckpointStore(CheckpointStore):
    """In-memory checkpoint store for testing."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, str] = {}

    async def save(self, key: str, data: dict[str, Any], ttl: int = 600) -> None:
        self._data[key] = data

    async def load(self, key: str) -> dict[str, Any] | None:
        return self._data.get(key)

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)
        self._locks.pop(key, None)

    async def acquire_lock(self, key: str, owner: str, ttl: int = 60) -> bool:
        if key in self._locks:
            return False
        self._locks[key] = owner
        return True

    async def release_lock(self, key: str) -> None:
        self._locks.pop(key, None)

    async def list_checkpoints(self) -> list[str]:
        return list(self._data.keys())


_ALICE = AuthenticatedPrincipal(id="alice", type="user", groups=frozenset({"engineering"}), scopes=frozenset())


def _make_spec(name: str = "test-agent") -> AgentSpec:
    return AgentSpec(name=name, model="test-model")


class TestCheckpointSaveLoad:
    @pytest.mark.asyncio
    async def test_checkpoint_saves_state(self):
        store = InMemoryCheckpointStore()
        runner = AgentRunner()
        runner.set_checkpoint_store(store)

        set_authenticated_principal(_ALICE)
        spec = _make_spec()
        ctx = _RunContext(
            spec=spec,
            session_id="sess-1",
            actor_id="test-agent:u:alice",
            trace_id="trace-1",
            knowledge_ns="agent:test-agent:u:alice",
            depth=0,
            prev_overrides={},
            messages=[{"role": "user", "content": "hello"}],
            turns_used=1,
            tools_called=["remember"],
            content="response",
        )

        await runner._checkpoint(ctx, "hello")

        data = await store.load("alice:sess-1")
        assert data is not None
        assert data["spec_name"] == "test-agent"
        assert data["session_id"] == "sess-1"
        assert data["actor_id"] == "test-agent:u:alice"
        assert data["turns_used"] == 1
        assert data["original_message"] == "hello"
        assert data["principal"]["id"] == "alice"
        assert data["principal"]["type"] == "user"
        assert data["principal"]["groups"] == ["engineering"]

    @pytest.mark.asyncio
    async def test_checkpoint_deleted_after_finalize(self):
        store = InMemoryCheckpointStore()
        runner = AgentRunner()
        runner.set_checkpoint_store(store)

        set_authenticated_principal(_ALICE)
        spec = _make_spec()
        ctx = _RunContext(
            spec=spec,
            session_id="sess-1",
            actor_id="test-agent:u:alice",
            trace_id="trace-1",
            knowledge_ns="agent:test-agent:u:alice",
            depth=0,
            prev_overrides={},
            content="done",
        )

        await runner._checkpoint(ctx, "hello")
        assert await store.load("alice:sess-1") is not None

        with patch.object(runner, "_cleanup_sessions", new_callable=AsyncMock):
            await runner._finalize(ctx, "hello")

        assert await store.load("alice:sess-1") is None

    @pytest.mark.asyncio
    async def test_no_checkpoint_without_store(self):
        runner = AgentRunner()
        set_authenticated_principal(_ALICE)
        spec = _make_spec()
        ctx = _RunContext(
            spec=spec,
            session_id="sess-1",
            actor_id="test-agent:u:alice",
            trace_id="t",
            knowledge_ns="ns",
            depth=0,
            prev_overrides={},
        )
        # Should not raise
        await runner._checkpoint(ctx, "hello")
        await runner._delete_checkpoint(ctx)

    @pytest.mark.asyncio
    async def test_checkpoint_requires_principal(self):
        store = InMemoryCheckpointStore()
        runner = AgentRunner()
        runner.set_checkpoint_store(store)

        set_authenticated_principal(None)  # type: ignore[arg-type]
        spec = _make_spec()
        ctx = _RunContext(
            spec=spec,
            session_id="sess-1",
            actor_id="test-agent:u:alice",
            trace_id="t",
            knowledge_ns="ns",
            depth=0,
            prev_overrides={},
        )
        with pytest.raises(RuntimeError, match="authenticated principal"):
            await runner._checkpoint(ctx, "hello")


class TestResume:
    @pytest.mark.asyncio
    async def test_resume_reconstructs_principal(self):
        store = InMemoryCheckpointStore()
        runner = AgentRunner()
        runner.set_checkpoint_store(store)

        agent_store = AsyncMock()
        agent_store.get = AsyncMock(return_value=_make_spec())
        runner.set_store(agent_store)

        # Save a checkpoint
        checkpoint_data = {
            "spec_name": "test-agent",
            "session_id": "sess-1",
            "actor_id": "test-agent:u:alice",
            "knowledge_ns": "agent:test-agent:u:alice",
            "trace_id": "trace-1",
            "depth": 0,
            "prev_overrides": {},
            "session_ctx": {},
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
            ],
            "turns_used": 1,
            "tools_called": [],
            "content": "hi there",
            "original_message": "hello",
            "principal": {
                "id": "alice",
                "type": "user",
                "groups": ["engineering"],
                "scopes": [],
            },
        }
        await store.save("alice:sess-1", checkpoint_data)

        # Mock the LLM to return a final response
        mock_response = {"content": "resumed response", "stop_reason": "end_turn"}
        with patch("agentic_primitives_gateway.agents.runner.registry") as mock_registry:
            mock_registry.gateway.route_request = AsyncMock(return_value=mock_response)
            mock_registry.memory.create_event = AsyncMock()
            mock_registry.observability.ingest_trace = AsyncMock()

            await runner.resume("alice:sess-1")

        # Checkpoint should be deleted after successful resume
        assert await store.load("alice:sess-1") is None

    @pytest.mark.asyncio
    async def test_resume_lock_prevents_double_recovery(self):
        store = InMemoryCheckpointStore()
        runner = AgentRunner()
        runner.set_checkpoint_store(store)
        runner.set_store(AsyncMock())

        await store.save("alice:sess-1", {"spec_name": "test"})

        # First lock succeeds
        assert await store.acquire_lock("alice:sess-1", "replica-1")
        # Second lock fails
        assert not await store.acquire_lock("alice:sess-1", "replica-2")

    @pytest.mark.asyncio
    async def test_resume_skips_missing_checkpoint(self):
        store = InMemoryCheckpointStore()
        runner = AgentRunner()
        runner.set_checkpoint_store(store)
        runner.set_store(AsyncMock())

        # No checkpoint saved — should return without error
        await runner.resume("nonexistent:sess-1")

    @pytest.mark.asyncio
    async def test_resume_skips_deleted_agent(self):
        store = InMemoryCheckpointStore()
        runner = AgentRunner()
        runner.set_checkpoint_store(store)

        agent_store = AsyncMock()
        agent_store.get = AsyncMock(return_value=None)
        runner.set_store(agent_store)

        await store.save(
            "alice:sess-1",
            {
                "spec_name": "deleted-agent",
                "session_id": "sess-1",
                "actor_id": "deleted-agent:u:alice",
                "principal": {"id": "alice", "type": "user", "groups": [], "scopes": []},
            },
        )

        # Should not raise — logs a warning and skips
        await runner.resume("alice:sess-1")


class TestCheckpointStoreABC:
    @pytest.mark.asyncio
    async def test_in_memory_store_crud(self):
        store = InMemoryCheckpointStore()

        await store.save("k1", {"data": 1})
        assert await store.load("k1") == {"data": 1}

        await store.delete("k1")
        assert await store.load("k1") is None

    @pytest.mark.asyncio
    async def test_in_memory_store_list(self):
        store = InMemoryCheckpointStore()
        await store.save("k1", {})
        await store.save("k2", {})

        keys = await store.list_checkpoints()
        assert set(keys) == {"k1", "k2"}

    @pytest.mark.asyncio
    async def test_in_memory_store_locking(self):
        store = InMemoryCheckpointStore()

        assert await store.acquire_lock("k1", "owner-a")
        assert not await store.acquire_lock("k1", "owner-b")

        await store.release_lock("k1")
        assert await store.acquire_lock("k1", "owner-b")
