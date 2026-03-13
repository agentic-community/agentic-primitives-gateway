"""Authentication middleware — validates credentials and sets principal in context."""

from __future__ import annotations

import logging

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from agentic_primitives_gateway.auth.base import AuthBackend
from agentic_primitives_gateway.auth.models import ANONYMOUS_PRINCIPAL
from agentic_primitives_gateway.context import set_authenticated_principal

logger = logging.getLogger(__name__)

# Paths exempt from authentication (prefix match).
# Shared constant so other modules can reference it.
AUTH_EXEMPT_PREFIXES = (
    "/healthz",
    "/readyz",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/v1/openapi",
    "/auth/config",
    "/ui",
    "/.well-known/agent.json",
)

# Additional patterns checked via suffix match (for paths with variables).
AUTH_EXEMPT_SUFFIXES = ("/.well-known/agent.json",)


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Validate incoming requests and store the authenticated principal.

    Runs after ``RequestContextMiddleware`` (so contextvars are set) and
    before ``PolicyEnforcementMiddleware`` (so the enforcer can read the
    principal from context).

    Exempt paths skip authentication entirely and get the anonymous principal.

    When the auth backend returns ``None`` (missing/invalid credentials)
    and the backend is not noop, the middleware returns 401.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        backend: AuthBackend | None = getattr(request.app.state, "auth_backend", None)

        # No backend configured — anonymous pass-through
        if backend is None:
            set_authenticated_principal(ANONYMOUS_PRINCIPAL)
            return await call_next(request)

        # Exempt paths get anonymous principal without validation
        path = request.url.path
        for prefix in AUTH_EXEMPT_PREFIXES:
            if path.startswith(prefix):
                set_authenticated_principal(ANONYMOUS_PRINCIPAL)
                return await call_next(request)
        for suffix in AUTH_EXEMPT_SUFFIXES:
            if path.endswith(suffix):
                set_authenticated_principal(ANONYMOUS_PRINCIPAL)
                return await call_next(request)

        principal = await backend.authenticate(request)

        if principal is None:
            logger.warning("Authentication failed for %s %s", request.method, path)
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing credentials"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        set_authenticated_principal(principal)
        return await call_next(request)
