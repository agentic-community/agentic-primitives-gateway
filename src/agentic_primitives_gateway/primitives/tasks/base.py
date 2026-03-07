from __future__ import annotations

from abc import ABC, abstractmethod

from agentic_primitives_gateway.models.tasks import Task, TaskNote


class TasksProvider(ABC):
    """Abstract base class for task board providers.

    Task providers manage structured task boards for agent team coordination.
    They support atomic claiming to prevent race conditions when multiple
    agents compete for work.
    """

    @abstractmethod
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
    ) -> Task: ...

    @abstractmethod
    async def get_task(self, team_run_id: str, task_id: str) -> Task | None: ...

    @abstractmethod
    async def list_tasks(
        self,
        team_run_id: str,
        *,
        status: str | None = None,
        assigned_to: str | None = None,
    ) -> list[Task]: ...

    @abstractmethod
    async def claim_task(
        self,
        team_run_id: str,
        task_id: str,
        agent_name: str,
    ) -> Task | None:
        """Atomically claim a task. Returns the updated task, or None if
        the task was already claimed, doesn't exist, or has unmet dependencies."""
        ...

    @abstractmethod
    async def update_task(
        self,
        team_run_id: str,
        task_id: str,
        *,
        status: str | None = None,
        result: str | None = None,
    ) -> Task | None: ...

    @abstractmethod
    async def add_note(
        self,
        team_run_id: str,
        task_id: str,
        note: TaskNote,
    ) -> Task | None: ...

    async def get_available(
        self,
        team_run_id: str,
        worker_name: str | None = None,
    ) -> list[Task]:
        """Return tasks that are pending and have all dependencies met.

        If ``worker_name`` is given, returns tasks suggested for that worker
        first, followed by unassigned tasks.  Tasks suggested for *other*
        workers are excluded.
        """
        all_tasks = await self.list_tasks(team_run_id)
        done_ids = {t.id for t in all_tasks if t.status == "done"}
        ready = [t for t in all_tasks if t.status == "pending" and all(dep in done_ids for dep in t.depends_on)]
        if worker_name is None:
            return ready
        # Include tasks suggested for this worker + unassigned tasks; exclude tasks for other workers
        mine = [t for t in ready if t.suggested_worker == worker_name]
        unassigned = [t for t in ready if t.suggested_worker is None]
        return mine + unassigned

    async def healthcheck(self) -> bool:
        return True
