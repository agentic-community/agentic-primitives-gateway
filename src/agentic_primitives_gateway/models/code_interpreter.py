from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agentic_primitives_gateway.models.enums import CodeLanguage


class StartSessionRequest(BaseModel):
    session_id: str | None = None
    language: str = CodeLanguage.PYTHON
    config: dict[str, Any] = Field(default_factory=dict)


class SessionInfo(BaseModel):
    session_id: str
    status: str
    language: str = CodeLanguage.PYTHON
    created_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecuteRequest(BaseModel):
    code: str
    language: str = CodeLanguage.PYTHON


class ExecutionResult(BaseModel):
    session_id: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    result: Any = None
    error: str | None = None


class FileUploadResponse(BaseModel):
    filename: str
    size: int
    session_id: str


class ListSessionsResponse(BaseModel):
    sessions: list[SessionInfo]
