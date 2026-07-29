"""Tasks audit wrappers used by ``TasksProvider.__init_subclass__``.

Every subclass gets ``create_task`` / ``claim_task`` / ``update_task`` /
``add_note`` wrapped automatically to emit the matching ``task.*``
action (per ``AuditAction``) on success and failure.  List/get stay on
the generic ``provider.call`` event.
"""

from __future__ import annotations

import functools
from typing import Any

from agentic_primitives_gateway.audit.emit import emit_audit_event
from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType


def _emit(action: str, outcome: AuditOutcome, *, resource_id: str | None, metadata: dict[str, Any]) -> None:
    # See tools/_audit.py::_emit for the ``layer`` rationale.
    metadata.setdefault("layer", "primitive")
    emit_audit_event(
        action=action,
        outcome=outcome,
        resource_type=ResourceType.TASK,
        resource_id=resource_id,
        metadata=metadata,
    )


def wrap_create_task(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(
        self: Any,
        team_run_id: str,
        title: str,
        *,
        description: str = "",
        created_by: str = "",
        depends_on: list[str] | None = None,
        priority: int = 0,
        suggested_worker: str | None = None,
    ) -> Any:
        try:
            result = await func(
                self,
                team_run_id,
                title,
                description=description,
                created_by=created_by,
                depends_on=depends_on,
                priority=priority,
                suggested_worker=suggested_worker,
            )
        except Exception as exc:
            _emit(
                AuditAction.TASK_CREATE,
                AuditOutcome.ERROR,
                resource_id=team_run_id,
                metadata={
                    "team_run_id": team_run_id,
                    "title": title,
                    "created_by": created_by,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        task_id = getattr(result, "id", None)
        _emit(
            AuditAction.TASK_CREATE,
            AuditOutcome.SUCCESS,
            resource_id=task_id,
            metadata={
                "team_run_id": team_run_id,
                "task_id": task_id,
                "title": title,
                "created_by": created_by,
                "suggested_worker": suggested_worker,
            },
        )
        return result

    return wrapper


def wrap_claim_task(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(self: Any, team_run_id: str, task_id: str, agent_name: str) -> Any:
        try:
            result = await func(self, team_run_id, task_id, agent_name)
        except Exception as exc:
            _emit(
                AuditAction.TASK_CLAIM,
                AuditOutcome.ERROR,
                resource_id=task_id,
                metadata={
                    "team_run_id": team_run_id,
                    "task_id": task_id,
                    "agent_name": agent_name,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        # ``claim_task`` returns None when already claimed / missing /
        # unmet deps.  That's not a failure — the caller competed and
        # lost — but surface it so dashboards can see claim contention.
        claimed = result is not None
        _emit(
            AuditAction.TASK_CLAIM,
            AuditOutcome.SUCCESS if claimed else AuditOutcome.FAILURE,
            resource_id=task_id,
            metadata={
                "team_run_id": team_run_id,
                "task_id": task_id,
                "agent_name": agent_name,
                "claimed": claimed,
            },
        )
        return result

    return wrapper


def wrap_update_task(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(
        self: Any,
        team_run_id: str,
        task_id: str,
        *,
        status: str | None = None,
        result: str | None = None,
    ) -> Any:
        try:
            task = await func(self, team_run_id, task_id, status=status, result=result)
        except Exception as exc:
            _emit(
                AuditAction.TASK_UPDATE,
                AuditOutcome.ERROR,
                resource_id=task_id,
                metadata={
                    "team_run_id": team_run_id,
                    "task_id": task_id,
                    "status": status,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        _emit(
            AuditAction.TASK_UPDATE,
            AuditOutcome.SUCCESS,
            resource_id=task_id,
            metadata={
                "team_run_id": team_run_id,
                "task_id": task_id,
                "status": status,
                "found": task is not None,
            },
        )
        return task

    return wrapper


def wrap_add_note(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(self: Any, team_run_id: str, task_id: str, note: Any) -> Any:
        try:
            task = await func(self, team_run_id, task_id, note)
        except Exception as exc:
            _emit(
                AuditAction.TASK_NOTE,
                AuditOutcome.ERROR,
                resource_id=task_id,
                metadata={
                    "team_run_id": team_run_id,
                    "task_id": task_id,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        _emit(
            AuditAction.TASK_NOTE,
            AuditOutcome.SUCCESS,
            resource_id=task_id,
            metadata={
                "team_run_id": team_run_id,
                "task_id": task_id,
                "agent": getattr(note, "agent", None),
                "found": task is not None,
            },
        )
        return task

    return wrapper
