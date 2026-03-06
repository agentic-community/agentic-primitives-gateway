from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PrimitiveConfig(BaseModel):
    """Per-primitive configuration within an agent spec."""

    enabled: bool = True
    tools: list[str] | None = None
    namespace: str | None = None


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


class ChatRequest(BaseModel):
    """Request body for POST /api/v1/agents/{name}/chat."""

    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    """Response for POST /api/v1/agents/{name}/chat."""

    response: str
    session_id: str
    agent_name: str
    turns_used: int
    tools_called: list[str] = Field(default_factory=list)
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
