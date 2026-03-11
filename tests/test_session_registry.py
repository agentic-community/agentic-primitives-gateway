"""Tests for InMemorySessionRegistry and RedisSessionRegistry."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agentic_primitives_gateway.agents.session_registry import (
    InMemorySessionRegistry,
    RedisSessionRegistry,
)


class TestInMemorySessionRegistry:
    async def test_register_and_list(self) -> None:
        reg = InMemorySessionRegistry()
        await reg.register("browser", "s1", {"agent": "worker1"})
        await reg.register("code_interpreter", "s2")

        sessions = await reg.list_sessions()
        assert len(sessions) == 2
        assert any(s["session_id"] == "s1" and s["primitive"] == "browser" for s in sessions)
        assert any(s["session_id"] == "s2" and s["primitive"] == "code_interpreter" for s in sessions)

    async def test_list_filtered_by_primitive(self) -> None:
        reg = InMemorySessionRegistry()
        await reg.register("browser", "s1")
        await reg.register("code_interpreter", "s2")

        browser_only = await reg.list_sessions(primitive="browser")
        assert len(browser_only) == 1
        assert browser_only[0]["session_id"] == "s1"

    async def test_unregister(self) -> None:
        reg = InMemorySessionRegistry()
        await reg.register("browser", "s1")
        await reg.unregister("browser", "s1")

        assert await reg.is_registered("browser", "s1") is False
        assert await reg.list_sessions() == []

    async def test_unregister_nonexistent(self) -> None:
        reg = InMemorySessionRegistry()
        await reg.unregister("browser", "missing")  # should not raise

    async def test_is_registered(self) -> None:
        reg = InMemorySessionRegistry()
        assert await reg.is_registered("browser", "s1") is False
        await reg.register("browser", "s1")
        assert await reg.is_registered("browser", "s1") is True

    async def test_metadata_included(self) -> None:
        reg = InMemorySessionRegistry()
        await reg.register("browser", "s1", {"agent": "w1", "run_id": "r1"})

        sessions = await reg.list_sessions()
        assert sessions[0]["agent"] == "w1"
        assert sessions[0]["run_id"] == "r1"


class TestRedisSessionRegistry:
    def _mock_redis(self) -> AsyncMock:
        """Mock async Redis with hash and scan support."""
        store: dict[str, dict[str, str]] = {}
        r = AsyncMock()

        async def hset(key, field, value):
            store.setdefault(key, {})[field] = value

        async def hdel(key, field):
            store.get(key, {}).pop(field, None)

        async def hgetall(key):
            return dict(store.get(key, {}))

        async def hexists(key, field):
            return 1 if field in store.get(key, {}) else 0

        async def expire(key, ttl):
            pass

        async def scan_iter(match=None):
            for k in store:
                if match and not k.startswith(match.replace("*", "")):
                    continue
                yield k

        r.hset = AsyncMock(side_effect=hset)
        r.hdel = AsyncMock(side_effect=hdel)
        r.hgetall = AsyncMock(side_effect=hgetall)
        r.hexists = AsyncMock(side_effect=hexists)
        r.expire = AsyncMock(side_effect=expire)
        r.scan_iter = scan_iter
        return r

    @pytest.fixture
    def registry(self) -> RedisSessionRegistry:
        mock_r = self._mock_redis()
        with patch("redis.asyncio.from_url", return_value=mock_r):
            reg = RedisSessionRegistry(redis_url="redis://test:6379/0")
            reg._redis = mock_r
            return reg

    async def test_register_and_list(self, registry: RedisSessionRegistry) -> None:
        await registry.register("browser", "s1", {"agent": "w1"})
        await registry.register("code_interpreter", "s2")

        all_sessions = await registry.list_sessions()
        assert len(all_sessions) == 2

    async def test_list_filtered(self, registry: RedisSessionRegistry) -> None:
        await registry.register("browser", "s1")
        await registry.register("code_interpreter", "s2")

        browser_only = await registry.list_sessions(primitive="browser")
        assert len(browser_only) == 1
        assert browser_only[0]["session_id"] == "s1"

    async def test_unregister(self, registry: RedisSessionRegistry) -> None:
        await registry.register("browser", "s1")
        await registry.unregister("browser", "s1")
        assert await registry.is_registered("browser", "s1") is False

    async def test_is_registered(self, registry: RedisSessionRegistry) -> None:
        assert await registry.is_registered("browser", "s1") is False
        await registry.register("browser", "s1")
        assert await registry.is_registered("browser", "s1") is True

    async def test_metadata_preserved(self, registry: RedisSessionRegistry) -> None:
        await registry.register("browser", "s1", {"run_id": "r1"})
        sessions = await registry.list_sessions(primitive="browser")
        assert sessions[0]["run_id"] == "r1"
