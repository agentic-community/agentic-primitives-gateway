"""Shared utilities for background streaming runs.

Both agent chat and team run streaming endpoints decouple the run from the
HTTP connection by spawning a background ``asyncio.Task`` that feeds events
into a queue.  This module extracts the common dict tracking, cleanup, and
SSE generator logic.

Event persistence is pluggable via ``EventStore``:
- Default (None): events stored in a local list (single-replica only).
- ``RedisEventStore``: events persisted to Redis lists, visible across replicas.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)

# Type for entries: (task, queue, event_log, started_at)
RunEntry = tuple[asyncio.Task, asyncio.Queue, list[dict[str, Any]], float]


# ── Event store abstraction ──────────────────────────────────────────


class EventStore(ABC):
    """Pluggable event persistence for background runs."""

    @abstractmethod
    async def set_status(self, key: str, status: str, ttl: int = 600) -> None: ...

    @abstractmethod
    async def get_status(self, key: str) -> str | None: ...

    @abstractmethod
    async def append_event(self, key: str, event: dict[str, Any], ttl: int = 600) -> None: ...

    @abstractmethod
    async def get_events(self, key: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    async def set_owner(self, key: str, owner_id: str, ttl: int = 600) -> None:  # noqa: B027
        """Store the owner of a run. Default is no-op."""

    async def get_owner(self, key: str) -> str | None:
        """Get the owner of a run. Default returns None."""
        return None

    async def rename_key(self, old_key: str, new_key: str) -> None:  # noqa: B027
        """Rename keys when a run ID changes. Default is no-op."""


class RedisEventStore(EventStore):
    """Redis-backed event persistence for cross-replica visibility."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        logger.info("RedisEventStore initialized (url=%s)", redis_url.split("@")[-1])

    @staticmethod
    def _status_key(key: str) -> str:
        return f"run:{key}:status"

    @staticmethod
    def _events_key(key: str) -> str:
        return f"run:{key}:events"

    async def set_status(self, key: str, status: str, ttl: int = 600) -> None:
        await self._redis.set(self._status_key(key), status, ex=ttl)

    async def get_status(self, key: str) -> str | None:
        result = await self._redis.get(self._status_key(key))
        return str(result) if result is not None else None

    async def append_event(self, key: str, event: dict[str, Any], ttl: int = 600) -> None:
        events_key = self._events_key(key)
        await self._redis.rpush(events_key, json.dumps(event, default=str))
        await self._redis.expire(events_key, ttl)

    async def get_events(self, key: str) -> list[dict[str, Any]]:
        raw_list = await self._redis.lrange(self._events_key(key), 0, -1)
        return [json.loads(r) for r in raw_list]

    @staticmethod
    def _owner_key(key: str) -> str:
        return f"run:{key}:owner"

    async def set_owner(self, key: str, owner_id: str, ttl: int = 600) -> None:
        await self._redis.set(self._owner_key(key), owner_id, ex=ttl)

    async def get_owner(self, key: str) -> str | None:
        result = await self._redis.get(self._owner_key(key))
        return str(result) if result is not None else None

    async def delete(self, key: str) -> None:
        await self._redis.delete(self._status_key(key), self._events_key(key), self._owner_key(key))

    async def rename_key(self, old_key: str, new_key: str) -> None:
        """Rename status, events, and owner keys (best-effort)."""
        for suffix in (":status", ":events", ":owner"):
            old = f"run:{old_key}{suffix}"
            new = f"run:{new_key}{suffix}"
            with contextlib.suppress(Exception):
                await self._redis.rename(old, new)


# ── Background run manager ───────────────────────────────────────────


