"""Credential resolution middleware — resolves per-user credentials from OIDC."""

from __future__ import annotations

import logging

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from agentic_primitives_gateway.context import (
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

    If explicit headers already populated credentials, this middleware skips
    resolution to preserve the "headers win" behavior.
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

        # Check if explicit headers already provided credentials
        has_aws_headers = get_aws_credentials() is not None
        has_service_headers = any(h.startswith("x-cred-") for h in request.headers)

        if has_aws_headers and has_service_headers:
            # Both header types present — headers win completely
            return await call_next(request)

        # Resolve credentials from OIDC
        resolved = await resolver.resolve(principal, access_token)
        if resolved is None:
            return await call_next(request)

        # Populate AWS credentials if not already set by headers
        if not has_aws_headers and resolved.aws is not None:
            set_aws_credentials(resolved.aws)

        # Populate service credentials if not already set by headers
        if not has_service_headers and resolved.service_credentials:
            set_service_credentials(resolved.service_credentials)

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
