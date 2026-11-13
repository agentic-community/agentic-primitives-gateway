"""Tests for RedisEventStore and BackgroundRunManager async methods."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_primitives_gateway.routes._background import (
    BackgroundRunManager,
    RedisEventStore,
    run_event_key,
)


def _mock_redis() -> AsyncMock:
    """Mock async Redis with string and list operations."""
    strings: dict[str, str] = {}
    lists: dict[str, list[str]] = {}
    r = AsyncMock()

    async def set_cmd(key, value, ex=None, nx=False):
        if nx and key in strings:
            return None
        strings[key] = value
        return True

    async def get_cmd(key):
        return strings.get(key)

    async def rpush(key, value):
        lists.setdefault(key, []).append(value)

    async def lrange(key, start, stop):
        items = lists.get(key, [])
        if stop == -1:
            return items[start:]
        return items[start : stop + 1]

    async def expire(key, ttl):
        pass

    async def delete(*keys):
        for k in keys:
            strings.pop(k, None)
            lists.pop(k, None)

    async def eval_cmd(script, numkeys, *params):
        keys, args = params[:numkeys], params[numkeys:]
        if strings.get(keys[0]) != args[0]:
            return 0
        if numkeys == 2 and "rpush" in script:
            lists.setdefault(keys[1], []).append(args[1])
            return 1
        if numkeys == 2 and "set" in script:
            strings[keys[1]] = args[1]
            return 1
        if "expire" in script:
            return 1
        del strings[keys[0]]
        return 1

    r.set = AsyncMock(side_effect=set_cmd)
    r.get = AsyncMock(side_effect=get_cmd)
    r.rpush = AsyncMock(side_effect=rpush)
    r.lrange = AsyncMock(side_effect=lrange)
    r.expire = AsyncMock(side_effect=expire)
    r.delete = AsyncMock(side_effect=delete)
    r.eval = AsyncMock(side_effect=eval_cmd)
    return r


class TestRedisEventStore:
    @pytest.fixture
    def store(self) -> RedisEventStore:
        mock_r = _mock_redis()
        with patch("redis.asyncio.from_url", return_value=mock_r):
            s = RedisEventStore(redis_url="redis://test:6379/0")
            s._redis = mock_r
            return s

    async def test_set_and_get_status(self, store: RedisEventStore) -> None:
        await store.set_status("run1", "running")
        assert await store.get_status("run1") == "running"

    async def test_get_status_missing(self, store: RedisEventStore) -> None:
        assert await store.get_status("missing") is None

    async def test_append_and_get_events(self, store: RedisEventStore) -> None:
        await store.append_event("run1", {"type": "start"})
        await store.append_event("run1", {"type": "done"})

        events = await store.get_events("run1")
        assert len(events) == 2
        assert events[0]["type"] == "start"
        assert events[1]["type"] == "done"

    async def test_get_events_empty(self, store: RedisEventStore) -> None:
        events = await store.get_events("missing")
        assert events == []

    async def test_delete(self, store: RedisEventStore) -> None:
        await store.set_status("run1", "running")
        await store.append_event("run1", {"type": "start"})
        await store.delete("run1")

        assert await store.get_status("run1") is None
        assert await store.get_events("run1") == []

    async def test_claim_is_atomic_and_delete_preserves_owner(self, store: RedisEventStore) -> None:
        assert await store.claim_owner("agent-id:shared", "alice")
        assert not await store.claim_owner("agent-id:shared", "bob")
        assert await store.claim_owner("agent-id:shared", "alice", reuse=True)
        assert not await store.claim_owner("agent-id:shared", "alice")
        await store.append_event("agent-id:shared", {"type": "done"})
        await store.delete("agent-id:shared")
        assert await store.get_events("agent-id:shared") == []
        assert await store.get_owner("agent-id:shared") == "alice"
        assert not await store.claim_owner("agent-id:shared", "bob")

    async def test_new_turn_clears_events_but_not_owner(self, store: RedisEventStore) -> None:
        await store.claim_owner("event:owned", "alice")
        await store.append_event("event:owned", {"type": "token", "content": "old"})
        await store.clear_events("event:owned")
        assert await store.get_events("event:owned") == []
        assert await store.get_owner("event:owned") == "alice"


class TestBackgroundRunManagerAsync:
    async def test_get_status_async_local_running(self) -> None:
        mgr = BackgroundRunManager()
        task = MagicMock(spec=asyncio.Task)
        task.done.return_value = False
        mgr._runs["s1"] = (task, asyncio.Queue(), [], 0)

        assert await mgr.get_status_async("s1") == "running"

    async def test_get_status_async_falls_back_to_store_idle(self) -> None:
        """Non-running Redis statuses are trusted (e.g. completed runs)."""
        store = AsyncMock()
        store.get_status = AsyncMock(return_value="idle")
        mgr = BackgroundRunManager(event_store=store)

        assert await mgr.get_status_async("s1") == "idle"
        store.get_status.assert_awaited_once_with("s1")

    async def test_get_status_async_trusts_only_active_remote_running(self) -> None:
        store = AsyncMock()
        store.get_status = AsyncMock(return_value="running")
        store.is_active = AsyncMock(return_value=False)
        mgr = BackgroundRunManager(event_store=store)

        assert await mgr.get_status_async("s1") == "idle"
        store.is_active.return_value = True
        assert await mgr.get_status_async("s1") == "running"

    async def test_get_status_async_idle_no_store(self) -> None:
        mgr = BackgroundRunManager()
        assert await mgr.get_status_async("missing") == "idle"

    async def test_get_status_async_idle_store_empty(self) -> None:
        store = AsyncMock()
        store.get_status = AsyncMock(return_value=None)
        mgr = BackgroundRunManager(event_store=store)

        assert await mgr.get_status_async("missing") == "idle"

    async def test_get_events_async_from_store(self) -> None:
        store = AsyncMock()
        store.get_events = AsyncMock(return_value=[{"type": "start"}, {"type": "done"}])
        mgr = BackgroundRunManager(event_store=store)

        events = await mgr.get_events_async("s1")
        assert len(events) == 2

    async def test_get_events_async_falls_back_to_local(self) -> None:
        mgr = BackgroundRunManager()
        task = MagicMock(spec=asyncio.Task)
        task.done.return_value = True
        local_events = [{"type": "local_event"}]
        mgr._runs["s1"] = (task, asyncio.Queue(), local_events, 0)

        events = await mgr.get_events_async("s1")
        assert events == [{"type": "local_event"}]

    async def test_shared_redis_prevents_owner_and_type_collision(self) -> None:
        fake = _mock_redis()
        with patch("redis.asyncio.from_url", return_value=fake):
            first_store = RedisEventStore()
            second_store = RedisEventStore()
        first = BackgroundRunManager(event_store=first_store)
        second = BackgroundRunManager(event_store=second_store)
        agent = run_event_key("agent", "alice", "system", "shared", "same-id")
        team = run_event_key("team", "alice", "system", "shared", "same-id")
        assert agent != team
        assert await first.reserve(agent, "alice", claim_key="agent-id:same-id", claim_identity=agent, reuse=True)
        bob_agent = run_event_key("agent", "bob", "system", "shared", "same-id")
        assert not await second.reserve(
            bob_agent, "bob", claim_key="agent-id:same-id", claim_identity=bob_agent, reuse=True
        )
        assert await second.reserve(team, "alice", claim_key="team-id:same-id", claim_identity=team)
        assert await first.get_owner_async(agent) == "alice"
        assert await second.get_owner_async(team) == "alice"
        await first_store.append_event(agent, {"type": "token", "content": "agent secret"})
        await second_store.append_event(team, {"type": "token", "content": "team result"})
        assert await first_store.get_events(agent) == [{"type": "token", "content": "agent secret"}]
        assert await second_store.get_events(team) == [{"type": "token", "content": "team result"}]
        await first.abandon(agent)
        await second.abandon(team)

    async def test_same_user_cannot_reuse_session_id_for_another_agent(self) -> None:
        fake = _mock_redis()
        with patch("redis.asyncio.from_url", return_value=fake):
            store = RedisEventStore()
        mgr = BackgroundRunManager(event_store=store)
        first = run_event_key("agent", "alice", "system", "one", "same-id")
        other = run_event_key("agent", "alice", "system", "two", "same-id")
        assert await mgr.reserve(first, "alice", claim_key="agent-id:same-id", claim_identity=first, reuse=True)
        await mgr.abandon(first)
        assert not await mgr.reserve(other, "alice", claim_key="agent-id:same-id", claim_identity=other, reuse=True)
        assert await store.get_owner(other) is None

    async def test_stale_lease_cannot_release_replacement(self) -> None:
        fake = _mock_redis()
        with patch("redis.asyncio.from_url", return_value=fake):
            store = RedisEventStore()
        assert await store.claim_active("session", "old-token")
        await fake.delete("run:session:active")  # Simulate an expired lease.
        assert await store.claim_active("session", "new-token")
        await store.release_active("session", "old-token")
        assert not await store.claim_active("session", "third-token")
        await store.release_active("session", "new-token")
        assert await store.claim_active("session", "third-token")

    async def test_heartbeat_only_renews_owned_lease(self) -> None:
        fake = _mock_redis()
        with patch("redis.asyncio.from_url", return_value=fake):
            store = RedisEventStore()
        assert await store.claim_active("session", "producer")
        assert await store.refresh_active("session", "producer")
        assert not await store.refresh_active("session", "intruder")
        await fake.delete("run:session:active")
        assert not await store.refresh_active("session", "producer")

    async def test_running_producer_renews_lease_and_stops_if_lost(self) -> None:
        fake = _mock_redis()
        with patch("redis.asyncio.from_url", return_value=fake):
            first_store = RedisEventStore()
            second_store = RedisEventStore()
        first = BackgroundRunManager(stale_seconds=3, event_store=first_store)
        second = BackgroundRunManager(stale_seconds=3, event_store=second_store)
        key = run_event_key("agent", "alice", "system", "shared", "active")
        claim = {"claim_key": "agent-id:active", "claim_identity": key, "reuse": True}
        assert await first.reserve(key, "alice", **claim)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def work():
            entered.set()
            await release.wait()
            yield {"type": "done"}

        async def renewal_observed():
            while fake.eval.await_count == 0:
                await asyncio.sleep(0.01)

        queue, _ = first.start(key, work(), owner_id="alice", record_events=True)
        try:
            await entered.wait()
            await asyncio.wait_for(renewal_observed(), timeout=2)
            assert not await second.reserve(key, "alice", **claim)
            await fake.delete(f"run:{key}:active")
            assert await asyncio.wait_for(queue.get(), timeout=3) is None
            await asyncio.wait_for(first.runs[key][0], timeout=3)
            assert await second.reserve(key, "alice", **claim)
        finally:
            release.set()
            await first.abandon(key)
            await second.abandon(key)

    async def test_repeat_turn_rejects_parallel_request_and_resets_replay(self) -> None:
        fake = _mock_redis()
        with patch("redis.asyncio.from_url", return_value=fake):
            first_store = RedisEventStore()
            second_store = RedisEventStore()
        first = BackgroundRunManager(event_store=first_store)
        second = BackgroundRunManager(event_store=second_store)
        key = run_event_key("agent", "alice", "system", "shared", "repeat")
        assert await first.reserve(key, "alice", claim_key="agent-id:repeat", claim_identity=key, reuse=True)
        await first_store.append_event(key, {"type": "token", "content": "previous"})
        assert not await second.reserve(key, "alice", claim_key="agent-id:repeat", claim_identity=key, reuse=True)
        assert await second.get_events_async(key) == [{"type": "token", "content": "previous"}]
        await first.abandon(key)
        assert await second.reserve(key, "alice", claim_key="agent-id:repeat", claim_identity=key, reuse=True)
        await second.reset_events(key)
        assert await second.get_events_async(key) == []
        await second.abandon(key)
