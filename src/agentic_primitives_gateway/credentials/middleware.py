"""Credential resolution middleware — resolves per-user credentials from OIDC."""

from __future__ import annotations

import logging

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from agentic_primitives_gateway import metrics
from agentic_primitives_gateway.audit.emit import emit_audit_event
from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType
from agentic_primitives_gateway.context import (
    get_all_service_credentials,
    get_authenticated_principal,
    get_aws_credentials,
    set_access_token,
    set_aws_credentials,
    set_service_credentials,
)
from agentic_primitives_gateway.credentials.base import CredentialResolver

logger = logging.getLogger(__name__)


class CredentialResolutionMiddleware(BaseHTTPMiddleware):
    """Resolve per-user credentials from OIDC userinfo and populate contextvars.

    Runs after ``AuthenticationMiddleware`` (so the principal is set) and before
    ``PolicyEnforcementMiddleware``.

    Resolution priority:
    1. Explicit headers (``X-AWS-*``, ``X-Cred-*``) — already set by RequestContextMiddleware
    2. OIDC-resolved credentials — fetched from userinfo (this middleware)
    3. Server ambient credentials — handled downstream by providers

    Service-credential precedence is per *key* and counts only non-empty
    header keys ``RequestContextMiddleware`` actually accepted: a header value
    replaces the user's OIDC value for that key, and the user's other OIDC
    keys are kept.  A rejected, empty, or unrelated header must never knock
    out the user's own OIDC credentials — under ``fallback`` that would
    silently switch them to operator credentials (e.g. an
    ``X-Cred-Agentcore-Memory-Id`` header dropping the user's
    ``apg.agentcore.gateway_token``).
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        resolver: CredentialResolver | None = getattr(request.app.state, "credential_resolver", None)
        if resolver is None:
            return await call_next(request)

        principal = get_authenticated_principal()
        if principal is None or principal.is_anonymous:
            return await call_next(request)

        # Extract and store the access token for downstream use
        access_token = self._extract_access_token(request)
        if access_token:
            set_access_token(access_token)

        resolved = await resolver.resolve(principal, access_token)
        if resolved is None:
            return await call_next(request)

        applied: list[str] = []
        if get_aws_credentials() is None and resolved.aws is not None:
            set_aws_credentials(resolved.aws)
            applied.append("aws")

        # Per key: an accepted header value wins for that key; the user's own
        # OIDC values fill the rest of the service.  Both sides are the
        # caller's — operator defaults are never merged in here.
        header_creds = get_all_service_credentials()
        merged = {svc: dict(kv) for svc, kv in header_creds.items()}
        oidc_services: list[str] = []
        for svc, kv in resolved.service_credentials.items():
            fill = {k: v for k, v in kv.items() if v and k not in merged.get(svc, {})}
            if fill:
                merged.setdefault(svc, {}).update(fill)
                oidc_services.append(svc)
        if oidc_services:
            set_service_credentials(merged, source="oidc")

        # Audit what was applied from OIDC (names only — never values).
        applied = sorted(oidc_services) + applied
        if applied:
            emit_audit_event(
                action=AuditAction.CREDENTIAL_RESOLVE,
                outcome=AuditOutcome.SUCCESS,
                resource_type=ResourceType.CREDENTIAL,
                metadata={"services": applied, "source": "oidc"},
            )
            for service in applied:
                metrics.CREDENTIAL_OPS.labels(op="resolve", service=service, outcome="success").inc()

        return await call_next(request)

    @staticmethod
    def _extract_access_token(request: Request) -> str | None:
        """Extract Bearer token from the Authorization header."""
        auth_header = request.headers.get("authorization")
        if not auth_header:
            return None
        parts = auth_header.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None
        return parts[1].strip()
