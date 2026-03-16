"""Abstract base class for credential resolvers."""

from __future__ import annotations

from abc import ABC, abstractmethod

from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.credentials.models import ResolvedCredentials


class CredentialResolver(ABC):
    """Resolve per-user credentials from an external source (e.g. OIDC userinfo)."""

    @abstractmethod
    async def resolve(
        self,
        principal: AuthenticatedPrincipal,
        access_token: str | None,
    ) -> ResolvedCredentials | None:
        """Resolve credentials for the given principal.

        Returns None if no credentials could be resolved (e.g. no token,
        no user attributes configured).
        """
        ...

    async def close(self) -> None:  # noqa: B027
        """Cleanup resources. Called on shutdown."""