class BackgroundRunManager:
    """Tracks background asyncio tasks for streaming runs.

    Args:
        stale_seconds: Remove runs older than this (even if still running).
        grace_seconds: Keep completed runs for this long before cleanup.
        event_store: Optional pluggable store for cross-replica event persistence.
    """

    def __init__(
        self,
        stale_seconds: float = 600,
        grace_seconds: float = 0,
        event_store: EventStore | None = None,
    ) -> None:
        self._runs: dict[str, RunEntry] = {}
        self._stale_seconds = stale_seconds
        self._grace_seconds = grace_seconds
        self._event_store = event_store

    @property
    def runs(self) -> dict[str, RunEntry]:
        return self._runs

    def cleanup(self) -> None:
        """Remove completed or stale entries from local tracking."""
        now = time.monotonic()
        to_remove = [
            key
            for key, (task, _, _, started) in self._runs.items()
            if (task.done() and (now - started > self._grace_seconds)) or (now - started > self._stale_seconds)
        ]
        for key in to_remove:
            self._runs.pop(key, None)

    def is_running(self, key: str) -> bool:
        entry = self._runs.get(key)
        return entry is not None and not entry[0].done()

    def get_status(self, key: str) -> str:
        """Check local task first, then fall back to event store."""
        if self.is_running(key):
            return "running"
        return "idle"

    async def get_status_async(self, key: str) -> str:
        """Check local task first, then fall back to Redis event store.

        A Redis status of ``"running"`` is only trusted if a local task exists
        — otherwise the run was lost to a restart and is effectively idle.
        """
        if self.is_running(key):
            return "running"
        if self._event_store:
            stored = await self._event_store.get_status(key)
            if stored and stored != "running":
                return stored
        return "idle"

    def get_events(self, key: str) -> list[dict[str, Any]]:
        """Get events from local memory."""
        entry = self._runs.get(key)
        return entry[2] if entry else []

    async def get_events_async(self, key: str) -> list[dict[str, Any]]:
        """Get events from Redis if available, else local memory."""
        if self._event_store:
            events = await self._event_store.get_events(key)
            if events:
                return events
        return self.get_events(key)

    async def get_owner_async(self, key: str) -> str | None:
        """Get the owner of a run from the event store."""
        if self._event_store:
            return await self._event_store.get_owner(key)
        return None

    def start(
        self,
        key: str,
        coro: Any,
        *,
        owner_id: str | None = None,
        record_events: bool = False,
        rekey_field: str | None = None,
    ) -> tuple[asyncio.Queue, list[dict[str, Any]]]:
        """Spawn a background task that feeds events into a queue.

        Args:
            key: The run identifier (session_id or team_run_id).
            coro: An async generator that yields event dicts.
            owner_id: The authenticated user who started this run.
            record_events: If True, accumulate events for replay.
            rekey_field: If set, watch for this field in events and re-key.

        Returns:
            (queue, event_log) — the SSE generator reads from the queue.
        """
        self.cleanup()

        queue: asyncio.Queue[dict | None] = asyncio.Queue()
        event_log: list[dict[str, Any]] = []
        ctx = contextvars.copy_context()
        manager = self
        store = self._event_store
        ttl = int(self._stale_seconds)
        run_owner = owner_id

        async def _run() -> None:
            rekeyed = False
            current_key = key
            if store:
                await store.set_status(current_key, "running", ttl=ttl)
                if run_owner:
                    await store.set_owner(current_key, run_owner, ttl=ttl)
            try:
                async for event in coro:
                    if record_events:
                        event_log.append(event)
                    if not rekeyed and rekey_field and rekey_field in event:
                        new_key = event[rekey_field]
                        manager.rekey(current_key, new_key)
                        if store:
                            await store.rename_key(current_key, new_key)
                        current_key = new_key
                        rekeyed = True
                    if store:
                        await store.append_event(current_key, event, ttl=ttl)
                    await queue.put(event)
            except asyncio.CancelledError:
                logger.info("Background task %s received CancelledError — closing generator", current_key)
                # Explicitly close the generator to trigger cleanup of child tasks
                await coro.aclose()
                cancelled_evt = {"type": "cancelled"}
                if record_events:
                    event_log.append(cancelled_evt)
                if store:
                    await store.append_event(current_key, cancelled_evt, ttl=ttl)
                    await store.set_status(current_key, "cancelled", ttl=ttl)
                await queue.put(cancelled_evt)
                await queue.put(None)
                return
            except Exception as exc:
                err = {"type": "error", "detail": str(exc)}
                if record_events:
                    event_log.append(err)
                if store:
                    await store.append_event(current_key, err, ttl=ttl)
                await queue.put(err)
            finally:
                if store:
                    await store.set_status(current_key, "idle", ttl=ttl)
                await queue.put(None)  # sentinel

        task = asyncio.create_task(_run(), context=ctx)
        self._runs[key] = (task, queue, event_log, time.monotonic())
        return queue, event_log

    async def cancel(self, key: str) -> bool:
        """Cancel a running background task. Returns True if cancelled."""
        entry = self._runs.get(key)
        if entry is None:
            logger.info("cancel(%s): no entry found in _runs (keys: %s)", key, list(self._runs.keys()))
            return False
        if entry[0].done():
            logger.info("cancel(%s): task already done", key)
            return False
        task = entry[0]
        task.cancel()
        logger.info("cancel(%s): task.cancel() called", key)
        if self._event_store:
            await self._event_store.set_status(key, "cancelled")
        return True

    def rekey(self, old_key: str, new_key: str) -> None:
        """Re-key a run entry (e.g. when team_run_id becomes known)."""
        entry = self._runs.pop(old_key, None)
        if entry is not None:
            self._runs[new_key] = entry


def sse_response(
    queue: asyncio.Queue[dict | None],
    *,
    strip_fields: frozenset[str] = frozenset(),
) -> StreamingResponse:
    """Create a StreamingResponse that reads from a background task queue."""

    async def _generate() -> AsyncIterator[str]:
        while True:
            event = await queue.get()
            if event is None:
                break
            if strip_fields:
                event = {k: v for k, v in event.items() if k not in strip_fields}
            yield f"data: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
