"""Run checkpoint persistence for durable execution.

Checkpoints capture the full state of a running agent or team so that
another replica can resume after a crash. The checkpoint includes the
authenticated principal so the resumed run writes to the correct
user-scoped memory namespace.

Each checkpoint stores a ``replica_id`` identifying which replica owns the
run.  A separate heartbeat key (``replica:{id}:heartbeat``) proves the
replica is alive.  On startup, orphan detection scans for checkpoints whose
replica heartbeat has expired and resumes them.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from abc import ABC, abstractmethod
from contextvars import ContextVar, Token
from typing import Any

logger = logging.getLogger(__name__)
_recovery_owner: ContextVar[str | None] = ContextVar("checkpoint_recovery_owner", default=None)
_producer_token: ContextVar[str | None] = ContextVar("checkpoint_producer_token", default=None)


def set_producer_token(token: str | None) -> Token[str | None]:
    return _producer_token.set(token)


def reset_producer_token(token: Token[str | None]) -> None:
    _producer_token.reset(token)


# ── Checkpoint store ─────────────────────────────────────────────────


class CheckpointStore(ABC):
    """Pluggable checkpoint persistence."""

    @abstractmethod
    async def save(self, key: str, data: dict[str, Any], ttl: int = 600) -> None: ...

    async def save_if_active(self, key: str, data: dict[str, Any], ttl: int = 86400) -> bool:
        """Persist only under the current run or recovery identity, never after cancel."""
        if await self.is_cancelled(key):
            return False
        owner = _recovery_owner.get()
        if owner is not None:
            return await self.save_recovery(key, owner, data, ttl=ttl)
        await self.save(key, data, ttl=ttl)
        return True

    @abstractmethod
    async def load(self, key: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    async def delete_if_owned(self, key: str) -> None:
        """Finish only the checkpoint belonging to this run generation."""
        await self.delete(key)

    @abstractmethod
    async def acquire_lock(self, key: str, owner: str, ttl: int = 60) -> bool:
        """Try to acquire a distributed lock with a unique per-attempt owner."""
        ...

    @abstractmethod
    async def release_lock(self, key: str, owner: str) -> None: ...

    async def refresh_lock(self, key: str, owner: str, ttl: int = 60) -> bool:
        """Extend only the currently held recovery lock."""
        return True

    async def save_recovery(self, key: str, owner: str, data: dict[str, Any], ttl: int = 86400) -> bool:
        """Update an existing uncancelled checkpoint only while holding its lock."""
        if await self.is_cancelled(key) or await self.load(key) is None:
            return False
        await self.save(key, data, ttl=ttl)
        return True

    @abstractmethod
    async def list_checkpoints(self) -> list[str]:
        """List all checkpoint keys (for orphan detection)."""
        ...

    @abstractmethod
    async def mark_cancelled(self, run_id: str, ttl: int = 300) -> None:
        """Signal that a run should be cancelled (cross-replica)."""
        ...

    @abstractmethod
    async def is_cancelled(self, run_id: str) -> bool:
        """Check if a run has been signalled for cancellation."""
        ...

    @abstractmethod
    async def clear_cancelled(self, run_id: str) -> None:
        """Clear a prior turn's marker only after the new run is reserved."""
        ...

    @abstractmethod
    async def set_heartbeat(self, replica_id: str, ttl: int = 30) -> None:
        """Write or refresh this replica's heartbeat."""
        ...

    @abstractmethod
    async def is_replica_alive(self, replica_id: str) -> bool:
        """Check if a replica's heartbeat key exists."""
        ...


