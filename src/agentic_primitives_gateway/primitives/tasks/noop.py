from __future__ import annotations

from typing import Any

from agentic_primitives_gateway.models.tasks import Task, TaskNote
from agentic_primitives_gateway.primitives.tasks.base import TasksProvider


class NoopTasksProvider(TasksProvider):
    """No-op tasks provider that does nothing. For configs that don't need tasks."""

    def __init__(self, **kwargs: Any) -> None:
        pass

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
        raise NotImplementedError("Tasks primitive not configured")

    async def get_task(self, team_run_id: str, task_id: str) -> Task | None:
        return None

    async def list_tasks(
        self,
        team_run_id: str,
        *,
        status: str | None = None,
        assigned_to: str | None = None,
    ) -> list[Task]:
        return []

    async def claim_task(
        self,
        team_run_id: str,
        task_id: str,
        agent_name: str,
    ) -> Task | None:
        return None

    async def update_task(
        self,
        team_run_id: str,
        task_id: str,
        *,
        status: str | None = None,
        result: str | None = None,
    ) -> Task | None:
        return None

    async def add_note(
        self,
        team_run_id: str,
        task_id: str,
        note: TaskNote,
    ) -> Task | None:
        return None
