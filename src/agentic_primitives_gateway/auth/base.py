"""Abstract base class for authentication backends."""

from __future__ import annotations

from abc import ABC, abstractmethod

from starlette.requests import Request

from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal


class AuthBackend(ABC):
    """Validate incoming requests and return an authenticated principal.

    Implementations extract credentials from the request (Bearer token,
    API key, etc.) and return an ``AuthenticatedPrincipal`` on success
    or ``None`` if credentials are missing/invalid.
    """

    @abstractmethod
    async def authenticate(self, request: Request) -> AuthenticatedPrincipal | None:
        """Authenticate the request.

        Returns:
            An ``AuthenticatedPrincipal`` if valid credentials are present,
            or ``None`` if credentials are missing or invalid.
        """
        ...

    async def close(self) -> None:  # noqa: B027
        """Cleanup resources. Called on shutdown. Default is a no-op."""
