from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class IngestDocument(BaseModel):
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    document_id: str | None = None
    source: str | None = None


class IngestRequest(BaseModel):
    documents: list[IngestDocument]


class IngestResult(BaseModel):
    document_ids: list[str]
    ingested: int


class RetrievedChunk(BaseModel):
    chunk_id: str
    document_id: str
    text: str
    score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrieveRequest(BaseModel):
    query: str
    top_k: int = 10
    filters: dict[str, Any] = Field(default_factory=dict)


class RetrieveResponse(BaseModel):
    chunks: list[RetrievedChunk]


class QueryRequest(BaseModel):
    question: str
    top_k: int = 5
    filters: dict[str, Any] = Field(default_factory=dict)


class QueryResponse(BaseModel):
    answer: str
    chunks: list[RetrievedChunk] = Field(default_factory=list)


class DocumentInfo(BaseModel):
    document_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None


class ListDocumentsResponse(BaseModel):
    documents: list[DocumentInfo]
    total: int
