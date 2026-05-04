"""Authentication middleware — validates credentials and sets principal in context."""

from __future__ import annotations

import logging

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from agentic_primitives_gateway import metrics
from agentic_primitives_gateway.audit.emit import emit_audit_event
from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType
from agentic_primitives_gateway.auth.access import filter_allowed_provider_overrides
from agentic_primitives_gateway.auth.base import AuthBackend
from agentic_primitives_gateway.auth.models import ANONYMOUS_PRINCIPAL, NOOP_PRINCIPAL, AuthenticatedPrincipal
from agentic_primitives_gateway.context import (
    get_provider_overrides,
    set_authenticated_principal,
    set_provider_overrides,
)

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

    When no auth backend is configured (dev mode), all requests get the
    noop principal (admin access).

    Exempt paths get the anonymous principal (non-admin) so that
    resource-level access checks (e.g. private agent discovery) still apply.

    When the auth backend returns ``None`` (missing/invalid credentials)
    and the backend is not noop, the middleware returns 401.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        backend: AuthBackend | None = getattr(request.app.state, "auth_backend", None)

        # No backend configured — noop pass-through (dev mode, full access)
        if backend is None:
            set_authenticated_principal(NOOP_PRINCIPAL)
            request.state.principal = NOOP_PRINCIPAL
            _enforce_provider_override_allowlist(NOOP_PRINCIPAL, request.url.path)
            return await call_next(request)

        # Exempt paths get anonymous principal (non-admin) without validation
        path = request.url.path
        for prefix in AUTH_EXEMPT_PREFIXES:
            if path.startswith(prefix):
                set_authenticated_principal(ANONYMOUS_PRINCIPAL)
                request.state.principal = ANONYMOUS_PRINCIPAL
                _enforce_provider_override_allowlist(ANONYMOUS_PRINCIPAL, path)
                return await call_next(request)
        for suffix in AUTH_EXEMPT_SUFFIXES:
            if path.endswith(suffix):
                set_authenticated_principal(ANONYMOUS_PRINCIPAL)
                request.state.principal = ANONYMOUS_PRINCIPAL
                _enforce_provider_override_allowlist(ANONYMOUS_PRINCIPAL, path)
                return await call_next(request)

        principal = await backend.authenticate(request)
        backend_name = type(backend).__name__

        if principal is None:
            logger.warning("Authentication failed for %s %s", request.method, path)
            emit_audit_event(
                action=AuditAction.AUTH_FAILURE,
                outcome=AuditOutcome.FAILURE,
                resource_type=ResourceType.SESSION,
                reason="invalid_or_missing_credentials",
                http_method=request.method,
                http_path=path,
                metadata={"backend": backend_name},
            )
            metrics.AUTH_EVENTS.labels(
                backend=backend_name,
                outcome="failure",
                principal_type="unknown",
            ).inc()
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing credentials"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        set_authenticated_principal(principal)
        request.state.principal = principal
        _enforce_provider_override_allowlist(principal, path)
        emit_audit_event(
            action=AuditAction.AUTH_SUCCESS,
            outcome=AuditOutcome.SUCCESS,
            resource_type=ResourceType.SESSION,
            resource_id=principal.id,
            http_method=request.method,
            http_path=path,
            metadata={"backend": backend_name},
        )
        metrics.AUTH_EVENTS.labels(
            backend=backend_name,
            outcome="success",
            principal_type=principal.type,
        ).inc()
        return await call_next(request)


def _enforce_provider_override_allowlist(principal: AuthenticatedPrincipal, path: str) -> None:
    """Strip ``X-Provider-*`` overrides that are not on the universal allow-list.

    ``RequestContextMiddleware`` (which runs earlier) populates the
    provider-override contextvar verbatim from the request headers.
    The allow-list lives in :mod:`auth.access` and is *universal* —
    it applies to every caller, admin included.  Request-time backend
    selection is a routing preference for non-trust-sensitive
    primitives; for identity / policy / tools / the global default,
    the configured backend is the authoritative choice and a request
    header can't override it.  Admins who need to pin a backend for
    testing use startup config or a shadow deployment, not this path.
    """
    overrides = get_provider_overrides()
    if not overrides:
        return
    kept, dropped = filter_allowed_provider_overrides(overrides)
    if not dropped:
        return
    set_provider_overrides(kept)
    emit_audit_event(
        action=AuditAction.RESOURCE_ACCESS_DENIED,
        outcome=AuditOutcome.FAILURE,
        resource_type=ResourceType.SESSION,
        resource_id=principal.id,
        reason="provider_override_not_on_allowlist",
        http_path=path,
        metadata={"dropped_overrides": dropped},
    )