class RedisCheckpointStore(CheckpointStore):
    """Redis-backed checkpoint persistence."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        import redis.asyncio

        self._redis: Any = redis.asyncio.from_url(redis_url, decode_responses=True)
        logger.info("RedisCheckpointStore initialized (url=%s)", redis_url.split("@")[-1])

    @staticmethod
    def _key(key: str) -> str:
        return f"checkpoint:{key}"

    @staticmethod
    def _lock_key(key: str) -> str:
        return f"checkpoint:{key}:lock"

    @staticmethod
    def _heartbeat_key(replica_id: str) -> str:
        return f"replica:{replica_id}:heartbeat"

    async def save(self, key: str, data: dict[str, Any], ttl: int = 600) -> None:
        await self._redis.set(
            self._key(key),
            json.dumps(data, default=str),
            ex=ttl,
        )

    async def save_if_active(self, key: str, data: dict[str, Any], ttl: int = 86400) -> bool:
        owner = _recovery_owner.get()
        if owner is not None:
            return await self.save_recovery(key, owner, data, ttl=ttl)
        token = _producer_token.get()
        if token is None:
            return False
        return bool(
            await self._redis.eval(
                'if redis.call("get", KEYS[1]) ~= ARGV[1] '
                'or redis.call("exists", KEYS[3]) == 1 then return 0 end '
                'return redis.call("set", KEYS[2], ARGV[2], "EX", ARGV[3]) and 1 or 0',
                3,
                f"run:{key}:active",
                self._key(key),
                self._cancel_key(key),
                token,
                json.dumps(data, default=str),
                ttl,
            )
        )

    async def load(self, key: str) -> dict[str, Any] | None:
        raw = await self._redis.get(self._key(key))
        if raw is None:
            return None
        result: dict[str, Any] = json.loads(raw)
        return result

    async def delete(self, key: str) -> None:
        await self._redis.delete(self._key(key))

    async def delete_if_owned(self, key: str) -> None:
        owner = _recovery_owner.get()
        token = _producer_token.get()
        if owner is None and token is None:
            return
        lock_or_active = self._lock_key(key) if owner is not None else f"run:{key}:active"
        await self._redis.eval(
            'if redis.call("get", KEYS[1]) == ARGV[1] then return redis.call("del", KEYS[2]) end return 0',
            2,
            lock_or_active,
            self._key(key),
            owner or token,
        )

    async def acquire_lock(self, key: str, owner: str, ttl: int = 60) -> bool:
        result = await self._redis.set(self._lock_key(key), owner, nx=True, ex=ttl)
        return result is not None

    async def release_lock(self, key: str, owner: str) -> None:
        await self._redis.eval(
            'if redis.call("get", KEYS[1]) == ARGV[1] then return redis.call("del", KEYS[1]) end return 0',
            1,
            self._lock_key(key),
            owner,
        )

    async def refresh_lock(self, key: str, owner: str, ttl: int = 60) -> bool:
        return bool(
            await self._redis.eval(
                'if redis.call("get", KEYS[1]) == ARGV[1] then '
                'return redis.call("expire", KEYS[1], ARGV[2]) end return 0',
                1,
                self._lock_key(key),
                owner,
                ttl,
            )
        )

    async def save_recovery(self, key: str, owner: str, data: dict[str, Any], ttl: int = 86400) -> bool:
        return bool(
            await self._redis.eval(
                'if redis.call("get", KEYS[1]) ~= ARGV[1] '
                'or redis.call("exists", KEYS[2]) == 0 '
                'or redis.call("exists", KEYS[3]) == 1 then return 0 end '
                'return redis.call("set", KEYS[2], ARGV[2], "EX", ARGV[3]) and 1 or 0',
                3,
                self._lock_key(key),
                self._key(key),
                self._cancel_key(key),
                owner,
                json.dumps(data, default=str),
                ttl,
            )
        )

    async def list_checkpoints(self) -> list[str]:
        keys: list[str] = []
        async for key in self._redis.scan_iter(match="checkpoint:*"):
            k = str(key)
            if k.endswith(":lock") or k.startswith("checkpoint:cancel:"):
                continue
            keys.append(k.removeprefix("checkpoint:"))
        return keys

    async def set_heartbeat(self, replica_id: str, ttl: int = 30) -> None:
        await self._redis.set(self._heartbeat_key(replica_id), "alive", ex=ttl)

    async def is_replica_alive(self, replica_id: str) -> bool:
        return bool(await self._redis.exists(self._heartbeat_key(replica_id)))

    @staticmethod
    def _cancel_key(run_id: str) -> str:
        return f"checkpoint:cancel:{run_id}"

    async def mark_cancelled(self, run_id: str, ttl: int = 300) -> None:
        await self._redis.set(self._cancel_key(run_id), "1", ex=ttl)

    async def is_cancelled(self, run_id: str) -> bool:
        return bool(await self._redis.exists(self._cancel_key(run_id)))

    async def clear_cancelled(self, run_id: str) -> None:
        await self._redis.delete(self._cancel_key(run_id))


# ── Replica heartbeat ────────────────────────────────────────────────


class ReplicaHeartbeat:
    """Periodically refreshes a heartbeat key and scans for orphaned runs.

    Other replicas check this key to determine if a run's owning replica
    is still alive.  If the heartbeat expires (TTL), the checkpoint is
    considered orphaned and eligible for recovery.

    Also runs a periodic orphan scan to recover runs from crashed replicas
    (including quick restarts of this replica).
    """

    def __init__(
        self,
        store: CheckpointStore,
        replica_id: str | None = None,
        ttl: int = 30,
        interval: int = 15,
        orphan_scan_interval: int = 60,
    ) -> None:
        self.replica_id = replica_id or uuid.uuid4().hex[:12]
        self._store = store
        self._ttl = ttl
        self._interval = interval
        self._orphan_scan_interval = orphan_scan_interval
        self._runner: Any | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._orphan_task: asyncio.Task[None] | None = None

    def set_runner(self, runner: Any, team_runner: Any | None = None) -> None:
        """Set the runner references for orphan recovery."""
        self._runner = runner
        self._team_runner = team_runner

    async def start(self) -> None:
        """Start the heartbeat and orphan scan loops."""
        await self._store.set_heartbeat(self.replica_id, self._ttl)
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("Replica heartbeat started (id=%s, ttl=%ds)", self.replica_id, self._ttl)

    def start_orphan_scanner(self) -> None:
        """Start periodic orphan scanning (call after all init is complete)."""
        if self._runner:
            self._orphan_task = asyncio.create_task(self._orphan_loop())
            logger.info("Orphan scanner started (interval=%ds)", self._orphan_scan_interval)

    async def stop(self) -> None:
        """Stop the heartbeat and orphan scan loops."""
        for task in (self._heartbeat_task, self._orphan_task):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._heartbeat_task = None
        self._orphan_task = None

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self._store.set_heartbeat(self.replica_id, self._ttl)
            except Exception:
                logger.warning("Failed to refresh heartbeat", exc_info=True)

    async def _orphan_loop(self) -> None:
        while True:
            await asyncio.sleep(self._orphan_scan_interval)
            try:
                if self._runner:
                    await recover_orphaned_runs(
                        self._store,
                        self._runner,
                        self.replica_id,
                        team_runner=getattr(self, "_team_runner", None),
                    )
            except Exception:
                logger.warning("Orphan scan failed", exc_info=True)


# ── Recovery task tracking ────────────────────────────────────────────

# Tracks active recovery tasks so they can be cancelled.
# Keyed by the full run identity (kind, caller, resource, and run ID).
_recovery_tasks: dict[str, asyncio.Task[bool]] = {}


def cancel_recovery_task(key: str) -> bool:
    """Cancel only the recovery task for this exact, authorized run key."""
    task = _recovery_tasks.get(key)
    if task is None:
        return False
    if not task.done():
        task.cancel()
        logger.info("Cancelled recovery task for %s", key)
        return True
    _recovery_tasks.pop(key, None)
    return False


@contextlib.asynccontextmanager
async def recovery_lock(store: CheckpointStore, key: str, replica_id: str):
    """Hold a unique, renewable recovery lock until the run stops."""
    owner = f"{replica_id}:{uuid.uuid4().hex}"
    if not await store.acquire_lock(key, owner):
        yield None
        return
    producer = asyncio.current_task()
    assert producer is not None

    async def renew() -> None:
        try:
            while True:
                await asyncio.sleep(20)
                if not await store.refresh_lock(key, owner):
                    producer.cancel()
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Recovery lock renewal failed for %s", key)
            producer.cancel()

    heartbeat = asyncio.create_task(renew())
    previous = _recovery_owner.set(owner)
    try:
        yield owner
    finally:
        _recovery_owner.reset(previous)
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        await store.release_lock(key, owner)


# ── Orphan recovery ──────────────────────────────────────────────────


async def recover_orphaned_runs(
    store: CheckpointStore,
    agent_runner: Any,
    replica_id: str,
    team_runner: Any | None = None,
) -> int:
    """Scan for orphaned checkpoints and resume them.

    A checkpoint is orphaned if its ``replica_id`` field refers to a
    replica whose heartbeat has expired.

    Dispatches to the agent runner or team runner based on the checkpoint's
    ``type`` field (``"team"`` for teams, anything else for agents).

    Returns the number of runs recovered.
    """
    checkpoint_keys = await store.list_checkpoints()
    logger.info("Orphan scan: found %d checkpoint(s) in store", len(checkpoint_keys))
    if not checkpoint_keys:
        return 0

    # Identify orphaned checkpoints
    orphaned: list[tuple[str, dict[str, Any]]] = []
    for key in checkpoint_keys:
        data = await store.load(key)
        if data is None:
            continue

        cp_replica = data.get("replica_id")
        if not cp_replica:
            # Old checkpoint without replica tracking — treat as orphaned
            pass
        elif await store.is_replica_alive(cp_replica):
            # Owning replica is still alive — skip
            continue

        logger.info(
            "Found orphaned checkpoint: %s (replica=%s, type=%s)",
            key,
            cp_replica or "unknown",
            data.get("type", "agent"),
        )
        orphaned.append((key, data))

    if not orphaned:
        return 0

    # Shuffle so multiple replicas starting simultaneously don't all
    # try to claim the same checkpoints in the same order.
    import random

    random.shuffle(orphaned)

    # Resume orphaned runs concurrently. Each resume() acquires a
    # distributed lock internally (SET NX), so only one replica wins
    # per checkpoint — other replicas skip gracefully.
    async def _resume_one(key: str, data: dict[str, Any]) -> bool:
        try:
            if data.get("type") == "team" and team_runner is not None:
                await team_runner.resume(key)
            else:
                await agent_runner.resume(key)
            return True
        except asyncio.CancelledError:
            logger.info("Recovery of %s was cancelled", key)
            return False
        except Exception:
            logger.exception("Failed to recover orphaned run: %s", key)
            return False
        finally:
            _recovery_tasks.pop(key, None)

    # Create tasks and track them for cancellation
    tasks = []
    for key, data in orphaned:
        task = asyncio.create_task(_resume_one(key, data))
        _recovery_tasks[key] = task
        tasks.append(task)

    results = await asyncio.gather(*tasks)
    recovered = sum(1 for r in results if r)

    if recovered:
        logger.info("Recovered %d orphaned run(s)", recovered)
    return recovered
