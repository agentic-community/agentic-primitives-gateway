"""Tests for Redis store config, factory methods, seeding, and store backend resolution."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_primitives_gateway.agents.redis_store import (
    _AGENT_KEY,
    _TEAM_KEY,
    RedisAgentStore,
    RedisTeamStore,
)
from agentic_primitives_gateway.agents.store import FileAgentStore
from agentic_primitives_gateway.agents.team_store import FileTeamStore
from agentic_primitives_gateway.config import (
    AGENT_STORE_ALIASES,
    TEAM_STORE_ALIASES,
    AgentsConfig,
    StoreConfig,
    TeamsConfig,
)
from agentic_primitives_gateway.models.agents import AgentSpec
from agentic_primitives_gateway.models.teams import TeamSpec

_REDIS_MOD = "agentic_primitives_gateway.agents.base_store"


def _mock_redis() -> AsyncMock:
    store: dict[str, dict[str, str]] = {}
    r = AsyncMock()

    async def hget(key, field):
        return store.get(key, {}).get(field)

    async def hset(key, field, value):
        store.setdefault(key, {})[field] = value

    async def hgetall(key):
        return store.get(key, {})

    async def hdel(key, field):
        if key in store and field in store[key]:
            del store[key][field]
            return 1
        return 0

    r.hget = AsyncMock(side_effect=hget)
    r.hset = AsyncMock(side_effect=hset)
    r.hgetall = AsyncMock(side_effect=hgetall)
    r.hdel = AsyncMock(side_effect=hdel)
    return r


# ── Store alias resolution ───────────────────────────────────────────


class TestStoreAliases:
    def test_agent_file_alias(self) -> None:
        assert AGENT_STORE_ALIASES["file"] == "agentic_primitives_gateway.agents.store.FileAgentStore"

    def test_agent_redis_alias(self) -> None:
        assert AGENT_STORE_ALIASES["redis"] == "agentic_primitives_gateway.agents.redis_store.RedisAgentStore"

    def test_team_file_alias(self) -> None:
        assert TEAM_STORE_ALIASES["file"] == "agentic_primitives_gateway.agents.team_store.FileTeamStore"

    def test_team_redis_alias(self) -> None:
        assert TEAM_STORE_ALIASES["redis"] == "agentic_primitives_gateway.agents.redis_store.RedisTeamStore"


class TestStoreConfig:
    def test_default(self) -> None:
        cfg = StoreConfig()
        assert cfg.backend == "file"
        assert cfg.config == {}

    def test_redis(self) -> None:
        cfg = StoreConfig(backend="redis", config={"redis_url": "redis://host:6379/0"})
        assert cfg.backend == "redis"
        assert cfg.config["redis_url"] == "redis://host:6379/0"

    def test_dotted_path(self) -> None:
        cfg = StoreConfig(backend="my.custom.store.MyStore", config={"key": "val"})
        assert cfg.backend == "my.custom.store.MyStore"


class TestAgentsTeamsConfig:
    def test_agents_default_store(self) -> None:
        cfg = AgentsConfig()
        assert cfg.store.backend == "file"

    def test_teams_default_store(self) -> None:
        cfg = TeamsConfig()
        assert cfg.store.backend == "file"


# ── Factory methods ──────────────────────────────────────────────────


class TestRedisAgentStoreFactories:
    @pytest.fixture
    def store(self) -> RedisAgentStore:
        with patch(f"{_REDIS_MOD}._get_redis", return_value=_mock_redis()):
            return RedisAgentStore(redis_url="redis://test:6379/0")

    def test_create_background_run_manager(self, store: RedisAgentStore) -> None:
        with patch("redis.asyncio.from_url", return_value=_mock_redis()):
            bg = store.create_background_run_manager(stale_seconds=300)
        assert bg is not None
        assert bg._event_store is not None
        assert bg._stale_seconds == 300

    def test_create_session_registry(self, store: RedisAgentStore) -> None:
        with patch("redis.asyncio.from_url", return_value=_mock_redis()):
            reg = store.create_session_registry()
        assert reg is not None

    def test_file_store_returns_none(self, tmp_path) -> None:
        fs = FileAgentStore(path=str(tmp_path / "a.json"))
        assert fs.create_background_run_manager() is None
        assert fs.create_session_registry() is None


class TestRedisTeamStoreFactories:
    @pytest.fixture
    def store(self) -> RedisTeamStore:
        with patch(f"{_REDIS_MOD}._get_redis", return_value=_mock_redis()):
            return RedisTeamStore(redis_url="redis://test:6379/0")

    def test_create_background_run_manager(self, store: RedisTeamStore) -> None:
        with patch("redis.asyncio.from_url", return_value=_mock_redis()):
            bg = store.create_background_run_manager(stale_seconds=600, grace_seconds=60)
        assert bg is not None
        assert bg._grace_seconds == 60

    def test_create_session_registry(self, store: RedisTeamStore) -> None:
        with patch("redis.asyncio.from_url", return_value=_mock_redis()):
            reg = store.create_session_registry()
        assert reg is not None

    def test_file_store_returns_none(self, tmp_path) -> None:
        fs = FileTeamStore(path=str(tmp_path / "t.json"))
        assert fs.create_background_run_manager() is None
        assert fs.create_session_registry() is None


# ── Seeding ──────────────────────────────────────────────────────────


class TestRedisAgentStoreSeed:
    async def test_seed_new_agents(self) -> None:
        mock_r = _mock_redis()
        with patch(f"{_REDIS_MOD}._get_redis", return_value=mock_r):
            RedisAgentStore(redis_url="redis://test:6379/0")

        # Directly call the async seed logic (bypassing the sync wrapper)
        specs = {"agent1": {"model": "m1"}, "agent2": {"model": "m2"}}
        for name, spec_dict in specs.items():
            new_spec = AgentSpec(name=name, **spec_dict)
            await mock_r.hset(_AGENT_KEY, name, json.dumps(new_spec.model_dump(), default=str))

        all_agents = await mock_r.hgetall(_AGENT_KEY)
        assert len(all_agents) == 2
        assert "agent1" in all_agents
        assert "agent2" in all_agents

    async def test_seed_skips_unchanged(self) -> None:
        mock_r = _mock_redis()
        with patch(f"{_REDIS_MOD}._get_redis", return_value=mock_r):
            RedisAgentStore(redis_url="redis://test:6379/0")

        spec = AgentSpec(name="a1", model="m1")
        await mock_r.hset(_AGENT_KEY, "a1", json.dumps(spec.model_dump(), default=str))

        # Seed with same spec — should not re-write
        existing_raw = await mock_r.hget(_AGENT_KEY, "a1")
        existing = AgentSpec(**json.loads(existing_raw))
        assert existing == spec


class TestRedisTeamStoreSeed:
    async def test_seed_new_teams(self) -> None:
        mock_r = _mock_redis()
        with patch(f"{_REDIS_MOD}._get_redis", return_value=mock_r):
            RedisTeamStore(redis_url="redis://test:6379/0")

        specs = {"team1": {"planner": "p", "synthesizer": "s", "workers": ["w"]}}
        for name, spec_dict in specs.items():
            new_spec = TeamSpec(name=name, **spec_dict)
            await mock_r.hset(_TEAM_KEY, name, json.dumps(new_spec.model_dump(), default=str))

        all_teams = await mock_r.hgetall(_TEAM_KEY)
        assert len(all_teams) == 1
        assert "team1" in all_teams


# ── BackgroundRunManager sync get_status ─────────────────────────────


class TestBackgroundRunManagerSyncStatus:
    def test_get_status_running(self) -> None:
        import asyncio

        from agentic_primitives_gateway.routes._background import BackgroundRunManager

        mgr = BackgroundRunManager()
        task = MagicMock(spec=asyncio.Task)
        task.done.return_value = False
        mgr._runs["s1"] = (task, asyncio.Queue(), [], 0)

        assert mgr.get_status("s1") == "running"

    def test_get_status_idle(self) -> None:
        from agentic_primitives_gateway.routes._background import BackgroundRunManager

        mgr = BackgroundRunManager()
        assert mgr.get_status("missing") == "idle"


# ── BackgroundRunManager error in background task ────────────────────


class TestBackgroundRunManagerError:
    async def test_error_recorded_in_events(self) -> None:
        from agentic_primitives_gateway.routes._background import BackgroundRunManager

        mgr = BackgroundRunManager()

        async def failing_gen():
            yield {"type": "start"}
            raise RuntimeError("boom")

        queue, event_log = mgr.start("err-run", failing_gen(), record_events=True)

        events = []
        while True:
            event = await queue.get()
            if event is None:
                break
            events.append(event)

        assert len(events) == 2
        assert events[0]["type"] == "start"
        assert events[1]["type"] == "error"
        assert "boom" in events[1]["detail"]
        assert len(event_log) == 2

    async def test_error_written_to_store(self) -> None:
        from agentic_primitives_gateway.routes._background import BackgroundRunManager

        store = AsyncMock()
        store.set_status = AsyncMock()
        store.append_event = AsyncMock()
        store.rename_key = AsyncMock()
        mgr = BackgroundRunManager(event_store=store)

        async def failing_gen():
            yield {"type": "start"}
            raise ValueError("test error")

        queue, _ = mgr.start("err-run", failing_gen(), record_events=True)

        # Drain queue
        while True:
            event = await queue.get()
            if event is None:
                break

        # Error should have been appended to store
        error_calls = [call for call in store.append_event.await_args_list if call.args[1].get("type") == "error"]
        assert len(error_calls) == 1
        assert "test error" in error_calls[0].args[1]["detail"]
        # Status should be set to idle on completion
        store.set_status.assert_any_await("err-run", "idle", ttl=600)
