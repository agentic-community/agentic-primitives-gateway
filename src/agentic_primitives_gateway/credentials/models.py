"""Credential resolution data models."""

from __future__ import annotations

from pydantic import BaseModel

from agentic_primitives_gateway.context import AWSCredentials

# All gateway credential attributes use this prefix to avoid colliding
# with other OIDC user attributes.  Convention: apg.{service}.{key}
APG_PREFIX = "apg."


class ResolvedCredentials(BaseModel):
    """Credentials resolved for a single request/user."""

    aws: AWSCredentials | None = None
    service_credentials: dict[str, dict[str, str]] = {}

    model_config = {"arbitrary_types_allowed": True}


class CredentialUpdateRequest(BaseModel):
    """Request body for PUT /api/v1/credentials."""

    attributes: dict[str, str] = {}


class CredentialStatus(BaseModel):
    """Response for GET /api/v1/credentials/status."""

    source: str  # "oidc" | "headers" | "server" | "none"
    aws_configured: bool = False
    aws_credential_expiry: str | None = None


class MaskedCredentials(BaseModel):
    """Response for GET /api/v1/credentials — secrets are masked."""

    attributes: dict[str, str] = {}
    services: dict[str, dict[str, str]] = {}


def mask_value(value: str) -> str:
    """Mask a credential value for display, showing only last 4 chars."""
    if len(value) <= 4:
        return "****"
    return f"****{value[-4:]}"
