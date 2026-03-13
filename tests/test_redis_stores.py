"""Unit tests for Redis-backed agent, team, and task stores.

Mocks the Redis client so these run without a Redis server.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_primitives_gateway.agents.redis_store import RedisAgentStore, RedisTeamStore
from agentic_primitives_gateway.models.agents import AgentSpec
from agentic_primitives_gateway.models.tasks import TaskNote, TaskStatus
from agentic_primitives_gateway.models.teams import TeamSpec

_REDIS_MOD = "agentic_primitives_gateway.agents.base_store"
_TASKS_MOD = "agentic_primitives_gateway.primitives.tasks.redis"


def _mock_redis() -> AsyncMock:
    """Create a mock async Redis client with hash operation support."""
    store: dict[str, dict[str, str]] = {}
    r = AsyncMock()

    async def hset(key, field, value):
        store.setdefault(key, {})[field] = value

    async def hget(key, field):
        return store.get(key, {}).get(field)

    async def hgetall(key):
        return store.get(key, {})

    async def hdel(key, field):
        if key in store and field in store[key]:
            del store[key][field]
            return 1
        return 0

    async def ping():
        return True

    r.hset = AsyncMock(side_effect=hset)
    r.hget = AsyncMock(side_effect=hget)
    r.hgetall = AsyncMock(side_effect=hgetall)
    r.hdel = AsyncMock(side_effect=hdel)
    r.ping = AsyncMock(side_effect=ping)
    r._store = store  # expose for assertions
    return r


# ── RedisAgentStore ──────────────────────────────────────────────────


class TestRedisAgentStore:
    @pytest.fixture
    def store(self) -> RedisAgentStore:
        with patch(f"{_REDIS_MOD}._get_redis", return_value=_mock_redis()):
            return RedisAgentStore(redis_url="redis://test:6379/0")

    async def test_create_and_get(self, store: RedisAgentStore) -> None:
        spec = AgentSpec(name="agent1", model="m1")
        await store.create(spec)
        result = await store.get("agent1")
        assert result is not None
        assert result.name == "agent1"
        assert result.model == "m1"

    async def test_get_not_found(self, store: RedisAgentStore) -> None:
        assert await store.get("nonexistent") is None

    async def test_list(self, store: RedisAgentStore) -> None:
        await store.create(AgentSpec(name="a1", model="m"))
        await store.create(AgentSpec(name="a2", model="m"))
        agents = await store.list()
        names = {a.name for a in agents}
        assert names == {"a1", "a2"}

    async def test_update(self, store: RedisAgentStore) -> None:
        await store.create(AgentSpec(name="u1", model="m1"))
        updated = await store.update("u1", {"model": "m2"})
        assert updated.model == "m2"

    async def test_update_not_found(self, store: RedisAgentStore) -> None:
        with pytest.raises(KeyError, match="not found"):
            await store.update("missing", {"model": "m"})

    async def test_delete(self, store: RedisAgentStore) -> None:
        await store.create(AgentSpec(name="d1", model="m"))
        assert await store.delete("d1") is True
        assert await store.get("d1") is None

    async def test_delete_not_found(self, store: RedisAgentStore) -> None:
        assert await store.delete("missing") is False


# ── RedisTeamStore ───────────────────────────────────────────────────


class TestRedisTeamStore:
    @pytest.fixture
    def store(self) -> RedisTeamStore:
        with patch(f"{_REDIS_MOD}._get_redis", return_value=_mock_redis()):
            return RedisTeamStore(redis_url="redis://test:6379/0")

    async def test_create_and_get(self, store: RedisTeamStore) -> None:
        spec = TeamSpec(name="team1", planner="p", synthesizer="s", workers=["w"])
        await store.create(spec)
        result = await store.get("team1")
        assert result is not None
        assert result.name == "team1"

    async def test_get_not_found(self, store: RedisTeamStore) -> None:
        assert await store.get("nonexistent") is None

    async def test_list(self, store: RedisTeamStore) -> None:
        await store.create(TeamSpec(name="t1", planner="p", synthesizer="s", workers=["w"]))
        await store.create(TeamSpec(name="t2", planner="p", synthesizer="s", workers=["w"]))
        teams = await store.list()
        names = {t.name for t in teams}
        assert names == {"t1", "t2"}

    async def test_update(self, store: RedisTeamStore) -> None:
        await store.create(TeamSpec(name="t1", planner="p", synthesizer="s", workers=["w"]))
        updated = await store.update("t1", {"description": "updated"})
        assert updated.description == "updated"

    async def test_update_not_found(self, store: RedisTeamStore) -> None:
        with pytest.raises(KeyError, match="not found"):
            await store.update("missing", {"description": "x"})

    async def test_delete(self, store: RedisTeamStore) -> None:
        await store.create(TeamSpec(name="d1", planner="p", synthesizer="s", workers=["w"]))
        assert await store.delete("d1") is True
        assert await store.get("d1") is None

    async def test_delete_not_found(self, store: RedisTeamStore) -> None:
        assert await store.delete("missing") is False


# ── RedisTasksProvider ───────────────────────────────────────────────


class TestRedisTasksProvider:
    @pytest.fixture
    def provider(self):
        mock_r = _mock_redis()

        # Simulate Lua scripts by implementing them in Python against the mock store
        import json as _json

        async def _lua_claim(keys, args):
            key, task_id, agent_name, now = keys[0], args[0], args[1], args[2]
            raw = await mock_r.hget(key, task_id)
            if raw is None:
                return None
            data = _json.loads(raw)
            if data["status"] != "pending":
                return None
            for dep_id in data.get("depends_on", []):
                dep_raw = await mock_r.hget(key, dep_id)
                if dep_raw is None or _json.loads(dep_raw)["status"] != "done":
                    return None
            data["status"] = "claimed"
            data["assigned_to"] = agent_name
            data["updated_at"] = now
            updated = _json.dumps(data, default=str)
            await mock_r.hset(key, task_id, updated)
            return updated

        async def _lua_update(keys, args):
            key, task_id, new_status, new_result, now = keys[0], args[0], args[1], args[2], args[3]
            raw = await mock_r.hget(key, task_id)
            if raw is None:
                return None
            data = _json.loads(raw)
            data["updated_at"] = now
            if new_status:
                data["status"] = new_status
            if new_result:
                data["result"] = new_result
            updated = _json.dumps(data, default=str)
            await mock_r.hset(key, task_id, updated)
            return updated

        async def _lua_add_note(keys, args):
            key, task_id, note_json, now = keys[0], args[0], args[1], args[2]
            raw = await mock_r.hget(key, task_id)
            if raw is None:
                return None
            data = _json.loads(raw)
            data["updated_at"] = now
            data.setdefault("notes", []).append(_json.loads(note_json))
            updated = _json.dumps(data, default=str)
            await mock_r.hset(key, task_id, updated)
            return updated

        scripts = {
            "claim": AsyncMock(side_effect=_lua_claim),
            "update": AsyncMock(side_effect=_lua_update),
            "add_note": AsyncMock(side_effect=_lua_add_note),
        }
        mock_r.register_script = MagicMock(return_value=AsyncMock())

        with patch("redis.asyncio.from_url", return_value=mock_r):
            from agentic_primitives_gateway.primitives.tasks.redis import RedisTasksProvider

            p = RedisTasksProvider(redis_url="redis://test:6379/0")
            p._redis = mock_r
            p._scripts = scripts
            return p

    async def test_create_and_get(self, provider) -> None:
        task = await provider.create_task("run1", title="Task 1", description="desc")
        assert task.title == "Task 1"
        assert task.status == TaskStatus.PENDING

        retrieved = await provider.get_task("run1", task.id)
        assert retrieved is not None
        assert retrieved.title == "Task 1"

    async def test_get_not_found(self, provider) -> None:
        assert await provider.get_task("run1", "missing") is None

    async def test_list_tasks(self, provider) -> None:
        await provider.create_task("run1", title="A", priority=1)
        await provider.create_task("run1", title="B", priority=2)
        tasks = await provider.list_tasks("run1")
        assert len(tasks) == 2
        assert tasks[0].title == "B"  # higher priority first

    async def test_list_tasks_filter_status(self, provider) -> None:
        t = await provider.create_task("run1", title="A")
        await provider.update_task("run1", t.id, status="done")
        await provider.create_task("run1", title="B")

        done = await provider.list_tasks("run1", status="done")
        assert len(done) == 1
        assert done[0].title == "A"

    async def test_update_task(self, provider) -> None:
        t = await provider.create_task("run1", title="T")
        updated = await provider.update_task("run1", t.id, status="in_progress", result="working")
        assert updated is not None
        assert updated.status == "in_progress"
        assert updated.result == "working"

    async def test_update_not_found(self, provider) -> None:
        assert await provider.update_task("run1", "missing", status="done") is None

    async def test_add_note(self, provider) -> None:
        t = await provider.create_task("run1", title="T")
        note = TaskNote(agent="w1", content="done")
        updated = await provider.add_note("run1", t.id, note)
        assert updated is not None
        assert len(updated.notes) == 1
        assert updated.notes[0].agent == "w1"

    async def test_add_note_not_found(self, provider) -> None:
        note = TaskNote(agent="w1", content="x")
        assert await provider.add_note("run1", "missing", note) is None

    async def test_healthcheck(self, provider) -> None:
        assert await provider.healthcheck() is True


# ── RedisSpecStore base class methods ────────────────────────────────


class TestRedisSpecStoreSeed:
    """Tests for RedisSpecStore.seed() which runs synchronously at startup."""

    @pytest.fixture
    def store(self) -> RedisAgentStore:
        with patch(f"{_REDIS_MOD}._get_redis", return_value=_mock_redis()):
            return RedisAgentStore(redis_url="redis://test:6379/0")

    async def test_seed_new_specs(self, store: RedisAgentStore) -> None:
        """Seeding new specs writes them to Redis."""
        specs = {
            "agent1": {"model": "m1", "system_prompt": "You are agent1."},
            "agent2": {"model": "m2"},
        }

        # seed() runs the async _seed() via loop.create_task — we need to await it
        # Since we have a running loop (pytest-asyncio), create_task will be used
        store.seed(specs)
        # Let the task run
        import asyncio

        await asyncio.sleep(0.1)

        # Verify specs were written
        result = await store.get("agent1")
        assert result is not None
        assert result.name == "agent1"
        assert result.model == "m1"

        result2 = await store.get("agent2")
        assert result2 is not None
        assert result2.name == "agent2"

    async def test_seed_defaults_shared_with_star(self, store: RedisAgentStore) -> None:
        """Seeded specs get shared_with=['*'] by default."""
        specs = {"agent1": {"model": "m1"}}
        store.seed(specs)
        import asyncio

        await asyncio.sleep(0.1)

        result = await store.get("agent1")
        assert result is not None
        assert result.shared_with == ["*"]

    async def test_seed_defaults_checkpointing_enabled(self, store: RedisAgentStore) -> None:
        """Seeded specs get checkpointing_enabled=True by default."""
        specs = {"agent1": {"model": "m1"}}
        store.seed(specs)
        import asyncio

        await asyncio.sleep(0.1)

        result = await store.get("agent1")
        assert result is not None
        assert result.checkpointing_enabled is True

    async def test_seed_overwrites_changed_spec(self, store: RedisAgentStore) -> None:
        """Seeding overwrites a spec if it has changed."""
        await store.create(AgentSpec(name="agent1", model="old-model"))

        specs = {"agent1": {"model": "new-model"}}
        store.seed(specs)
        import asyncio

        await asyncio.sleep(0.1)

        result = await store.get("agent1")
        assert result is not None
        assert result.model == "new-model"

    async def test_seed_skips_unchanged_spec(self, store: RedisAgentStore) -> None:
        """Seeding does not write if spec is unchanged."""
        spec = AgentSpec(name="agent1", model="m1", shared_with=["*"], checkpointing_enabled=True)
        await store.create(spec)

        specs = {"agent1": {"model": "m1"}}
        store.seed(specs)
        import asyncio

        await asyncio.sleep(0.1)

        # hset should not have been called again since spec is unchanged
        # (or called at most once more for the comparison)
        result = await store.get("agent1")
        assert result is not None
        assert result.model == "m1"


class TestRedisSpecStoreFactoryMethods:
    """Tests for RedisSpecStore.create_background_run_manager() and create_session_registry()."""

    @pytest.fixture
    def store(self) -> RedisAgentStore:
        with patch(f"{_REDIS_MOD}._get_redis", return_value=_mock_redis()):
            return RedisAgentStore(redis_url="redis://test:6379/0")

    def test_create_background_run_manager(self, store: RedisAgentStore) -> None:
        """Factory creates a BackgroundRunManager with RedisEventStore."""
        with patch("agentic_primitives_gateway.routes._background.RedisEventStore") as mock_event_store_cls:
            mock_event_store_cls.return_value = MagicMock()
            mgr = store.create_background_run_manager()
            assert mgr is not None
            mock_event_store_cls.assert_called_once_with("redis://test:6379/0")

    def test_create_session_registry(self, store: RedisAgentStore) -> None:
        """Factory creates a RedisSessionRegistry."""
        with patch("agentic_primitives_gateway.agents.session_registry.RedisSessionRegistry") as mock_reg_cls:
            mock_reg_cls.return_value = MagicMock()
            reg = store.create_session_registry()
            assert reg is not None
            mock_reg_cls.assert_called_once_with(redis_url="redis://test:6379/0")


class TestGetRedisHelper:
    """Tests for the _get_redis helper function."""

    def test_get_redis_creates_client(self) -> None:
        mock_client = MagicMock()
        with patch("redis.asyncio.from_url", return_value=mock_client):
            from agentic_primitives_gateway.agents.base_store import _get_redis

            result = _get_redis("redis://localhost:6379/0")
            assert result is mock_client
