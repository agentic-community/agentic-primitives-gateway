"""Intent-level test: task get_available filters by dependencies AND suggested_worker.

Contract (per docstring in ``primitives/tasks/base.py:get_available``):

    Return tasks that are pending and have all dependencies met.
    If ``worker_name`` is given, returns tasks suggested for that
    worker first, followed by unassigned tasks.  Tasks suggested for
    *other* workers are excluded.

Existing tests cover the dependency filter (``test_get_available``,
``test_get_available_after_dependency_met``).  **Nothing tests the
``suggested_worker`` filter at all.**

A regression where ``suggested_worker`` was silently ignored would
cause workers to steal each other's suggested tasks — breaking the
team runner's routing contract.  These tests catch that.

Also adds a combined-filter test: a task with unmet deps AND a
specific ``suggested_worker`` is correctly excluded from every
worker's view, not mistakenly returned to the suggested worker.
"""

from __future__ import annotations

import pytest

from agentic_primitives_gateway.models.tasks import TaskStatus
from agentic_primitives_gateway.primitives.tasks.in_memory import InMemoryTasksProvider

RUN_ID = "run-filter-tests"


@pytest.fixture
async def provider() -> InMemoryTasksProvider:
    return InMemoryTasksProvider()


class TestSuggestedWorkerFilter:
    @pytest.mark.asyncio
    async def test_task_suggested_for_other_worker_excluded(self, provider: InMemoryTasksProvider):
        """A task with ``suggested_worker="researcher"`` is NOT
        returned to worker "coder".  This is the core routing
        contract — without it, any worker could claim any suggested
        task.
        """
        await provider.create_task(RUN_ID, "researcher's job", suggested_worker="researcher")

        coders = await provider.get_available(RUN_ID, worker_name="coder")
        assert coders == [], (
            f"Coder saw researcher's task: {[t.title for t in coders]}.  suggested_worker filter not enforced."
        )

    @pytest.mark.asyncio
    async def test_task_suggested_for_me_is_returned(self, provider: InMemoryTasksProvider):
        await provider.create_task(RUN_ID, "researcher's job", suggested_worker="researcher")

        res = await provider.get_available(RUN_ID, worker_name="researcher")
        assert len(res) == 1
        assert res[0].title == "researcher's job"

    @pytest.mark.asyncio
    async def test_unassigned_task_visible_to_all_workers(self, provider: InMemoryTasksProvider):
        """suggested_worker=None (unassigned) → every worker can see
        it.  This is how generic work gets distributed.
        """
        await provider.create_task(RUN_ID, "open work")

        res = await provider.get_available(RUN_ID, worker_name="researcher")
        coder = await provider.get_available(RUN_ID, worker_name="coder")
        assert len(res) == 1
        assert len(coder) == 1
        assert res[0].title == "open work"
        assert coder[0].title == "open work"

    @pytest.mark.asyncio
    async def test_suggested_worker_tasks_come_before_unassigned(self, provider: InMemoryTasksProvider):
        """Contract: "tasks suggested for that worker first, followed
        by unassigned tasks".  The ordering signals priority — workers
        should pick up their suggested work before generic tasks.
        """
        # Create unassigned first; suggested second.  If ordering is
        # by insertion order, the suggested one would come last — a
        # violation of the contract.
        await provider.create_task(RUN_ID, "unassigned")
        await provider.create_task(RUN_ID, "mine", suggested_worker="researcher")

        res = await provider.get_available(RUN_ID, worker_name="researcher")
        assert len(res) == 2
        assert res[0].title == "mine", f"Expected suggested task first, got order: {[t.title for t in res]}"
        assert res[1].title == "unassigned"

    @pytest.mark.asyncio
    async def test_worker_name_none_returns_all_ready_regardless_of_suggested(self, provider: InMemoryTasksProvider):
        """No ``worker_name`` → no filtering by suggestion.  All
        pending+ready tasks come back.  (Contract docstring says
        worker_name defaults to None and just returns ``ready``.)
        """
        await provider.create_task(RUN_ID, "for alice", suggested_worker="alice")
        await provider.create_task(RUN_ID, "for bob", suggested_worker="bob")
        await provider.create_task(RUN_ID, "open")

        res = await provider.get_available(RUN_ID)
        assert {t.title for t in res} == {"for alice", "for bob", "open"}


class TestCombinedFilters:
    @pytest.mark.asyncio
    async def test_unmet_deps_override_suggested_worker(self, provider: InMemoryTasksProvider):
        """Even if a task is suggested for worker X, it must not
        appear in X's get_available if its deps aren't done.
        """
        prereq = await provider.create_task(RUN_ID, "prereq")
        await provider.create_task(RUN_ID, "my blocked task", depends_on=[prereq.id], suggested_worker="worker-x")

        res = await provider.get_available(RUN_ID, worker_name="worker-x")
        # Only the prereq is ready (and it has no suggested_worker).
        assert len(res) == 1
        assert res[0].id == prereq.id

    @pytest.mark.asyncio
    async def test_claimed_task_not_in_available(self, provider: InMemoryTasksProvider):
        """Once a task is claimed (status != pending), it must not
        appear in anyone's get_available.  Otherwise two workers
        would pick up the same task.
        """
        t = await provider.create_task(RUN_ID, "to claim", suggested_worker="worker-x")
        claimed = await provider.claim_task(RUN_ID, t.id, "worker-x")
        assert claimed is not None

        res = await provider.get_available(RUN_ID, worker_name="worker-x")
        assert res == [], f"Claimed task still appears: {[t.title for t in res]}"

    @pytest.mark.asyncio
    async def test_done_task_not_in_available_but_deps_satisfied_now(self, provider: InMemoryTasksProvider):
        """Completing a task:
        1. Removes it from the ready set (status=done, not pending).
        2. Allows any task depending on it to become ready.
        Both must happen in one call to get_available.
        """
        t1 = await provider.create_task(RUN_ID, "first")
        t2 = await provider.create_task(RUN_ID, "second", depends_on=[t1.id])

        # Mark t1 done.
        await provider.claim_task(RUN_ID, t1.id, "worker-x")
        await provider.update_task(RUN_ID, t1.id, status=TaskStatus.DONE)

        res = await provider.get_available(RUN_ID)
        assert len(res) == 1
        assert res[0].id == t2.id  # t1 excluded (done), t2 included (deps met)
