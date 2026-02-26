from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agentic_primitives_gateway.models.enums import LogLevel


class SpanData(BaseModel):
    name: str
    input: Any = None
    output: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    level: str | None = None
    model: str | None = None
    duration_ms: float | None = None


class IngestTraceRequest(BaseModel):
    trace_id: str
    name: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    input: Any = None
    output: Any = None
    tags: list[str] = Field(default_factory=list)
    spans: list[SpanData] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestLogRequest(BaseModel):
    level: str = LogLevel.INFO
    message: str
    trace_id: str | None = None
    timestamp: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Trace(BaseModel):
    trace_id: str
    name: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    input: Any = None
    output: Any = None
    tags: list[str] = Field(default_factory=list)
    spans: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    latency: float | None = None
    total_cost: float | None = None


class QueryTracesResponse(BaseModel):
    traces: list[Trace]


# ── LLM generation tracking ───────────────────────────────────────────


class UsageInfo(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class LogGenerationRequest(BaseModel):
    name: str
    model: str
    input: Any = None
    output: Any = None
    usage: UsageInfo | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    level: str | None = None


class GenerationInfo(BaseModel):
    generation_id: str | None = None
    trace_id: str
    name: str
    model: str
    input: Any = None
    output: Any = None
    usage: UsageInfo | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    level: str | None = None


# ── Trace updates & scoring ───────────────────────────────────────────


class UpdateTraceRequest(BaseModel):
    name: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    input: Any = None
    output: Any = None
    metadata: dict[str, Any] | None = None
    tags: list[str] | None = None


class ScoreRequest(BaseModel):
    name: str
    value: float
    comment: str | None = None
    data_type: str | None = None


class ScoreInfo(BaseModel):
    score_id: str | None = None
    trace_id: str
    name: str
    value: float
    comment: str | None = None
    data_type: str | None = None


class ListScoresResponse(BaseModel):
    scores: list[ScoreInfo]


# ── Session management ────────────────────────────────────────────────


class ObservabilitySessionInfo(BaseModel):
    session_id: str
    user_id: str | None = None
    trace_count: int = 0
    created_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ListSessionsResponse(BaseModel):
    sessions: list[ObservabilitySessionInfo]


# ── Flush ─────────────────────────────────────────────────────────────


class FlushResponse(BaseModel):
    status: str
