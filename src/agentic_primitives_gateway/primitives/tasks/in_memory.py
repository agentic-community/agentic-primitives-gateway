from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from agentic_primitives_gateway.models.tasks import Task, TaskNote, TaskStatus
from agentic_primitives_gateway.primitives.tasks.base import TasksProvider


class InMemoryTasksProvider(TasksProvider):
    """In-memory task board with asyncio.Lock for atomic claims.

    Suitable for single-process development. Not for multi-replica production.
    """

    def __init__(self, **kwargs: Any) -> None:
        # team_run_id -> task_id -> Task
        self._boards: dict[str, dict[str, Task]] = {}
        self._lock = asyncio.Lock()

    def _board(self, team_run_id: str) -> dict[str, Task]:
        return self._boards.setdefault(team_run_id, {})

    async def create_task(
        self,
        team_run_id: str,
        title: str,
        *,
        description: str = "",
        created_by: str = "",
        depends_on: list[str] | None = None,
        priority: int = 0,
        suggested_worker: str | None = None,
    ) -> Task:
        now = datetime.now(UTC)
        task = Task(
            id=uuid.uuid4().hex[:12],
            team_run_id=team_run_id,
            title=title,
            description=description,
            created_by=created_by,
            depends_on=depends_on or [],
            priority=priority,
            suggested_worker=suggested_worker,
            created_at=now,
            updated_at=now,
        )
        self._board(team_run_id)[task.id] = task
        return task

    async def get_task(self, team_run_id: str, task_id: str) -> Task | None:
        return self._board(team_run_id).get(task_id)

    async def list_tasks(
        self,
        team_run_id: str,
        *,
        status: str | None = None,
        assigned_to: str | None = None,
    ) -> list[Task]:
        tasks = list(self._board(team_run_id).values())
        if status is not None:
            tasks = [t for t in tasks if t.status == status]
        if assigned_to is not None:
            tasks = [t for t in tasks if t.assigned_to == assigned_to]
        return sorted(tasks, key=lambda t: (-t.priority, t.created_at))

    async def claim_task(
        self,
        team_run_id: str,
        task_id: str,
        agent_name: str,
    ) -> Task | None:
        async with self._lock:
            task = self._board(team_run_id).get(task_id)
            if task is None:
                return None
            if task.status != TaskStatus.PENDING:
                return None
            # Check dependencies are all done
            board = self._board(team_run_id)
            for dep_id in task.depends_on:
                dep = board.get(dep_id)
                if dep is None or dep.status != TaskStatus.DONE:
                    return None
            # Atomic claim
            updated = task.model_copy(
                update={
                    "status": TaskStatus.CLAIMED,
                    "assigned_to": agent_name,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._board(team_run_id)[task_id] = updated
            return updated

    async def update_task(
        self,
        team_run_id: str,
        task_id: str,
        *,
        status: str | None = None,
        result: str | None = None,
    ) -> Task | None:
        task = self._board(team_run_id).get(task_id)
        if task is None:
            return None
        updates: dict[str, Any] = {"updated_at": datetime.now(UTC)}
        if status is not None:
            updates["status"] = status
        if result is not None:
            updates["result"] = result
        updated = task.model_copy(update=updates)
        self._board(team_run_id)[task_id] = updated
        return updated

    async def add_note(
        self,
        team_run_id: str,
        task_id: str,
        note: TaskNote,
    ) -> Task | None:
        task = self._board(team_run_id).get(task_id)
        if task is None:
            return None
        updated = task.model_copy(
            update={
                "notes": [*task.notes, note],
                "updated_at": datetime.now(UTC),
            }
        )
        self._board(team_run_id)[task_id] = updated
        return updated
