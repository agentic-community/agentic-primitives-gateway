from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"
    FAILED = "failed"


class TaskNote(BaseModel):
    """A note left on a task by an agent."""

    agent: str
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Task(BaseModel):
    """A unit of work on a team task board."""

    id: str
    team_run_id: str
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    assigned_to: str | None = None
    suggested_worker: str | None = None
    created_by: str = ""
    depends_on: list[str] = Field(default_factory=list)
    result: str | None = None
    notes: list[TaskNote] = Field(default_factory=list)
    priority: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
