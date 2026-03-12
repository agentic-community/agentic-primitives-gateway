"""Run checkpoint persistence for durable execution.

Checkpoints capture the full state of a running agent or team so that
another replica can resume after a crash. The checkpoint includes the
authenticated principal so the resumed run writes to the correct
user-scoped memory namespace.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class CheckpointStore(ABC):
    """Pluggable checkpoint persistence."""

    @abstractmethod
    async def save(self, key: str, data: dict[str, Any], ttl: int = 600) -> None: ...

    @abstractmethod
    async def load(self, key: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    async def acquire_lock(self, key: str, owner: str, ttl: int = 60) -> bool:
        """Try to acquire a distributed lock. Returns True if acquired."""
        ...

    @abstractmethod
    async def release_lock(self, key: str) -> None: ...

    @abstractmethod
    async def list_checkpoints(self) -> list[str]:
        """List all checkpoint keys (for orphan detection)."""
        ...


class RedisCheckpointStore(CheckpointStore):
    """Redis-backed checkpoint persistence."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        logger.info("RedisCheckpointStore initialized (url=%s)", redis_url.split("@")[-1])

    @staticmethod
    def _key(key: str) -> str:
        return f"checkpoint:{key}"

    @staticmethod
    def _lock_key(key: str) -> str:
        return f"checkpoint:{key}:lock"

    async def save(self, key: str, data: dict[str, Any], ttl: int = 600) -> None:
        await self._redis.set(
            self._key(key),
            json.dumps(data, default=str),
            ex=ttl,
        )

    async def load(self, key: str) -> dict[str, Any] | None:
        raw = await self._redis.get(self._key(key))
        if raw is None:
            return None
        result: dict[str, Any] = json.loads(raw)
        return result

    async def delete(self, key: str) -> None:
        await self._redis.delete(self._key(key), self._lock_key(key))

    async def acquire_lock(self, key: str, owner: str, ttl: int = 60) -> bool:
        result = await self._redis.set(self._lock_key(key), owner, nx=True, ex=ttl)
        return result is not None

    async def release_lock(self, key: str) -> None:
        await self._redis.delete(self._lock_key(key))

    async def list_checkpoints(self) -> list[str]:
        keys: list[str] = []
        async for key in self._redis.scan_iter(match="checkpoint:*"):
            k = str(key)
            if not k.endswith(":lock"):
                keys.append(k.removeprefix("checkpoint:"))
        return keys
