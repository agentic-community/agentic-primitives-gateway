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
