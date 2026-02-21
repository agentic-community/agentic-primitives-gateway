from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class StartBrowserSessionRequest(BaseModel):
    session_id: str | None = None
    viewport: dict[str, int] | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class BrowserSessionInfo(BaseModel):
    session_id: str
    status: str
    viewport: dict[str, int] | None = None
    created_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class LiveViewResponse(BaseModel):
    url: str
    expires_in: int


class ListBrowserSessionsResponse(BaseModel):
    sessions: list[BrowserSessionInfo]


class NavigateRequest(BaseModel):
    url: str


class ClickRequest(BaseModel):
    selector: str


class TypeRequest(BaseModel):
    selector: str
    text: str


class EvaluateRequest(BaseModel):
    expression: str
