from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agentic_primitives_gateway.models.agents import (
    ForkRef,
    Identity,
    VersionStatus,
)


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
    shared_with: list[str] = Field(default_factory=list)
    checkpointing_enabled: bool = False


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
    checkpointing_enabled: bool | None = None


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


# ── Versioning ────────────────────────────────────────────────────────────


class TeamVersion(BaseModel):
    """Immutable record of one edit to a team identity.

    Mirrors ``AgentVersion`` but embeds a ``TeamSpec``.  Used by the versioned
    team store.
    """

    model_config = ConfigDict(frozen=True)

    version_id: str
    team_name: str
    owner_id: str
    version_number: int
    spec: TeamSpec
    created_at: datetime
    created_by: str
    parent_version_id: str | None = None
    forked_from: ForkRef | None = None
    status: VersionStatus
    approved_by: str | None = None
    approved_at: datetime | None = None
    deployed_at: datetime | None = None
    commit_message: str | None = None


class CreateTeamVersionRequest(BaseModel):
    """Request body for POST /api/v1/teams/{name}/versions."""

    description: str | None = None
    planner: str | None = None
    synthesizer: str | None = None
    workers: list[str] | None = None
    max_concurrent: int | None = None
    global_max_turns: int | None = None
    global_timeout_seconds: int | None = None
    shared_memory_namespace: str | None = None
    shared_with: list[str] | None = None
    checkpointing_enabled: bool | None = None
    commit_message: str | None = None
    parent_version_id: str | None = None


class TeamVersionListResponse(BaseModel):
    """Response for GET /api/v1/teams/{name}/versions."""

    versions: list[TeamVersion] = Field(default_factory=list)


class TeamBucketedListResponse(BaseModel):
    """Default response shape for GET /api/v1/teams."""

    mine: list[TeamSpec] = Field(default_factory=list)
    system: list[TeamSpec] = Field(default_factory=list)
    shared_with_me: list[TeamSpec] = Field(default_factory=list)


class LineageNodeTeam(BaseModel):
    """One node in the team lineage DAG."""

    version: TeamVersion
    children_ids: list[str] = Field(default_factory=list)
    forks_out: list[ForkRef] = Field(default_factory=list)


class TeamLineage(BaseModel):
    """Full lineage graph for a team identity, transitively including all forks."""

    root_identity: Identity
    nodes: list[LineageNodeTeam] = Field(default_factory=list)
    deployed: dict[str, str] = Field(default_factory=dict)
