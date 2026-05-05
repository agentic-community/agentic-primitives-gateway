"""Integration tests for checkpoint + resume against a real Redis server.

Requires Redis running at localhost:6379 (or REDIS_URL env var).

Run with:
    python -m pytest tests/integration/test_checkpoint_redis.py -v
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from agentic_primitives_gateway.agents.checkpoint import (
    RedisCheckpointStore,
    ReplicaHeartbeat,
    recover_orphaned_runs,
)
from agentic_primitives_gateway.agents.runner import AgentRunner
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import set_authenticated_principal
from agentic_primitives_gateway.models.agents import AgentSpec

pytestmark = pytest.mark.integration

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
_ALICE = AuthenticatedPrincipal(id="alice", type="user", groups=frozenset({"engineering"}), scopes=frozenset())


def _redis_available() -> bool:
    try:
        import redis

        r = redis.from_url(REDIS_URL)
        r.ping()
        r.close()
        return True
    except Exception:
        return False


@pytest.fixture(autouse=True)
def _skip_without_redis():
    if not _redis_available():
        pytest.skip("Redis not available — skipping checkpoint integration tests")


@pytest.fixture
async def store():
    s = RedisCheckpointStore(redis_url=REDIS_URL)
    yield s
    # Cleanup: delete all test checkpoints
    async for key in s._redis.scan_iter(match="checkpoint:*"):
        await s._redis.delete(key)
    async for key in s._redis.scan_iter(match="replica:*"):
        await s._redis.delete(key)


class TestRedisCheckpointStore:
    @pytest.mark.asyncio
    async def test_save_load_delete(self, store: RedisCheckpointStore):
        data = {"spec_name": "test", "turns_used": 3}
        await store.save("test-key", data, ttl=60)

        loaded = await store.load("test-key")
        assert loaded is not None
        assert loaded["spec_name"] == "test"
        assert loaded["turns_used"] == 3

        await store.delete("test-key")
        assert await store.load("test-key") is None

    @pytest.mark.asyncio
    async def test_ttl_expiry(self, store: RedisCheckpointStore):
        """Keys expire after TTL."""
        import asyncio

        await store.save("ttl-test", {"data": 1}, ttl=1)
        assert await store.load("ttl-test") is not None
        await asyncio.sleep(1.5)
        assert await store.load("ttl-test") is None

    @pytest.mark.asyncio
    async def test_distributed_lock(self, store: RedisCheckpointStore):
        assert await store.acquire_lock("lock-test", "owner-a", ttl=10)
        assert not await store.acquire_lock("lock-test", "owner-b", ttl=10)
        await store.release_lock("lock-test")
        assert await store.acquire_lock("lock-test", "owner-b", ttl=10)
        await store.release_lock("lock-test")

    @pytest.mark.asyncio
    async def test_list_checkpoints(self, store: RedisCheckpointStore):
        await store.save("list-a", {})
        await store.save("list-b", {})

        keys = await store.list_checkpoints()
        assert "list-a" in keys
        assert "list-b" in keys
        # Lock keys should not appear
        await store.acquire_lock("list-a", "x")
        keys2 = await store.list_checkpoints()
        assert all(not k.endswith(":lock") for k in keys2)
        await store.release_lock("list-a")

    @pytest.mark.asyncio
    async def test_heartbeat(self, store: RedisCheckpointStore):
        assert not await store.is_replica_alive("hb-test")
        await store.set_heartbeat("hb-test", ttl=5)
        assert await store.is_replica_alive("hb-test")


class TestReplicaHeartbeatIntegration:
    @pytest.mark.asyncio
    async def test_heartbeat_starts_and_stops(self, store: RedisCheckpointStore):
        hb = ReplicaHeartbeat(store, ttl=5, interval=2)
        await hb.start()
        assert await store.is_replica_alive(hb.replica_id)
        await hb.stop()

    @pytest.mark.asyncio
    async def test_heartbeat_expires(self, store: RedisCheckpointStore):
        """Heartbeat expires after TTL if not refreshed."""
        import asyncio

        hb = ReplicaHeartbeat(store, ttl=1, interval=100)  # Long interval = won't refresh
        await store.set_heartbeat(hb.replica_id, ttl=1)
        assert await store.is_replica_alive(hb.replica_id)
        await asyncio.sleep(1.5)
        assert not await store.is_replica_alive(hb.replica_id)


class TestOrphanRecoveryIntegration:
    @pytest.mark.asyncio
    async def test_full_orphan_recovery_cycle(self, store: RedisCheckpointStore):
        """Save a checkpoint, let heartbeat expire, recover it."""
        # Simulate a dead replica's checkpoint
        await store.save(
            "alice:integ-sess",
            {
                "spec_name": "test-agent",
                "session_id": "integ-sess",
                "actor_id": "test-agent:u:alice",
                "memory_ns": "agent:test-agent:u:alice",
                "trace_id": "trace-1",
                "depth": 0,
                "prev_overrides": {},
                "session_ids": {},
                "messages": [{"role": "user", "content": "hello"}],
                "turns_used": 0,
                "tools_called": [],
                "content": "",
                "original_message": "hello",
                "replica_id": "dead-replica-xyz",
                "principal": {
                    "id": "alice",
                    "type": "user",
                    "groups": ["engineering"],
                    "scopes": [],
                },
            },
            ttl=300,
        )

        # dead-replica-xyz has no heartbeat → checkpoint is orphaned
        runner = AsyncMock()
        runner.resume = AsyncMock()

        recovered = await recover_orphaned_runs(store, runner, "live-replica")
        assert recovered == 1
        runner.resume.assert_called_once_with("alice:integ-sess")

        # Clean up
        await store.delete("alice:integ-sess")

    @pytest.mark.asyncio
    async def test_alive_replica_not_recovered(self, store: RedisCheckpointStore):
        """Checkpoints from alive replicas are skipped."""
        await store.set_heartbeat("alive-replica", ttl=30)
        await store.save(
            "bob:integ-sess",
            {
                "spec_name": "test",
                "replica_id": "alive-replica",
                "principal": {"id": "bob", "type": "user", "groups": [], "scopes": []},
            },
        )

        runner = AsyncMock()
        runner.resume = AsyncMock()

        recovered = await recover_orphaned_runs(store, runner, "other-replica")
        assert recovered == 0
        runner.resume.assert_not_called()

        await store.delete("bob:integ-sess")


class TestAgentCheckpointIntegration:
    @pytest.mark.asyncio
    async def test_checkpoint_and_resume_e2e(self, store: RedisCheckpointStore):
        """Full cycle: checkpoint during run, delete after finalize, resume from checkpoint."""
        set_authenticated_principal(_ALICE)
        spec = AgentSpec(name="integ-agent", model="test-model", checkpointing_enabled=True)

        runner = AgentRunner()
        runner.set_checkpoint_store(store, replica_id="integ-replica")

        # Mock the agent store for resume.  Resume uses
        # ``resolve_qualified(owner, name)`` (not ``.get``) since
        # owner-scoped identities landed; older tests used ``.get``.
        agent_store = AsyncMock()
        agent_store.resolve_qualified = AsyncMock(return_value=spec)
        runner.set_store(agent_store)

        from agentic_primitives_gateway.agents.runner import _RunContext

        ctx = _RunContext(
            spec=spec,
            session_id="integ-sess-001",
            actor_id="integ-agent:u:alice",
            trace_id="trace-integ",
            memory_ns="agent:integ-agent:u:alice",
            knowledge_ns="test-corpus",
            depth=0,
            prev_overrides={},
            messages=[{"role": "user", "content": "test message"}],
            turns_used=1,
            tools_called=[],
            content="partial response",
        )

        # Checkpoint
        await runner._checkpoint(ctx, "test message")
        loaded = await store.load("alice:integ-sess-001")
        assert loaded is not None
        assert loaded["spec_name"] == "integ-agent"
        assert loaded["replica_id"] == "integ-replica"
        assert loaded["principal"]["id"] == "alice"
        assert loaded["principal"]["groups"] == ["engineering"]

        # Resume (mocked LLM)
        mock_response = {"content": "resumed!", "stop_reason": "end_turn"}
        with patch("agentic_primitives_gateway.agents.runner.registry") as mock_reg:
            mock_reg.llm.route_request = AsyncMock(return_value=mock_response)
            mock_reg.memory.create_event = AsyncMock()
            mock_reg.observability.ingest_trace = AsyncMock()

            await runner.resume("alice:integ-sess-001")

        # Checkpoint should be deleted after successful resume
        assert await store.load("alice:integ-sess-001") is None
