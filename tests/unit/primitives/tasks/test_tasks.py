"""Tests for the tasks primitive (in-memory provider)."""

from __future__ import annotations

import asyncio

import pytest

from agentic_primitives_gateway.models.tasks import TaskNote, TaskStatus
from agentic_primitives_gateway.primitives.tasks.in_memory import InMemoryTasksProvider


@pytest.fixture
def provider() -> InMemoryTasksProvider:
    return InMemoryTasksProvider()


RUN_ID = "test-run-001"


@pytest.mark.asyncio
async def test_create_task(provider: InMemoryTasksProvider) -> None:
    task = await provider.create_task(RUN_ID, "Research topic", description="Find info about X")
    assert task.id
    assert task.title == "Research topic"
    assert task.description == "Find info about X"
    assert task.status == TaskStatus.PENDING
    assert task.team_run_id == RUN_ID


@pytest.mark.asyncio
async def test_get_task(provider: InMemoryTasksProvider) -> None:
    task = await provider.create_task(RUN_ID, "My task")
    fetched = await provider.get_task(RUN_ID, task.id)
    assert fetched is not None
    assert fetched.id == task.id


@pytest.mark.asyncio
async def test_get_task_not_found(provider: InMemoryTasksProvider) -> None:
    result = await provider.get_task(RUN_ID, "nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_list_tasks(provider: InMemoryTasksProvider) -> None:
    await provider.create_task(RUN_ID, "Task A", priority=1)
    await provider.create_task(RUN_ID, "Task B", priority=2)
    await provider.create_task(RUN_ID, "Task C", priority=0)

    tasks = await provider.list_tasks(RUN_ID)
    assert len(tasks) == 3
    # Should be sorted by priority descending
    assert tasks[0].title == "Task B"
    assert tasks[1].title == "Task A"
    assert tasks[2].title == "Task C"


@pytest.mark.asyncio
async def test_list_tasks_filter_status(provider: InMemoryTasksProvider) -> None:
    t1 = await provider.create_task(RUN_ID, "Pending task")
    await provider.create_task(RUN_ID, "Another pending")
    await provider.claim_task(RUN_ID, t1.id, "agent-a")

    pending = await provider.list_tasks(RUN_ID, status="pending")
    assert len(pending) == 1
    assert pending[0].title == "Another pending"

    claimed = await provider.list_tasks(RUN_ID, status="claimed")
    assert len(claimed) == 1
    assert claimed[0].id == t1.id


@pytest.mark.asyncio
async def test_list_tasks_filter_assigned(provider: InMemoryTasksProvider) -> None:
    t1 = await provider.create_task(RUN_ID, "Agent A task")
    await provider.create_task(RUN_ID, "Unassigned")
    await provider.claim_task(RUN_ID, t1.id, "agent-a")

    assigned = await provider.list_tasks(RUN_ID, assigned_to="agent-a")
    assert len(assigned) == 1
    assert assigned[0].id == t1.id


@pytest.mark.asyncio
async def test_claim_task(provider: InMemoryTasksProvider) -> None:
    task = await provider.create_task(RUN_ID, "Claimable task")
    claimed = await provider.claim_task(RUN_ID, task.id, "worker-1")
    assert claimed is not None
    assert claimed.status == TaskStatus.CLAIMED
    assert claimed.assigned_to == "worker-1"


@pytest.mark.asyncio
async def test_claim_already_claimed(provider: InMemoryTasksProvider) -> None:
    task = await provider.create_task(RUN_ID, "Task")
    await provider.claim_task(RUN_ID, task.id, "worker-1")
    # Second claim should fail
    result = await provider.claim_task(RUN_ID, task.id, "worker-2")
    assert result is None


@pytest.mark.asyncio
async def test_claim_nonexistent(provider: InMemoryTasksProvider) -> None:
    result = await provider.claim_task(RUN_ID, "nonexistent", "worker-1")
    assert result is None


@pytest.mark.asyncio
async def test_claim_with_unmet_dependencies(provider: InMemoryTasksProvider) -> None:
    t1 = await provider.create_task(RUN_ID, "Prerequisite")
    t2 = await provider.create_task(RUN_ID, "Dependent", depends_on=[t1.id])

    # Cannot claim t2 because t1 is not done
    result = await provider.claim_task(RUN_ID, t2.id, "worker-1")
    assert result is None

    # Complete t1
    await provider.claim_task(RUN_ID, t1.id, "worker-1")
    await provider.update_task(RUN_ID, t1.id, status=TaskStatus.DONE)

    # Now t2 should be claimable
    result = await provider.claim_task(RUN_ID, t2.id, "worker-2")
    assert result is not None
    assert result.status == TaskStatus.CLAIMED


@pytest.mark.asyncio
async def test_claim_atomic(provider: InMemoryTasksProvider) -> None:
    """Test that concurrent claims result in exactly one winner."""
    task = await provider.create_task(RUN_ID, "Contested task")

    results = await asyncio.gather(
        provider.claim_task(RUN_ID, task.id, "worker-1"),
        provider.claim_task(RUN_ID, task.id, "worker-2"),
        provider.claim_task(RUN_ID, task.id, "worker-3"),
    )
    winners = [r for r in results if r is not None]
    assert len(winners) == 1


@pytest.mark.asyncio
async def test_update_task(provider: InMemoryTasksProvider) -> None:
    task = await provider.create_task(RUN_ID, "Task")
    updated = await provider.update_task(RUN_ID, task.id, status=TaskStatus.DONE, result="Completed!")
    assert updated is not None
    assert updated.status == TaskStatus.DONE
    assert updated.result == "Completed!"


@pytest.mark.asyncio
async def test_update_nonexistent(provider: InMemoryTasksProvider) -> None:
    result = await provider.update_task(RUN_ID, "nonexistent", status=TaskStatus.DONE)
    assert result is None


@pytest.mark.asyncio
async def test_add_note(provider: InMemoryTasksProvider) -> None:
    task = await provider.create_task(RUN_ID, "Task with notes")
    note = TaskNote(agent="worker-1", content="Found something useful")
    updated = await provider.add_note(RUN_ID, task.id, note)
    assert updated is not None
    assert len(updated.notes) == 1
    assert updated.notes[0].agent == "worker-1"
    assert updated.notes[0].content == "Found something useful"


@pytest.mark.asyncio
async def test_add_note_multiple(provider: InMemoryTasksProvider) -> None:
    task = await provider.create_task(RUN_ID, "Task")
    await provider.add_note(RUN_ID, task.id, TaskNote(agent="a", content="Note 1"))
    updated = await provider.add_note(RUN_ID, task.id, TaskNote(agent="b", content="Note 2"))
    assert updated is not None
    assert len(updated.notes) == 2


@pytest.mark.asyncio
async def test_get_available(provider: InMemoryTasksProvider) -> None:
    t1 = await provider.create_task(RUN_ID, "Ready", priority=1)
    t2 = await provider.create_task(RUN_ID, "Blocked", depends_on=[t1.id])
    await provider.create_task(RUN_ID, "Also ready", priority=2)

    available = await provider.get_available(RUN_ID)
    assert len(available) == 2
    # t2 should not be available (depends on t1)
    available_ids = {t.id for t in available}
    assert t2.id not in available_ids


@pytest.mark.asyncio
async def test_get_available_after_dependency_met(provider: InMemoryTasksProvider) -> None:
    t1 = await provider.create_task(RUN_ID, "Prerequisite")
    t2 = await provider.create_task(RUN_ID, "Dependent", depends_on=[t1.id])

    available = await provider.get_available(RUN_ID)
    assert len(available) == 1
    assert available[0].id == t1.id

    # Complete t1
    await provider.claim_task(RUN_ID, t1.id, "worker")
    await provider.update_task(RUN_ID, t1.id, status=TaskStatus.DONE)

    # Now t2 should be available
    available = await provider.get_available(RUN_ID)
    assert len(available) == 1
    assert available[0].id == t2.id


@pytest.mark.asyncio
async def test_separate_boards(provider: InMemoryTasksProvider) -> None:
    """Tasks from different team runs are isolated."""
    await provider.create_task("run-1", "Task in run 1")
    await provider.create_task("run-2", "Task in run 2")

    tasks1 = await provider.list_tasks("run-1")
    tasks2 = await provider.list_tasks("run-2")
    assert len(tasks1) == 1
    assert len(tasks2) == 1
    assert tasks1[0].title == "Task in run 1"
    assert tasks2[0].title == "Task in run 2"
