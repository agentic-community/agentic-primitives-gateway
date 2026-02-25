from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agentic_primitives_gateway.models.enums import AuthFlow, TokenType

# ── Token operations ─────────────────────────────────────────────


class TokenRequest(BaseModel):
    credential_provider: str
    workload_token: str
    auth_flow: str = AuthFlow.M2M
    scopes: list[str] = Field(default_factory=list)
    callback_url: str | None = None
    force_auth: bool = False
    session_uri: str | None = None
    custom_state: str | None = None
    custom_parameters: dict[str, str] | None = None


class TokenResponse(BaseModel):
    access_token: str | None = None
    token_type: str = TokenType.BEARER
    expires_at: datetime | None = None
    scopes: list[str] = Field(default_factory=list)
    authorization_url: str | None = None
    session_uri: str | None = None


class ApiKeyRequest(BaseModel):
    credential_provider: str
    workload_token: str


class ApiKeyResponse(BaseModel):
    api_key: str
    credential_provider: str
    expires_at: datetime | None = None


class WorkloadTokenRequest(BaseModel):
    workload_name: str
    user_token: str | None = None
    user_id: str | None = None


class WorkloadTokenResponse(BaseModel):
    workload_token: str
    workload_name: str


class CompleteAuthRequest(BaseModel):
    session_uri: str
    user_token: str | None = None
    user_id: str | None = None


# ── Credential provider management ──────────────────────────────


class CreateCredentialProviderRequest(BaseModel):
    name: str
    provider_type: str
    config: dict[str, Any] = Field(default_factory=dict)


class UpdateCredentialProviderRequest(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)


class CredentialProviderInfo(BaseModel):
    name: str
    provider_type: str | None = None
    arn: str | None = None
    created_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ListCredentialProvidersResponse(BaseModel):
    credential_providers: list[CredentialProviderInfo]


# ── Workload identity management ────────────────────────────────


class CreateWorkloadIdentityRequest(BaseModel):
    name: str
    allowed_return_urls: list[str] = Field(default_factory=list)


class UpdateWorkloadIdentityRequest(BaseModel):
    allowed_return_urls: list[str] = Field(default_factory=list)


class WorkloadIdentityInfo(BaseModel):
    name: str
    arn: str | None = None
    allowed_return_urls: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ListWorkloadIdentitiesResponse(BaseModel):
    workload_identities: list[WorkloadIdentityInfo]
