"""Intent-level test: multiple replicas recovering orphans claim disjoint sets.

Contract from ``CLAUDE.md`` (Multi-Replica Capable / Checkpointing):

    ``ReplicaHeartbeat`` refreshes a TTL key every 15s and scans for
    orphaned checkpoints every 60s.  ``recover_orphaned_runs()`` uses
    distributed locking (``SET NX``) with shuffled order so multiple
    replicas don't all claim the same checkpoints.

Existing integration tests cover single-replica recovery only.  The
stated contract is that when multiple replicas simultaneously scan for
orphans, each checkpoint is resumed exactly once across the fleet —
not once per replica (which would mean every orphan gets resumed N
times) and not zero times (which would mean all replicas collide on
the first lock).  This test drives that two-replica race against real
Redis and asserts the disjoint-claim invariant.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import uuid
from unittest.mock import AsyncMock

import pytest

from agentic_primitives_gateway.agents.checkpoint import RedisCheckpointStore, recover_orphaned_runs

pytestmark = pytest.mark.integration

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def _redis_available() -> bool:
    try:
        import redis

        r = redis.from_url(REDIS_URL)
        r.ping()
        r.close()
        return True
    except Exception:
        return False


@pytest.fixture(autouse=True)
def _skip_without_redis():
    if not _redis_available():
        pytest.skip("Redis not available — skipping checkpoint recovery race tests")


def _uid() -> str:
    return uuid.uuid4().hex[:8]


class TestTwoReplicasDisjointClaims:
    @pytest.fixture
    async def store(self):
        s = RedisCheckpointStore(redis_url=REDIS_URL)
        created: list[str] = []
        yield s, created
        for key in created:
            with contextlib.suppress(Exception):
                await s.delete(key)

    @pytest.mark.asyncio
    async def test_two_replicas_each_claim_disjoint_orphans(self, store):
        """Seed 6 orphaned checkpoints; two live replicas run
        ``recover_orphaned_runs`` concurrently.  Contract: each
        checkpoint is recovered exactly once across the whole fleet
        (never double-claimed, never skipped).

        A regression that dropped ``SET NX`` (or that used non-atomic
        check-then-set) would cause at least one checkpoint to be
        claimed by both replicas — the assertion catches that.
        """
        s, created = store
        uid = _uid()
        n_orphans = 6
        keys = [f"replica-race-{uid}-{i}" for i in range(n_orphans)]

        # Seed orphaned checkpoints.
        for key in keys:
            created.append(key)
            await s.save(
                key,
                {
                    "spec_name": "test-agent",
                    "session_id": key,
                    "actor_id": "test-agent:u:alice",
                    "memory_ns": "agent:test-agent:u:alice",
                    "trace_id": f"trace-{key}",
                    "depth": 0,
                    "prev_overrides": {},
                    "session_ids": {},
                    "messages": [{"role": "user", "content": "hi"}],
                    "turns_used": 0,
                    "tools_called": [],
                    "content": "",
                    "original_message": "hi",
                    "replica_id": "dead-replica",  # not alive
                    "principal": {
                        "id": "alice",
                        "type": "user",
                        "groups": [],
                        "scopes": [],
                    },
                },
                ttl=300,
            )

        # Two live replicas, each with a resume() that records which
        # checkpoint keys it saw AND competes for the distributed lock
        # the same way the real runner does.
        claimed_by_a: list[str] = []
        claimed_by_b: list[str] = []

        async def make_runner(label: str, claimed_list: list[str]):
            r = AsyncMock()

            async def _resume(checkpoint_key: str) -> None:
                # Mirror the real ``AgentRunner.resume`` contract:
                # ``acquire_lock`` decides whether this replica
                # actually handles the checkpoint.  Only count it as
                # "claimed" if the lock is won.  Without this, both
                # replicas would appear to recover every orphan, which
                # would hide a missing-lock regression.
                if await s.acquire_lock(checkpoint_key, label):
                    claimed_list.append(checkpoint_key)

            r.resume = AsyncMock(side_effect=_resume)
            return r

        runner_a = await make_runner("replica-a", claimed_by_a)
        runner_b = await make_runner("replica-b", claimed_by_b)

        # Kick off both recoveries concurrently.
        _recovered_a, _recovered_b = await asyncio.gather(
            recover_orphaned_runs(s, runner_a, "replica-a"),
            recover_orphaned_runs(s, runner_b, "replica-b"),
        )

        # Release locks so the fixture cleanup can delete.
        for key in keys:
            await s.release_lock(key)

        # Every orphan was claimed exactly once — no double-claims,
        # no skips.
        all_claimed = set(claimed_by_a) | set(claimed_by_b)
        assert all_claimed == set(keys), (
            f"Every orphan should be claimed by exactly one replica. "
            f"Expected {set(keys)}, got {all_claimed}. "
            f"Missing: {set(keys) - all_claimed}"
        )

        # No checkpoint claimed by both.
        overlap = set(claimed_by_a) & set(claimed_by_b)
        assert overlap == set(), (
            f"Distributed lock failed — {overlap} was claimed by both replicas. The SET NX contract is not delivered."
        )

        # Replicas shared the work (each got at least one, unless by
        # chance the shuffle happened to give all to one).  Don't
        # over-assert here — the invariant is disjointness, not equal
        # distribution.
        assert len(claimed_by_a) + len(claimed_by_b) == n_orphans

    @pytest.mark.asyncio
    async def test_four_replicas_disjoint_claims(self, store):
        """Scale up to 4 replicas and 12 orphans.

        With more racers, a non-atomic implementation is more likely
        to produce a collision, making this a stronger regression
        guard.  Same invariant: every orphan claimed exactly once,
        zero collisions.
        """
        s, created = store
        uid = _uid()
        n_orphans = 12
        n_replicas = 4
        keys = [f"replica-race-4-{uid}-{i}" for i in range(n_orphans)]

        for key in keys:
            created.append(key)
            await s.save(
                key,
                {
                    "spec_name": "test-agent",
                    "session_id": key,
                    "actor_id": "test-agent:u:alice",
                    "memory_ns": "agent:test-agent:u:alice",
                    "trace_id": f"trace-{key}",
                    "depth": 0,
                    "prev_overrides": {},
                    "session_ids": {},
                    "messages": [],
                    "turns_used": 0,
                    "tools_called": [],
                    "content": "",
                    "original_message": "hi",
                    "replica_id": "dead-replica",
                    "principal": {"id": "alice", "type": "user", "groups": [], "scopes": []},
                },
                ttl=300,
            )

        claim_lists: list[list[str]] = [[] for _ in range(n_replicas)]

        async def make_runner(label: str, idx: int):
            r = AsyncMock()

            async def _resume(checkpoint_key: str) -> None:
                if await s.acquire_lock(checkpoint_key, label):
                    claim_lists[idx].append(checkpoint_key)

            r.resume = AsyncMock(side_effect=_resume)
            return r

        runners = [await make_runner(f"replica-{i}", i) for i in range(n_replicas)]

        await asyncio.gather(*[recover_orphaned_runs(s, runners[i], f"replica-{i}") for i in range(n_replicas)])

        for key in keys:
            await s.release_lock(key)

        # Union of all claim lists is exactly the seeded set.
        all_claimed: set[str] = set()
        for lst in claim_lists:
            all_claimed |= set(lst)
        assert all_claimed == set(keys), f"Missing: {set(keys) - all_claimed}"

        # All pairwise intersections are empty.
        for i in range(n_replicas):
            for j in range(i + 1, n_replicas):
                overlap = set(claim_lists[i]) & set(claim_lists[j])
                assert overlap == set(), f"replica-{i} and replica-{j} both claimed {overlap} — distributed lock broken"

        # Sum of all claims equals the orphan count — no duplicates
        # (if a key appeared twice in one replica's list somehow, the
        # set-based checks above would miss it; this catches it).
        total = sum(len(lst) for lst in claim_lists)
        assert total == n_orphans, f"Total claims {total} != orphan count {n_orphans}"
