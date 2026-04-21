from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PrimitiveConfig(BaseModel):
    """Per-primitive configuration within an agent spec."""

    enabled: bool = True
    tools: list[str] | None = None
    namespace: str | None = None
    shared_namespaces: list[str] | None = None


class HooksConfig(BaseModel):
    """Auto-hook configuration for an agent."""

    auto_memory: bool = True
    auto_trace: bool = True


class AgentSpec(BaseModel):
    """Declarative agent specification."""

    name: str
    description: str = ""
    model: str
    system_prompt: str = "You are a helpful assistant."
    primitives: dict[str, PrimitiveConfig] = Field(default_factory=dict)
    hooks: HooksConfig = Field(default_factory=HooksConfig)
    provider_overrides: dict[str, str] = Field(default_factory=dict)
    max_turns: int = 20
    temperature: float = 1.0
    max_tokens: int | None = None
    owner_id: str = "system"
    shared_with: list[str] = Field(default_factory=list)
    checkpointing_enabled: bool = False


class CreateAgentRequest(BaseModel):
    """Request body for POST /api/v1/agents."""

    name: str
    description: str = ""
    model: str
    system_prompt: str = "You are a helpful assistant."
    primitives: dict[str, PrimitiveConfig] = Field(default_factory=dict)
    hooks: HooksConfig = Field(default_factory=HooksConfig)
    provider_overrides: dict[str, str] = Field(default_factory=dict)
    max_turns: int = 20
    temperature: float = 1.0
    max_tokens: int | None = None
    shared_with: list[str] = Field(default_factory=list)


class UpdateAgentRequest(BaseModel):
    """Request body for PUT /api/v1/agents/{name}. All fields optional."""

    description: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    primitives: dict[str, PrimitiveConfig] | None = None
    hooks: HooksConfig | None = None
    provider_overrides: dict[str, str] | None = None
    max_turns: int | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    shared_with: list[str] | None = None
    checkpointing_enabled: bool | None = None


class ChatRequest(BaseModel):
    """Request body for POST /api/v1/agents/{name}/chat."""

    message: str
    session_id: str | None = None


class ToolArtifact(BaseModel):
    """A tool call input/output pair captured during agent execution."""

    tool_name: str
    tool_input: dict[str, Any] = Field(default_factory=dict)
    output: str = ""


class ChatResponse(BaseModel):
    """Response for POST /api/v1/agents/{name}/chat."""

    response: str
    session_id: str
    agent_name: str
    turns_used: int
    tools_called: list[str] = Field(default_factory=list)
    artifacts: list[ToolArtifact] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentListResponse(BaseModel):
    """Response for GET /api/v1/agents."""

    agents: list[AgentSpec]


class AgentToolInfo(BaseModel):
    """Info about a single tool available to an agent."""

    name: str
    description: str
    primitive: str
    provider: str


class AgentToolsResponse(BaseModel):
    """Response for GET /api/v1/agents/{name}/tools."""

    agent_name: str
    tools: list[AgentToolInfo] = Field(default_factory=list)


class MemoryStoreInfo(BaseModel):
    """Info about a single memory namespace/store."""

    namespace: str
    memory_count: int = 0
    memories: list[dict[str, Any]] = Field(default_factory=list)


class AgentMemoryResponse(BaseModel):
    """Response for GET /api/v1/agents/{name}/memory."""

    agent_name: str
    memory_enabled: bool
    namespace: str
    stores: list[MemoryStoreInfo] = Field(default_factory=list)


class SessionHistoryResponse(BaseModel):
    """Response for GET /api/v1/agents/{name}/sessions/{session_id}."""

    agent_name: str
    session_id: str
    messages: list[dict[str, str]] = Field(default_factory=list)


# ── Versioning ────────────────────────────────────────────────────────────


class VersionStatus(StrEnum):
    """Lifecycle state of a version.

    ``deployed`` is the single active state per identity.  ``draft``/``proposed``
    are inert and never served to runners.  ``archived`` and ``rejected`` are
    terminal.
    """

    DRAFT = "draft"
    PROPOSED = "proposed"
    DEPLOYED = "deployed"
    ARCHIVED = "archived"
    REJECTED = "rejected"


class ForkRef(BaseModel):
    """Pointer to the source of a fork. Lives in a (possibly different) owner's namespace."""

    model_config = ConfigDict(frozen=True)

    name: str
    owner_id: str
    version_id: str


class Identity(BaseModel):
    """Composite primary key for an agent or team identity."""

    model_config = ConfigDict(frozen=True)

    owner_id: str
    name: str

    @property
    def qualified(self) -> str:
        return f"{self.owner_id}:{self.name}"


class AgentVersion(BaseModel):
    """Immutable record of one edit to an agent identity.

    The embedded ``spec`` is the full AgentSpec at this point in history.
    ``version_id`` is globally unique.  ``version_number`` is a monotonic
    counter scoped to the ``(owner_id, name)`` identity.
    """

    model_config = ConfigDict(frozen=True)

    version_id: str
    agent_name: str
    owner_id: str
    version_number: int
    spec: AgentSpec
    created_at: datetime
    created_by: str
    parent_version_id: str | None = None
    forked_from: ForkRef | None = None
    status: VersionStatus
    approved_by: str | None = None
    approved_at: datetime | None = None
    deployed_at: datetime | None = None
    commit_message: str | None = None


class CreateVersionRequest(BaseModel):
    """Request body for POST /api/v1/agents/{name}/versions."""

    description: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    primitives: dict[str, PrimitiveConfig] | None = None
    hooks: HooksConfig | None = None
    provider_overrides: dict[str, str] | None = None
    max_turns: int | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    shared_with: list[str] | None = None
    checkpointing_enabled: bool | None = None
    commit_message: str | None = None
    parent_version_id: str | None = None


class ForkRequest(BaseModel):
    """Request body for POST /api/v1/agents/{name}/fork."""

    target_name: str | None = None
    commit_message: str | None = None


class RejectionRequest(BaseModel):
    """Request body for POST /api/v1/agents/{name}/versions/{id}/reject."""

    reason: str


class AgentVersionListResponse(BaseModel):
    """Response for GET /api/v1/agents/{name}/versions."""

    versions: list[AgentVersion] = Field(default_factory=list)


class AgentBucketedListResponse(BaseModel):
    """Default response shape for GET /api/v1/agents."""

    mine: list[AgentSpec] = Field(default_factory=list)
    system: list[AgentSpec] = Field(default_factory=list)
    shared_with_me: list[AgentSpec] = Field(default_factory=list)


class LineageNodeAgent(BaseModel):
    """One node in the agent lineage DAG."""

    version: AgentVersion
    children_ids: list[str] = Field(default_factory=list)
    forks_out: list[ForkRef] = Field(default_factory=list)


class AgentLineage(BaseModel):
    """Full lineage graph for an agent identity, transitively including all forks."""

    root_identity: Identity
    nodes: list[LineageNodeAgent] = Field(default_factory=list)
    deployed: dict[str, str] = Field(default_factory=dict)
