from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class StoreMemoryRequest(BaseModel):
    key: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryRecord(BaseModel):
    namespace: str
    key: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SearchMemoryRequest(BaseModel):
    query: str
    top_k: int = 10
    filters: dict[str, Any] = Field(default_factory=dict)


class SearchResult(BaseModel):
    record: MemoryRecord
    score: float


class SearchMemoryResponse(BaseModel):
    results: list[SearchResult]


class ListMemoryResponse(BaseModel):
    records: list[MemoryRecord]
    total: int


# ── Conversation memory models ────────────────────────────────────────


class EventMessage(BaseModel):
    text: str
    role: str


class CreateEventRequest(BaseModel):
    messages: list[EventMessage]
    metadata: dict[str, Any] = Field(default_factory=dict)


class EventInfo(BaseModel):
    event_id: str
    actor_id: str
    session_id: str
    messages: list[EventMessage]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class ListEventsResponse(BaseModel):
    events: list[EventInfo]


class TurnGroup(BaseModel):
    messages: list[EventMessage]


class GetTurnsResponse(BaseModel):
    turns: list[TurnGroup]


# ── Session management models ─────────────────────────────────────────


class ActorInfo(BaseModel):
    actor_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionInfo(BaseModel):
    session_id: str
    actor_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── Branch management models ──────────────────────────────────────────


class BranchInfo(BaseModel):
    name: str
    root_event_id: str
    event_count: int = 0


class ForkConversationRequest(BaseModel):
    root_event_id: str
    branch_name: str
    messages: list[EventMessage]


# ── Control plane models ──────────────────────────────────────────────


class MemoryResourceInfo(BaseModel):
    memory_id: str
    name: str
    status: str = "ACTIVE"
    strategies: list[dict[str, Any]] = Field(default_factory=list)
    description: str = ""


class CreateMemoryResourceRequest(BaseModel):
    name: str
    strategies: list[dict[str, Any]] = Field(default_factory=list)
    description: str = ""


class StrategyInfo(BaseModel):
    strategy_id: str
    type: str
    name: str = ""
    namespaces: list[str] = Field(default_factory=list)


class AddStrategyRequest(BaseModel):
    strategy: dict[str, Any]
