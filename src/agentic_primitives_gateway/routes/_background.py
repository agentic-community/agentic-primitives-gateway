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
import contextvars
import json
import logging
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)

# Type for entries: (task, queue, event_log, started_at)
RunEntry = tuple[asyncio.Task, asyncio.Queue, list[dict[str, Any]], float]


def run_event_key(kind: str, owner_id: str, resource_owner: str, resource_name: str, run_id: str) -> str:
    """Unambiguous run key, scoped to the type, caller, and resolved spec."""
    return "event:" + json.dumps([kind, owner_id, resource_owner, resource_name, run_id], separators=(",", ":"))


# ── Event store abstraction ──────────────────────────────────────────


class EventStore(ABC):
    """Pluggable event persistence for background runs."""

    @abstractmethod
    async def set_status(self, key: str, status: str, ttl: int = 600) -> None: ...

    async def set_status_owned(self, key: str, token: str, status: str, ttl: int = 600) -> bool:
        """Set status only for the current active lease owner."""
        await self.set_status(key, status, ttl=ttl)
        return True

    @abstractmethod
    async def get_status(self, key: str) -> str | None: ...

    @abstractmethod
    async def append_event(self, key: str, event: dict[str, Any], ttl: int = 600) -> None: ...

    async def append_event_owned(self, key: str, token: str, event: dict[str, Any], ttl: int = 600) -> bool:
        """Append only for the current active lease owner."""
        await self.append_event(key, event, ttl=ttl)
        return True

    @abstractmethod
    async def get_events(self, key: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def clear_events(self, key: str) -> None:
        """Clear prior events without releasing the owner claim."""

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    async def claim_owner(self, key: str, owner_id: str, *, reuse: bool = False) -> bool:
        """Atomically claim a run ID; reuse permits only the existing owner."""

    @abstractmethod
    async def get_owner(self, key: str) -> str | None: ...

    async def add_to_index(self, index_key: str, run_id: str, ttl: int = 86400) -> None:  # noqa: B027
        """Add a run ID to a named index (e.g. per-team run list). Default is no-op."""

    async def get_index(self, index_key: str) -> list[str]:
        """Get all run IDs in a named index. Default returns empty list."""
        return []

    async def claim_active(self, key: str, token: str, ttl: int = 600) -> bool:
        """Exclude overlapping turns on different replicas."""
        return True

    async def refresh_active(self, key: str, token: str, ttl: int = 600) -> bool:
        """Renew only the producer's current lease."""
        return True

    async def is_active(self, key: str) -> bool:
        """Whether any replica still holds a live producer lease."""
        return False

    async def release_active(self, key: str, token: str) -> None:  # noqa: B027
        """Release only the lease held by this producer."""


class RedisEventStore(EventStore):
    """Redis-backed event persistence for cross-replica visibility."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        import redis.asyncio

        self._redis = redis.asyncio.from_url(redis_url, decode_responses=True)
        logger.info("RedisEventStore initialized (url=%s)", redis_url.split("@")[-1])

    @staticmethod
    def _status_key(key: str) -> str:
        return f"run:{key}:status"

    @staticmethod
    def _events_key(key: str) -> str:
        return f"run:{key}:events"

    async def set_status(self, key: str, status: str, ttl: int = 600) -> None:
        await self._redis.set(self._status_key(key), status, ex=ttl)

    async def set_status_owned(self, key: str, token: str, status: str, ttl: int = 600) -> bool:
        return bool(
            await self._redis.eval(
                'if redis.call("get", KEYS[1]) ~= ARGV[1] then return 0 end '
                'return redis.call("set", KEYS[2], ARGV[2], "EX", ARGV[3]) and 1 or 0',
                2,
                f"run:{key}:active",
                self._status_key(key),
                token,
                status,
                ttl,
            )
        )

    async def get_status(self, key: str) -> str | None:
        result = await self._redis.get(self._status_key(key))
        return str(result) if result is not None else None

    async def append_event(self, key: str, event: dict[str, Any], ttl: int = 600) -> None:
        events_key = self._events_key(key)
        await self._redis.rpush(events_key, json.dumps(event, default=str))
        await self._redis.expire(events_key, ttl)

    async def append_event_owned(self, key: str, token: str, event: dict[str, Any], ttl: int = 600) -> bool:
        return bool(
            await self._redis.eval(
                'if redis.call("get", KEYS[1]) ~= ARGV[1] then return 0 end '
                'redis.call("rpush", KEYS[2], ARGV[2]); '
                'return redis.call("expire", KEYS[2], ARGV[3])',
                2,
                f"run:{key}:active",
                self._events_key(key),
                token,
                json.dumps(event, default=str),
                ttl,
            )
        )

    async def get_events(self, key: str) -> list[dict[str, Any]]:
        raw_list = await self._redis.lrange(self._events_key(key), 0, -1)
        return [json.loads(r) for r in raw_list]

    @staticmethod
    def _owner_key(key: str) -> str:
        return f"run:{key}:owner"

    async def claim_owner(self, key: str, owner_id: str, *, reuse: bool = False) -> bool:
        owner_key = self._owner_key(key)
        if await self._redis.set(owner_key, owner_id, nx=True):
            return True
        return reuse and await self.get_owner(key) == owner_id

    async def get_owner(self, key: str) -> str | None:
        result = await self._redis.get(self._owner_key(key))
        return str(result) if result is not None else None

    async def claim_active(self, key: str, token: str, ttl: int = 600) -> bool:
        return bool(await self._redis.set(f"run:{key}:active", token, nx=True, ex=ttl))

    async def is_active(self, key: str) -> bool:
        return bool(await self._redis.exists(f"run:{key}:active"))

    async def refresh_active(self, key: str, token: str, ttl: int = 600) -> bool:
        return bool(
            await self._redis.eval(
                'if redis.call("get", KEYS[1]) == ARGV[1] then '
                'return redis.call("expire", KEYS[1], ARGV[2]) end return 0',
                1,
                f"run:{key}:active",
                token,
                ttl,
            )
        )

    async def release_active(self, key: str, token: str) -> None:
        await self._redis.eval(
            'if redis.call("get", KEYS[1]) == ARGV[1] then return redis.call("del", KEYS[1]) end return 0',
            1,
            f"run:{key}:active",
            token,
        )

    async def delete(self, key: str) -> None:
        # Keep the owner claim: a deleted run ID must never become claimable by
        # another principal while a checkpoint or task board may still exist.
        await self._redis.delete(self._status_key(key), self._events_key(key))

    async def clear_events(self, key: str) -> None:
        await self._redis.delete(self._status_key(key), self._events_key(key))

    async def add_to_index(self, index_key: str, run_id: str, ttl: int = 86400) -> None:
        key = f"index:{index_key}"
        await self._redis.sadd(key, run_id)
        await self._redis.expire(key, ttl)

    async def get_index(self, index_key: str) -> list[str]:
        members = await self._redis.smembers(f"index:{index_key}")
        return [member.decode("utf-8") if isinstance(member, bytes) else member for member in members]


# ── Background run manager ───────────────────────────────────────────


class BackgroundRunManager:
    """Tracks background asyncio tasks for streaming runs.

    Args:
        stale_seconds: Redis lease and event TTL; active runs renew the lease.
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
        self._started: dict[str, asyncio.Event] = {}
        self._stale_seconds = stale_seconds
        self._grace_seconds = grace_seconds
        self._event_store = event_store
        self._owners: dict[str, str] = {}
        self._claims: dict[str, str] = {}
        self._active: dict[str, str] = {}

    @property
    def runs(self) -> dict[str, RunEntry]:
        return self._runs

    def cleanup(self) -> None:
        """Remove completed runs after their replay grace period."""
        now = time.monotonic()
        to_remove = [
            key
            for key, (task, _, _, started) in self._runs.items()
            if task.done() and now - started > self._grace_seconds
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
        """Trust remote running status only while an active producer lease exists."""
        if self.is_running(key):
            return "running"
        if self._event_store:
            stored = await self._event_store.get_status(key)
            if stored == "running":
                return "running" if await self._event_store.is_active(key) else "idle"
            if stored:
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
        """Get the run owner from Redis, or the local claim in dev mode."""
        if self._event_store:
            return await self._event_store.get_owner(key)
        return self._owners.get(key)

    async def claim(
        self,
        key: str,
        owner_id: str,
        *,
        claim_key: str | None = None,
        claim_identity: str | None = None,
        reuse: bool = False,
    ) -> bool:
        """Reserve the raw ID for this identity and its scoped event key."""
        global_key = claim_key or key
        identity = claim_identity or owner_id
        if self._event_store:
            if not await self._event_store.claim_owner(global_key, identity, reuse=reuse):
                return False
            if global_key != key and not await self._event_store.claim_owner(key, owner_id, reuse=reuse):
                return False
        elif ((existing := self._claims.get(global_key)) is not None and (existing != identity or not reuse)) or (
            (existing := self._owners.get(key)) is not None and (existing != owner_id or not reuse)
        ):
            return False
        self._claims[global_key] = identity
        self._owners[global_key] = identity
        self._owners[key] = owner_id
        return True

    async def reserve(
        self,
        key: str,
        owner_id: str,
        *,
        claim_key: str | None = None,
        claim_identity: str | None = None,
        reuse: bool = False,
    ) -> bool:
        """Claim ownership and exclude overlapping runs of the same scoped ID."""
        if not await self.claim(key, owner_id, claim_key=claim_key, claim_identity=claim_identity, reuse=reuse):
            return False
        if key in self._active or self.is_running(key):
            return False
        token = uuid.uuid4().hex
        if self._event_store and not await self._event_store.claim_active(key, token, ttl=int(self._stale_seconds)):
            return False
        self._active[key] = token
        return True

    async def abandon(self, key: str) -> None:
        """Release an active reservation when no producer could be started."""
        token = self._active.pop(key, None)
        if token is not None and self._event_store:
            await self._event_store.release_active(key, token)

    async def reset_events(self, key: str) -> None:
        """Begin a new turn for the same owned session without replaying old turns."""
        if self._event_store:
            await self._event_store.clear_events(key)
        self._runs.pop(key, None)

    def start(
        self,
        key: str,
        coro: Any,
        *,
        owner_id: str | None = None,
        record_events: bool = False,
        index_key: str | None = None,
        index_run_id: str | None = None,
    ) -> tuple[asyncio.Queue, list[dict[str, Any]]]:
        """Spawn a background task that feeds events into a queue.

        Args:
            key: The run identifier (session_id or team_run_id).
            coro: An async generator that yields event dicts.
            owner_id: The authenticated user who started this run.
            record_events: If True, accumulate events for replay.
            index_key: If set, register the run in this index.
            index_run_id: Public run ID for the index entry.

        Returns:
            (queue, event_log) — the SSE generator reads from the queue.
        """
        self.cleanup()

        queue: asyncio.Queue[dict | None] = asyncio.Queue()
        event_log: list[dict[str, Any]] = []
        ctx = contextvars.copy_context()
        # All run IDs must already be claimed and scoped by the caller.
        store = self._event_store
        ttl = int(self._stale_seconds)
        run_index_key = index_key
        lease_token = self._active.get(key)

        lease_lost = False

        async def _renew_lease(producer: asyncio.Task[None]) -> None:
            nonlocal lease_lost
            assert store is not None and lease_token is not None
            try:
                while True:
                    await asyncio.sleep(max(1, ttl / 3))
                    if not await store.refresh_active(key, lease_token, ttl=ttl):
                        lease_lost = True
                        producer.cancel()
                        return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unable to renew active run lease %s", key)
                lease_lost = True
                producer.cancel()

        async def _run() -> None:
            nonlocal lease_lost
            current_key = key
            started.set()
            cancelled = False
            renewal: asyncio.Task[None] | None = None
            from agentic_primitives_gateway.agents.checkpoint import reset_producer_token, set_producer_token

            producer_cv = set_producer_token(lease_token)
            try:
                if store and lease_token is not None:
                    if not await store.set_status_owned(current_key, lease_token, "running", ttl=ttl):
                        lease_lost = True
                        return
                    if run_index_key and index_run_id:
                        await store.add_to_index(run_index_key, index_run_id, ttl=ttl)
                if store and lease_token is not None:
                    producer = asyncio.current_task()
                    assert producer is not None
                    renewal = asyncio.create_task(_renew_lease(producer))
                async for event in coro:
                    if record_events:
                        event_log.append(event)
                    if (
                        store
                        and lease_token is not None
                        and not lease_lost
                        and not await store.append_event_owned(current_key, lease_token, event, ttl=ttl)
                    ):
                        lease_lost = True
                        raise RuntimeError("Active run lease lost")
                    await queue.put(event)
            except asyncio.CancelledError:
                cancelled = True
                logger.info("Background task %s cancelled", current_key)
                if not lease_lost:
                    cancelled_evt = {"type": "cancelled"}
                    if record_events:
                        event_log.append(cancelled_evt)
                    if store and lease_token is not None:
                        await store.append_event_owned(current_key, lease_token, cancelled_evt, ttl=ttl)
                        await store.set_status_owned(current_key, lease_token, "cancelled", ttl=ttl)
                    await queue.put(cancelled_evt)
                return
            except Exception as exc:
                err = {"type": "error", "detail": str(exc)}
                if record_events:
                    event_log.append(err)
                if store and lease_token is not None and not lease_lost:
                    await store.append_event_owned(current_key, lease_token, err, ttl=ttl)
                await queue.put(err)
            finally:
                reset_producer_token(producer_cv)
                if renewal is not None:
                    renewal.cancel()
                    await asyncio.gather(renewal, return_exceptions=True)
                try:
                    if store:
                        if not cancelled and not lease_lost and lease_token is not None:
                            await store.set_status_owned(current_key, lease_token, "idle", ttl=ttl)
                        if lease_token is not None:
                            await store.release_active(current_key, lease_token)
                finally:
                    self._started.pop(current_key, None)
                    if self._active.get(current_key) == lease_token:
                        self._active.pop(current_key, None)
                    await queue.put(None)  # sentinel

        started = asyncio.Event()
        self._started[key] = started
        task = asyncio.create_task(_run(), context=ctx)
        self._runs[key] = (task, queue, event_log, time.monotonic())
        return queue, event_log

    async def cancel(self, key: str) -> bool:
        """Cancel a running background task. Returns True if cancelled."""
        entry = self._runs.get(key)
        if entry is None or entry[0].done():
            return False
        ready = self._started.get(key)
        if ready is not None:
            await ready.wait()
        if entry[0].done():
            return False
        entry[0].cancel()
        if self._event_store:
            await self._event_store.set_status(key, "cancelled")
        return True


async def reconnect_event_generator(
    bg: BackgroundRunManager,
    run_id: str,
    *,
    done_event_types: frozenset[str] = frozenset({"done", "cancelled"}),
) -> AsyncIterator[str]:
    """Reusable SSE generator for reconnect streams.

    Replays stored events, then polls for new ones until the run completes.
    Token events are throttled with a small delay so replayed batches feel streamed.
    """
    _TOKEN_TYPES = frozenset({"token", "sub_agent_token"})
    sent = 0
    idle_count = 0
    max_idle = 900  # 900 x 0.2s = 3 min
    seen_running = False

    while idle_count < max_idle:
        events = await bg.get_events_async(run_id)
        status = await bg.get_status_async(run_id)

        if status == "running":
            seen_running = True

        if len(events) > sent:
            for event in events[sent:]:
                yield f"data: {json.dumps(event, default=str)}\n\n"
                if isinstance(event, dict) and event.get("type") in _TOKEN_TYPES:
                    await asyncio.sleep(0.005)
            sent = len(events)
            idle_count = 0

            last_evt = events[-1] if events else {}
            if isinstance(last_evt, dict) and last_evt.get("type") in done_event_types:
                break
        else:
            idle_count += 1

        if status == "cancelled":
            break

        if seen_running and status == "idle" and idle_count > 25:
            break

        await asyncio.sleep(0.2)


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
