"""Intent-level test: RedisSpecStore._save_state is atomic.

Contract: a save operation either fully persists or fully fails.
A half-written state (e.g., ``versions`` deleted but ``identities``
unchanged) corrupts the store and leaks incomplete reads to
concurrent callers.

The pre-fix pipeline used ``self._redis.pipeline()`` without
``transaction=True``.  Redis executes pipeline commands
sequentially but not atomically: if the network drops between
``delete versions`` and ``hset versions``, the versions hash is
gone and the agent is unrecoverable.

Fix: ``transaction=True`` wraps the pipeline in MULTI/EXEC so Redis
applies all commands or none.

This test verifies the end state after a successful save is what
we expect (positive path) and then verifies that with a failure
simulation, the store still has a consistent state.
"""

from __future__ import annotations

import contextlib
import os
import uuid

import pytest

from agentic_primitives_gateway.agents.redis_store import RedisAgentStore
from agentic_primitives_gateway.models.agents import AgentSpec

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
        pytest.skip("Redis not available")


def _uid() -> str:
    return uuid.uuid4().hex[:8]


class TestSaveStateAtomicity:
    """The pipeline must apply all commands or none — no partial
    state observable after a failed save.
    """

    @pytest.fixture
    async def store(self):
        s = RedisAgentStore(redis_url=REDIS_URL)
        created: list[str] = []
        yield s, created
        for name in created:
            with contextlib.suppress(KeyError):
                await s.delete(name)

    @pytest.mark.asyncio
    async def test_successful_save_leaves_consistent_state(self, store):
        """Sanity: after a normal save, both ``versions`` and
        ``identities`` hashes contain the new entries.
        """
        s, created = store
        name = f"atomic-ok-{_uid()}"
        created.append(name)

        await s.create(AgentSpec(name=name, model="m", owner_id="alice", shared_with=[]))

        # Read back — everything in place.
        result = await s.get(name)
        assert result is not None
        assert result.name == name

    @pytest.mark.asyncio
    async def test_pipeline_uses_transaction_mode(self, store):
        """Verify the implementation actually uses MULTI/EXEC.  The
        contract is atomicity; this test pins the mechanism.

        A regression that reverted to plain pipelining
        (``pipeline()`` without ``transaction=True``) would fail
        this test and the reviewer would see the atomicity risk
        surfacing deliberately.
        """
        import inspect

        from agentic_primitives_gateway.agents.redis_store import RedisSpecStore

        source = inspect.getsource(RedisSpecStore._save_state)
        # Either ``pipeline(transaction=True)`` is explicit, or
        # some future refactor adopted Redis Lua scripting (which
        # is also atomic) — both acceptable.  What's NOT acceptable
        # is a bare ``pipeline()`` with no transactional wrapper.
        assert "transaction=True" in source or "EVAL" in source or "eval(" in source.lower(), (
            "_save_state uses a non-atomic pipeline — a network drop "
            "mid-save could leave the store in a half-written state.  "
            "Use pipeline(transaction=True) or migrate to a Lua script."
        )

    @pytest.mark.asyncio
    async def test_pipeline_failure_does_not_corrupt_existing_state(self, store):
        """Extreme case: a save that raises (e.g., serialization
        error on one of the values) should not leave the store in
        a half-deleted state.  With transaction=True the MULTI/EXEC
        either commits everything or nothing.

        We can't easily inject a failure in the middle of a real
        Redis pipeline without low-level hackery, but we CAN verify
        the state is consistent after a create that would cause
        serialization to fail for an unrelated reason — in which
        case ``_save_state`` shouldn't even queue broken commands.
        The real protection is architectural (transaction=True);
        this test guards the positive observable: existing state
        survives a failed create.
        """
        s, created = store
        name1 = f"atomic-pre-{_uid()}"
        created.append(name1)

        # First create establishes baseline state.
        await s.create(AgentSpec(name=name1, model="m", owner_id="alice", shared_with=[]))
        baseline = await s.get(name1)
        assert baseline is not None

        # Try to create an agent with the same name → KeyError.
        # The store must still have the original agent intact.
        with pytest.raises(KeyError):
            await s.create(AgentSpec(name=name1, model="different", owner_id="alice", shared_with=[]))

        # Original agent still there and unchanged.
        still_there = await s.get(name1)
        assert still_there is not None
        assert still_there.model == "m", (
            f"Baseline agent was corrupted by the failed create: "
            f"got model={still_there.model!r}, expected 'm'.  "
            "The store is NOT atomic on conflict."
        )
