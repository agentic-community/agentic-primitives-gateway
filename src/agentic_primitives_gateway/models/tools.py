from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RegisterToolRequest(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolInfo(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ListToolsResponse(BaseModel):
    tools: list[ToolInfo]


class InvokeToolRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    tool_name: str
    result: Any = None
    error: str | None = None
