"""Pydantic models for the A2A (Agent-to-Agent) protocol.

Ref: https://a2a-protocol.org
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class A2APart(BaseModel):
    """A2A Part - text, file, or data."""

    text: str | None = None
    data: dict[str, Any] | None = None
    media_type: str | None = None


class A2AMessage(BaseModel):
    """A2A Message exchanged between agents."""

    message_id: str
    role: Literal["user", "agent"]
    parts: list[A2APart]
    context_id: str | None = None
    task_id: str | None = None
    metadata: dict[str, Any] | None = None
    reference_task_ids: list[str] | None = None


class A2ATaskState(StrEnum):
    """Task lifecycle states per the A2A protocol."""

    SUBMITTED = "submitted"
    WORKING = "working"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    INPUT_REQUIRED = "input_required"
    REJECTED = "rejected"
    AUTH_REQUIRED = "auth_required"


class A2ATaskStatus(BaseModel):
    """Current status of an A2A task."""

    state: A2ATaskState
    message: A2AMessage | None = None
    timestamp: str | None = None


class A2AArtifact(BaseModel):
    """An artifact produced during task execution."""

    artifact_id: str
    name: str | None = None
    description: str | None = None
    parts: list[A2APart]
    metadata: dict[str, Any] | None = None


class A2ATask(BaseModel):
    """A2A Task representing a unit of work."""

    id: str
    context_id: str | None = None
    status: A2ATaskStatus
    artifacts: list[A2AArtifact] | None = None
    history: list[A2AMessage] | None = None
    metadata: dict[str, Any] | None = None


class A2ASendMessageRequest(BaseModel):
    """Request body for message:send and message:stream."""

    message: A2AMessage
    configuration: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class A2ATaskStatusUpdateEvent(BaseModel):
    """SSE event for task status changes."""

    task_id: str
    context_id: str
    status: A2ATaskStatus
    metadata: dict[str, Any] | None = None


class A2ATaskArtifactUpdateEvent(BaseModel):
    """SSE event for artifact updates."""

    task_id: str
    context_id: str
    artifact: A2AArtifact
    append: bool = False
    last_chunk: bool = False
    metadata: dict[str, Any] | None = None


class A2AAgentSkill(BaseModel):
    """A capability/skill exposed by an A2A agent."""

    id: str
    name: str
    description: str
    tags: list[str] = Field(default_factory=list)
    examples: list[str] | None = None
    input_modes: list[str] | None = None
    output_modes: list[str] | None = None


class A2AAgentCapabilities(BaseModel):
    """Capabilities advertised in the agent card."""

    streaming: bool | None = None
    push_notifications: bool | None = None
    extended_agent_card: bool | None = None


class A2AAgentInterface(BaseModel):
    """Protocol interface endpoint."""

    url: str
    protocol_binding: str
    protocol_version: str
    tenant: str | None = None


class A2ASecurityScheme(BaseModel):
    """Simplified security scheme for the agent card."""

    type: str  # "apiKey", "http", "openIdConnect"
    description: str | None = None
    # API Key fields
    name: str | None = None
    location: str | None = None  # "header", "query", "cookie"
    # HTTP auth fields
    scheme: str | None = None  # "bearer", "basic"
    bearer_format: str | None = None
    # OIDC fields
    open_id_connect_url: str | None = None


class A2AAgentCard(BaseModel):
    """Agent card served at /.well-known/agent.json for A2A discovery."""

    name: str
    description: str
    version: str
    supported_interfaces: list[A2AAgentInterface]
    capabilities: A2AAgentCapabilities
    skills: list[A2AAgentSkill]
    default_input_modes: list[str] = Field(default_factory=lambda: ["text/plain"])
    default_output_modes: list[str] = Field(default_factory=lambda: ["text/plain"])
    security_schemes: dict[str, A2ASecurityScheme] | None = None
    security_requirements: list[dict[str, list[str]]] | None = None
    provider: dict[str, str] | None = None
    icon_url: str | None = None
