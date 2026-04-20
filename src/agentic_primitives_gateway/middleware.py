"""Request context middleware — extracts credentials and routing from headers."""

from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from agentic_primitives_gateway.context import (
    AWSCredentials,
    set_aws_credentials,
    set_correlation_id,
    set_provider_overrides,
    set_request_id,
    set_service_credentials,
)
from agentic_primitives_gateway.registry import PRIMITIVES

logger = logging.getLogger(__name__)

# Keys forbidden in ``X-Cred-{service}-{key}`` headers.  Host / URL /
# endpoint / port are configuration, not per-request credentials —
# letting a caller override them would rewrite which host a provider
# talks to, giving any authenticated user a one-header SSRF into any
# service the provider reaches (cloud metadata, internal admin APIs,
# etc.).  Actual opaque secrets (``public_key``, ``secret_key``,
# ``api_key``, ``admin_client_secret``) continue to pass through.
_FORBIDDEN_CRED_KEY_SUBSTRINGS: frozenset[str] = frozenset({"url", "uri", "host", "endpoint", "origin", "port"})


def _is_forbidden_cred_key(key: str) -> bool:
    lowered = key.lower()
    return any(substr in lowered for substr in _FORBIDDEN_CRED_KEY_SUBSTRINGS)


def _emit_cred_key_denied(*, service: str, key: str, http_path: str) -> None:
    """Audit the rejection of a forbidden X-Cred-* header key.

    Emits a ``network.access.denied`` event so SIEM can alert on
    attempted SSRF via credential-header injection.  Deferred import
    matches the rest of the audit integration pattern — no module-load
    cost for code paths that don't exercise audit.
    """
    try:
        from agentic_primitives_gateway.audit.emit import emit_audit_event
        from agentic_primitives_gateway.audit.models import (
            AuditAction,
            AuditOutcome,
            ResourceType,
        )
    except ImportError:  # pragma: no cover
        return
    emit_audit_event(
        action=AuditAction.NETWORK_ACCESS_DENIED,
        outcome=AuditOutcome.DENY,
        resource_type=ResourceType.CREDENTIAL,
        resource_id=f"{service}:{key}",
        reason="blocked_cred_key",
        http_path=http_path,
        metadata={"service": service, "key": key},
    )


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Extract AWS credentials and provider routing from request headers.

    AWS credential headers:
        X-AWS-Access-Key-Id       (required for pass-through)
        X-AWS-Secret-Access-Key   (required for pass-through)
        X-AWS-Session-Token       (optional, for temporary credentials)
        X-AWS-Region              (optional, overrides provider default)

    Service credential headers (generic, for any service):
        X-Cred-{Service}-{Key}    e.g. X-Cred-Langfuse-Public-Key
        Parsed into: {"langfuse": {"public_key": "..."}}

    Provider routing headers:
        X-Provider                (default provider for all primitives)
        X-Provider-Memory         (override for memory)
        X-Provider-Identity       (override for identity)
        X-Provider-Code-Interpreter (override for code_interpreter)
        X-Provider-Browser        (override for browser)
        X-Provider-Observability  (override for observability)
        X-Provider-Gateway        (override for gateway)
        X-Provider-Tools          (override for tools)
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Request ID
        request_id = request.headers.get("x-request-id") or uuid4().hex
        set_request_id(request_id)

        # Correlation ID — threads across sub-agent calls + background runs.
        # Falls back to the request_id so callers that don't pass one still
        # get a usable chain identifier.
        correlation_id = request.headers.get("x-correlation-id") or request_id
        set_correlation_id(correlation_id)

        # AWS credentials
        access_key = request.headers.get("x-aws-access-key-id")
        secret_key = request.headers.get("x-aws-secret-access-key")

        if access_key and secret_key:
            set_aws_credentials(
                AWSCredentials(
                    access_key_id=access_key,
                    secret_access_key=secret_key,
                    session_token=request.headers.get("x-aws-session-token"),
                    region=request.headers.get("x-aws-region"),
                )
            )
        else:
            set_aws_credentials(None)

        # Service credentials (X-Cred-{Service}-{Key} headers).
        # URL/host/endpoint keys are filtered out — treat those as
        # server-side configuration, not caller-supplied credentials.
        # Otherwise any authenticated request could redirect a provider
        # to an attacker-chosen host (SSRF).
        service_creds: dict[str, dict[str, str]] = {}
        for header_name, header_value in request.headers.items():
            if not header_name.startswith("x-cred-"):
                continue
            parts = header_name.removeprefix("x-cred-").split("-", 1)
            if len(parts) != 2:
                continue
            service = parts[0]
            key = parts[1].replace("-", "_")
            if _is_forbidden_cred_key(key):
                logger.warning(
                    "Ignoring forbidden X-Cred-* key %r on service %r — "
                    "URL/host/endpoint fields are server-config only",
                    key,
                    service,
                )
                _emit_cred_key_denied(service=service, key=key, http_path=request.url.path)
                continue
            service_creds.setdefault(service, {})[key] = header_value
        set_service_credentials(service_creds)

        # Provider routing
        overrides: dict[str, str] = {}
        if default_provider := request.headers.get("x-provider"):
            overrides["default"] = default_provider
        for primitive in PRIMITIVES:
            header = f"x-provider-{primitive.replace('_', '-')}"
            if value := request.headers.get(header):
                overrides[primitive] = value
        set_provider_overrides(overrides)

        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        response.headers["x-correlation-id"] = correlation_id
        return response
