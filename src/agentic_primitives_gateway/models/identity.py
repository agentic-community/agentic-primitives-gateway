from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agentic_primitives_gateway.models.enums import TokenType


class TokenRequest(BaseModel):
    provider_name: str
    scopes: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = TokenType.BEARER
    expires_at: datetime | None = None
    scopes: list[str] = Field(default_factory=list)


class ApiKeyRequest(BaseModel):
    provider_name: str
    context: dict[str, Any] = Field(default_factory=dict)


class ApiKeyResponse(BaseModel):
    api_key: str
    provider_name: str
    expires_at: datetime | None = None


class IdentityProviderInfo(BaseModel):
    name: str
    type: str
    scopes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ListProvidersResponse(BaseModel):
    providers: list[IdentityProviderInfo]
