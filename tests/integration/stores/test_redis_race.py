"""Intent-level race-condition tests for the Redis-backed agent store.

The contract in ``CLAUDE.md`` (Multi-Replica Capable → Atomic operations):

    ``HSETNX`` for agent/team creation prevents race conditions.
    Distributed locking (``SET NX``) for checkpoint recovery prevents
    multiple replicas claiming the same orphan.

Existing integration tests verify single-replica happy paths only.
Nothing asserts the stated atomicity under concurrent creates.  These
tests fire two or more replicas at the same agent name simultaneously
and assert the contract: exactly one ``create()`` succeeds, the rest
raise ``KeyError("already exists")``.  If current code is a racy
load-mutate-save (as ``redis_store.py:6-8`` warns about), these tests
will catch it.

Each test uses a unique agent name per run so failures are reproducible
and cleanup is deterministic.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import uuid

import pytest

from agentic_primitives_gateway.agents.redis_store import RedisAgentStore, RedisTeamStore
from agentic_primitives_gateway.models.agents import AgentSpec
from agentic_primitives_gateway.models.teams import TeamSpec

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
        pytest.skip("Redis not available — skipping Redis race-condition tests")


def _uid() -> str:
    return uuid.uuid4().hex[:8]


class TestRedisAgentStoreCreateIsAtomic:
    """Two replicas racing to create the same agent → exactly one wins."""

    @pytest.fixture
    async def cleanup(self):
        """Yield a list we append agent names to; all get deleted after."""
        created: list[str] = []
        yield created
        # Use a fresh store for cleanup so we don't depend on any
        # particular test-local store state.
        store = RedisAgentStore(redis_url=REDIS_URL)
        for name in created:
            with contextlib.suppress(KeyError):
                await store.delete(name)

    @pytest.mark.asyncio
    async def test_concurrent_create_same_name_exactly_one_wins(self, cleanup):
        """Two independent RedisAgentStore instances (simulating two
        replicas connected to the same Redis) call ``create()`` on the
        same agent name concurrently.  Contract: exactly one succeeds,
        the other raises ``KeyError``.

        A load-mutate-save implementation will fail this because both
        replicas read the same "not-exists" snapshot, both compute a
        new state, and both write — the last write wins and both
        callers report success.
        """
        name = f"race-create-{_uid()}"
        cleanup.append(name)

        store_a = RedisAgentStore(redis_url=REDIS_URL)
        store_b = RedisAgentStore(redis_url=REDIS_URL)

        spec_a = AgentSpec(name=name, model="m-alice", owner_id="alice", shared_with=[])
        spec_b = AgentSpec(name=name, model="m-bob", owner_id="alice", shared_with=[])

        async def try_create(store, spec) -> tuple[str, Exception | None]:
            try:
                await store.create(spec)
                return ("ok", None)
            except Exception as exc:
                return ("fail", exc)

        # Fire both concurrently.
        results = await asyncio.gather(
            try_create(store_a, spec_a),
            try_create(store_b, spec_b),
        )

        outcomes = [r[0] for r in results]
        failures = [r[1] for r in results if r[1] is not None]

        # Contract: exactly one success, exactly one failure.
        assert outcomes.count("ok") == 1, (
            f"Expected exactly one concurrent create to win — got {outcomes.count('ok')} wins. "
            f"Failures: {failures}.  If this says 2 wins, the Redis store's create() is "
            "running a load-mutate-save that silently permits duplicate writes — the "
            "contract ('HSETNX prevents races') is not delivered."
        )
        assert outcomes.count("fail") == 1
        assert isinstance(failures[0], KeyError)
        assert "already exists" in str(failures[0]).lower()

        # Sanity: exactly one version landed in Redis.
        final = await store_a.get(name)
        assert final is not None
        # The winner's model persisted; nothing silently overwrote it.
        assert final.model in ("m-alice", "m-bob")

    @pytest.mark.asyncio
    async def test_concurrent_create_ten_replicas_exactly_one_wins(self, cleanup):
        """Ten concurrent creates against the same name → 1 success, 9 failures."""
        name = f"race-create-10-{_uid()}"
        cleanup.append(name)

        n = 10
        stores = [RedisAgentStore(redis_url=REDIS_URL) for _ in range(n)]
        specs = [AgentSpec(name=name, model=f"m-{i}", owner_id="alice", shared_with=[]) for i in range(n)]

        async def try_create(store, spec):
            try:
                await store.create(spec)
                return "ok"
            except KeyError:
                return "fail"
            except Exception as exc:
                return f"error:{type(exc).__name__}"

        results = await asyncio.gather(*[try_create(s, sp) for s, sp in zip(stores, specs, strict=False)])

        ok_count = results.count("ok")
        fail_count = results.count("fail")
        other = [r for r in results if r not in ("ok", "fail")]

        assert ok_count == 1, (
            f"Expected 1 winner, got {ok_count}.  Results: {results}. "
            "Under-delivered atomicity — multiple concurrent creates succeeded."
        )
        assert fail_count == n - 1
        assert other == [], f"Unexpected error types: {other}"


class TestRedisTeamStoreCreateIsAtomic:
    """Same contract for the team store."""

    @pytest.fixture
    async def cleanup(self):
        created: list[str] = []
        yield created
        store = RedisTeamStore(redis_url=REDIS_URL)
        for name in created:
            with contextlib.suppress(KeyError):
                await store.delete(name)

    @pytest.mark.asyncio
    async def test_concurrent_create_same_team_exactly_one_wins(self, cleanup):
        name = f"race-team-{_uid()}"
        cleanup.append(name)

        store_a = RedisTeamStore(redis_url=REDIS_URL)
        store_b = RedisTeamStore(redis_url=REDIS_URL)

        spec = TeamSpec(name=name, planner="p", synthesizer="s", workers=["w"], owner_id="alice")

        async def try_create(store):
            try:
                await store.create(spec)
                return "ok"
            except KeyError:
                return "fail"

        results = await asyncio.gather(try_create(store_a), try_create(store_b))

        assert results.count("ok") == 1, (
            f"Team store: expected exactly one winner, got {results}. Atomicity contract under-delivered."
        )
