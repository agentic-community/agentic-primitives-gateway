"""No-op auth backend — all requests pass as anonymous."""

from __future__ import annotations

from starlette.requests import Request

from agentic_primitives_gateway.auth.base import AuthBackend
from agentic_primitives_gateway.auth.models import NOOP_PRINCIPAL, AuthenticatedPrincipal


class NoopAuthBackend(AuthBackend):
    """Default backend: no authentication, full access.

    Returns a non-anonymous principal with admin scope so that all
    ownership and access checks pass. This is for dev/testing only —
    production deployments should use ``api_key`` or ``jwt``.
    """

    async def authenticate(self, request: Request) -> AuthenticatedPrincipal | None:
        return NOOP_PRINCIPAL
