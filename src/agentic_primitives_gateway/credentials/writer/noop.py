"""No-op credential writer — read/write are unsupported."""

from __future__ import annotations

from typing import Any

from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.credentials.models import CredentialUpdateRequest
from agentic_primitives_gateway.credentials.writer.base import CredentialWriter


class NoopCredentialWriter(CredentialWriter):
    """Default writer: no credential write support.

    Returns empty data on read and raises on write.
    """

    async def write(
        self,
        principal: AuthenticatedPrincipal,
        access_token: str,
        updates: CredentialUpdateRequest,
    ) -> None:
        raise NotImplementedError("Credential writing is not configured")

    async def read(
        self,
        principal: AuthenticatedPrincipal,
        access_token: str,
    ) -> dict[str, Any]:
        return {}

    async def delete(
        self,
        principal: AuthenticatedPrincipal,
        access_token: str,
        key: str,
    ) -> None:
        raise NotImplementedError("Credential deletion is not configured")
