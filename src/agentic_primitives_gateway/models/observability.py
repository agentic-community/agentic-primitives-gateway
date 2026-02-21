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
