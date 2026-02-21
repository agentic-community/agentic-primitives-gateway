from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CompletionRequest(BaseModel):
    model: str
    messages: list[dict[str, Any]]
    temperature: float = 1.0
    max_tokens: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompletionResponse(BaseModel):
    model: str
    content: str
    usage: dict[str, int] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelInfo(BaseModel):
    name: str
    provider: str
    capabilities: list[str] = Field(default_factory=list)


class ListModelsResponse(BaseModel):
    models: list[ModelInfo]
