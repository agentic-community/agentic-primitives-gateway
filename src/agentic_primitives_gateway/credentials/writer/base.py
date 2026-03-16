"""Abstract base class for credential writers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.credentials.models import CredentialUpdateRequest


class CredentialWriter(ABC):
    """Write per-user credentials to an external identity store."""

    @abstractmethod
    async def write(
        self,
        principal: AuthenticatedPrincipal,
        access_token: str,
        updates: CredentialUpdateRequest,
    ) -> None:
        """Write credential attributes for the given user."""
        ...

    @abstractmethod
    async def read(
        self,
        principal: AuthenticatedPrincipal,
        access_token: str,
    ) -> dict[str, Any]:
        """Read the user's current credential attributes."""
        ...

    @abstractmethod
    async def delete(
        self,
        principal: AuthenticatedPrincipal,
        access_token: str,
        key: str,
    ) -> None:
        """Delete a single credential attribute."""
        ...

    async def close(self) -> None:  # noqa: B027
        """Cleanup resources. Called on shutdown."""
