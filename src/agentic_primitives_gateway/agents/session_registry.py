"""Cross-replica session registry for browser and code_interpreter sessions.

Tracks which sessions are active so any replica can:
- List active sessions (observability)
- Clean up orphaned sessions (e.g., after a replica crash)

The session lifecycle is still owned by the background task on one replica.
This registry is informational — the actual session lives on the remote
backend (Selenium Hub, Jupyter Server, AgentCore).
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class SessionRegistry(ABC):
    """Tracks active browser/code_interpreter sessions across replicas."""

    @abstractmethod
    async def register(self, primitive: str, session_id: str, metadata: dict[str, Any] | None = None) -> None:
        """Register an active session."""
        ...

    @abstractmethod
    async def unregister(self, primitive: str, session_id: str) -> None:
        """Remove a session from the registry."""
        ...

    @abstractmethod
    async def list_sessions(self, primitive: str | None = None) -> list[dict[str, Any]]:
        """List all registered sessions, optionally filtered by primitive."""
        ...

    @abstractmethod
    async def is_registered(self, primitive: str, session_id: str) -> bool:
        """Check if a session is registered."""
        ...


class InMemorySessionRegistry(SessionRegistry):
    """Single-replica session registry. Default for dev."""

    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, dict[str, Any]]] = {}

    async def register(self, primitive: str, session_id: str, metadata: dict[str, Any] | None = None) -> None:
        self._sessions.setdefault(primitive, {})[session_id] = metadata or {}

    async def unregister(self, primitive: str, session_id: str) -> None:
        self._sessions.get(primitive, {}).pop(session_id, None)

    async def list_sessions(self, primitive: str | None = None) -> list[dict[str, Any]]:
        results = []
        prims = [primitive] if primitive else list(self._sessions.keys())
        for p in prims:
            for sid, meta in self._sessions.get(p, {}).items():
                results.append({"primitive": p, "session_id": sid, **meta})
        return results

    async def is_registered(self, primitive: str, session_id: str) -> bool:
        return session_id in self._sessions.get(primitive, {})


class RedisSessionRegistry(SessionRegistry):
    """Cross-replica session registry backed by Redis.

    Sessions stored as hash fields in ``sessions:{primitive}``.
    Each field is a session_id, value is JSON metadata.
    TTL ensures orphaned sessions auto-expire.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0", ttl: int = 3600) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._ttl = ttl
        logger.info("RedisSessionRegistry initialized (url=%s)", redis_url.split("@")[-1])

    @staticmethod
    def _key(primitive: str) -> str:
        return f"sessions:{primitive}"

    async def register(self, primitive: str, session_id: str, metadata: dict[str, Any] | None = None) -> None:
        key = self._key(primitive)
        await self._redis.hset(key, session_id, json.dumps(metadata or {}, default=str))
        await self._redis.expire(key, self._ttl)

    async def unregister(self, primitive: str, session_id: str) -> None:
        await self._redis.hdel(self._key(primitive), session_id)

    async def list_sessions(self, primitive: str | None = None) -> list[dict[str, Any]]:
        results = []
        if primitive:
            prims = [primitive]
        else:
            # Scan for all session keys
            prims = []
            async for key in self._redis.scan_iter(match="sessions:*"):
                prims.append(key.removeprefix("sessions:"))
        for p in prims:
            all_raw = await self._redis.hgetall(self._key(p))
            for sid, meta_raw in all_raw.items():
                meta = json.loads(meta_raw) if meta_raw else {}
                results.append({"primitive": p, "session_id": sid, **meta})
        return results

    async def is_registered(self, primitive: str, session_id: str) -> bool:
        return bool(await self._redis.hexists(self._key(primitive), session_id))
