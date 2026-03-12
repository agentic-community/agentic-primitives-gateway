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
from typing import Any

logger = logging.getLogger(__name__)

# ── Checkpoint store ─────────────────────────────────────────────────


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
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(redis_url, decode_responses=True)
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

    async def set_heartbeat(self, replica_id: str, ttl: int = 30) -> None:
        await self._redis.set(self._heartbeat_key(replica_id), "alive", ex=ttl)

    async def is_replica_alive(self, replica_id: str) -> bool:
        return bool(await self._redis.exists(self._heartbeat_key(replica_id)))


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
    if not checkpoint_keys:
        return 0

    recovered = 0
    for key in checkpoint_keys:
        data = await store.load(key)
        if data is None:
            continue

        cp_replica = data.get("replica_id")
        if not cp_replica:
            # Old checkpoint without replica tracking — try to recover
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

        try:
            if data.get("type") == "team" and team_runner is not None:
                await team_runner.resume(key)
            else:
                await agent_runner.resume(key)
            recovered += 1
        except Exception:
            logger.exception("Failed to recover orphaned run: %s", key)

    if recovered:
        logger.info("Recovered %d orphaned run(s)", recovered)
    return recovered
