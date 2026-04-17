"""HTTP request audit middleware.

Emits one ``http.request`` event per non-exempt request with the final
status code, duration, principal, and request path.  Runs inside
:class:`~starlette.middleware.base.BaseHTTPMiddleware` so it wraps the
auth / credential / policy chain and sees the authoritative response.
"""

from __future__ import annotations

import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from agentic_primitives_gateway.audit.emit import emit_audit_event
from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType

# Prefixes whose requests are *not* emitted as http.request audit events.
# Health + metrics endpoints run at high frequency and would dominate the
# audit stream with no operational value.  ``/ui`` and ``/docs`` are
# browser bootstraps that also generate many requests per page load.
DEFAULT_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/healthz",
    "/readyz",
    "/metrics",
    "/ui",
    "/docs",
    "/redoc",
    "/openapi.json",
)


class AuditMiddleware(BaseHTTPMiddleware):
    """Time each request and emit a single ``http.request`` audit event."""

    def __init__(self, app, exempt_prefixes: tuple[str, ...] = DEFAULT_EXEMPT_PREFIXES) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self._exempt_prefixes = exempt_prefixes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if any(path.startswith(prefix) for prefix in self._exempt_prefixes):
            return await call_next(request)

        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration_ms = (time.perf_counter() - start) * 1000.0
            outcome = AuditOutcome.SUCCESS if status_code < 400 else AuditOutcome.FAILURE
            client = request.client
            # Auth middleware runs downstream in its own task and sets the
            # principal on ``request.state`` for us (contextvars set by an
            # inner BaseHTTPMiddleware don't propagate back out).
            principal = getattr(request.state, "principal", None)
            emit_audit_event(
                action=AuditAction.HTTP_REQUEST,
                outcome=outcome,
                resource_type=ResourceType.HTTP,
                resource_id=path,
                http_method=request.method,
                http_path=path,
                http_status=status_code,
                duration_ms=round(duration_ms, 3),
                source_ip=client.host if client else None,
                user_agent=request.headers.get("user-agent"),
                actor_id=principal.id if principal else None,
                actor_type=principal.type if principal else None,
                actor_groups=sorted(principal.groups) if principal else None,
            )
