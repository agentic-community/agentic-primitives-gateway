"""Tests for RedisEventStore and BackgroundRunManager async methods."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_primitives_gateway.routes._background import (
    BackgroundRunManager,
    RedisEventStore,
)


def _mock_redis() -> AsyncMock:
    """Mock async Redis with string and list operations."""
    strings: dict[str, str] = {}
    lists: dict[str, list[str]] = {}
    r = AsyncMock()

    async def set_cmd(key, value, ex=None):
        strings[key] = value

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

    async def rename(old, new):
        if old in strings:
            strings[new] = strings.pop(old)
        if old in lists:
            lists[new] = lists.pop(old)

    r.set = AsyncMock(side_effect=set_cmd)
    r.get = AsyncMock(side_effect=get_cmd)
    r.rpush = AsyncMock(side_effect=rpush)
    r.lrange = AsyncMock(side_effect=lrange)
    r.expire = AsyncMock(side_effect=expire)
    r.delete = AsyncMock(side_effect=delete)
    r.rename = AsyncMock(side_effect=rename)
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

    async def test_rename_key(self, store: RedisEventStore) -> None:
        await store.set_status("old", "running")
        await store.append_event("old", {"type": "start"})

        await store.rename_key("old", "new")

        assert await store.get_status("new") == "running"
        assert len(await store.get_events("new")) == 1
        assert await store.get_status("old") is None

    async def test_rename_missing_key(self, store: RedisEventStore) -> None:
        # Should not raise even if keys don't exist
        await store.rename_key("nonexistent", "also_nonexistent")


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

    async def test_get_status_async_ignores_stale_running(self) -> None:
        """Redis 'running' without a local task means the run was lost — return idle."""
        store = AsyncMock()
        store.get_status = AsyncMock(return_value="running")
        mgr = BackgroundRunManager(event_store=store)

        assert await mgr.get_status_async("s1") == "idle"

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

    async def test_start_with_event_store(self) -> None:
        store = AsyncMock()
        store.set_status = AsyncMock()
        store.append_event = AsyncMock()
        store.rename_key = AsyncMock()
        mgr = BackgroundRunManager(event_store=store)

        async def gen():
            yield {"type": "start", "run_id": "r1"}
            yield {"type": "done"}

        queue, _event_log = mgr.start("key1", gen(), record_events=True, rekey_field="run_id")

        # Drain the queue to let the task complete
        events = []
        while True:
            event = await queue.get()
            if event is None:
                break
            events.append(event)

        assert len(events) == 2
        store.set_status.assert_any_await("key1", "running", ttl=600)
        assert store.append_event.await_count == 2
        # Should have rekeyed
        store.rename_key.assert_awaited_once_with("key1", "r1")
