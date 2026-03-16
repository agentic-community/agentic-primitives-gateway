"""No-op credential resolver — returns None (no per-user credentials)."""

from __future__ import annotations

from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.credentials.base import CredentialResolver
from agentic_primitives_gateway.credentials.models import ResolvedCredentials


class NoopCredentialResolver(CredentialResolver):
    """Default resolver: no per-user credential resolution.

    Used in dev mode where server ambient credentials are sufficient.
    """

    async def resolve(
        self,
        principal: AuthenticatedPrincipal,
        access_token: str | None,
    ) -> ResolvedCredentials | None:
        return None
