from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class TeamSpec(BaseModel):
    """Declarative team specification."""

    name: str
    description: str = ""
    planner: str
    synthesizer: str
    workers: list[str]
    max_concurrent: int | None = None
    global_max_turns: int = 100
    global_timeout_seconds: int = 300
    shared_memory_namespace: str | None = None
    owner_id: str = "system"
    shared_with: list[str] = Field(default_factory=lambda: ["*"])


class CreateTeamRequest(BaseModel):
    """Request body for POST /api/v1/teams."""

    name: str
    description: str = ""
    planner: str
    synthesizer: str
    workers: list[str]
    max_concurrent: int | None = None
    global_max_turns: int = 100
    global_timeout_seconds: int = 300
    shared_memory_namespace: str | None = None
    shared_with: list[str] = Field(default_factory=list)


class UpdateTeamRequest(BaseModel):
    """Request body for PUT /api/v1/teams/{name}. All fields optional."""

    description: str | None = None
    planner: str | None = None
    synthesizer: str | None = None
    workers: list[str] | None = None
    max_concurrent: int | None = None
    global_max_turns: int | None = None
    global_timeout_seconds: int | None = None
    shared_memory_namespace: str | None = None
    shared_with: list[str] | None = None


class TeamListResponse(BaseModel):
    """Response for GET /api/v1/teams."""

    teams: list[TeamSpec]


class TeamRunPhase(StrEnum):
    PLANNING = "planning"
    EXECUTION = "execution"
    SYNTHESIS = "synthesis"
    DONE = "done"
    FAILED = "failed"


class TeamRunRequest(BaseModel):
    """Request body for POST /api/v1/teams/{name}/run."""

    message: str
    session_id: str | None = None


class TeamRunResponse(BaseModel):
    """Response for POST /api/v1/teams/{name}/run."""

    response: str
    team_run_id: str
    team_name: str
    phase: TeamRunPhase
    tasks_created: int
    tasks_completed: int
    workers_used: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
