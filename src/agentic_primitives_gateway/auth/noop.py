"""No-op auth backend — all requests pass as anonymous."""

from __future__ import annotations

from starlette.requests import Request

from agentic_primitives_gateway.auth.base import AuthBackend
from agentic_primitives_gateway.auth.models import ANONYMOUS_PRINCIPAL, AuthenticatedPrincipal


class NoopAuthBackend(AuthBackend):
    """Default backend: no authentication, all requests are anonymous.

    This preserves the existing behavior — no 401s, no token validation.
    """

    async def authenticate(self, request: Request) -> AuthenticatedPrincipal | None:
        return ANONYMOUS_PRINCIPAL
