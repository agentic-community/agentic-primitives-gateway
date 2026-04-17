"""Request-scoped context for credential pass-through and provider routing.

Three context systems:

1. **AWS credentials** — AgentCore providers read these to create per-request
   boto3 sessions with the caller's identity.
2. **Service credentials** — Generic key-value credentials for any service
   (Langfuse, OpenAI, etc.). Providers read by service name.
3. **Provider routing** — Selects which named backend to use per-request.

Credential fallback behavior is controlled by ``allow_server_credentials``
in the server config. When disabled (default), requests without client
credentials will fail rather than silently using the server's own credentials.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal


@dataclass(frozen=True)
class AWSCredentials:
    access_key_id: str
    secret_access_key: str
    session_token: str | None = None
    region: str | None = None


# ── Request ID ─────────────────────────────────────────────────────

_request_id: ContextVar[str] = ContextVar("_request_id", default="")


def set_request_id(request_id: str) -> None:
    _request_id.set(request_id)


def get_request_id() -> str:
    return _request_id.get()


# ── Correlation ID ─────────────────────────────────────────────────
#
# ``request_id`` is unique per HTTP request.  ``correlation_id`` is
# threaded across sub-agent calls, background runs, and reconnects so a
# multi-step agent workflow appears as one chain in audit/logs/traces.

_correlation_id: ContextVar[str] = ContextVar("_correlation_id", default="")


def set_correlation_id(correlation_id: str) -> None:
    _correlation_id.set(correlation_id)


def get_correlation_id() -> str:
    return _correlation_id.get()


# ── AWS credentials ───────────────────────────────────────────────

_aws_credentials: ContextVar[AWSCredentials | None] = ContextVar("_aws_credentials", default=None)


def set_aws_credentials(creds: AWSCredentials | None) -> None:
    _aws_credentials.set(creds)


def get_aws_credentials() -> AWSCredentials | None:
    return _aws_credentials.get()


# ── Service credentials ─────────────────────────────────────────────

_service_credentials: ContextVar[dict[str, dict[str, str]]] = ContextVar("_service_credentials", default={})  # noqa: B039


def set_service_credentials(creds: dict[str, dict[str, str]]) -> None:
    _service_credentials.set(creds)


def get_service_credentials(service: str) -> dict[str, str] | None:
    """Get credentials for a service from the current request context.

    Returns None if no credentials were provided for this service.
    """
    creds = _service_credentials.get()
    return creds.get(service) or None


# ── Authenticated principal ─────────────────────────────────────────

_authenticated_principal: ContextVar[AuthenticatedPrincipal | None] = ContextVar(
    "_authenticated_principal", default=None
)


def set_authenticated_principal(principal: AuthenticatedPrincipal | None) -> None:
    _authenticated_principal.set(principal)


def get_authenticated_principal() -> AuthenticatedPrincipal | None:
    return _authenticated_principal.get()


# ── Access token ───────────────────────────────────────────────────

_access_token: ContextVar[str | None] = ContextVar("_access_token", default=None)


def set_access_token(token: str | None) -> None:
    _access_token.set(token)


def get_access_token() -> str | None:
    return _access_token.get()


# ── Provider routing ────────────────────────────────────────────────

_provider_overrides: ContextVar[dict[str, str]] = ContextVar("_provider_overrides", default={})  # noqa: B039


def set_provider_overrides(overrides: dict[str, str]) -> None:
    _provider_overrides.set(overrides)


def get_provider_override(primitive: str) -> str | None:
    overrides = _provider_overrides.get()
    return overrides.get(primitive) or overrides.get("default")


# ── Credential resolution with server fallback control ──────────────


def _server_credentials_allowed() -> bool:
    """Check if the server is configured to allow its own credentials as fallback."""
    from agentic_primitives_gateway.config import settings

    mode = settings.allow_server_credentials
    if isinstance(mode, bool):
        return mode
    # ServerCredentialMode enum
    from agentic_primitives_gateway.models.enums import ServerCredentialMode

    return mode in (ServerCredentialMode.FALLBACK, ServerCredentialMode.ALWAYS)


def get_boto3_session(default_region: str = "us-east-1") -> Any:
    """Create a boto3 Session from the current request's AWS credentials.

    If client credentials are in context, uses those. Otherwise:
    - If ``allow_server_credentials`` is True, falls back to the server's
      credential chain (env vars, instance profile, etc.).
    - If ``allow_server_credentials`` is False (default), raises ValueError.
    """
    import boto3

    creds = get_aws_credentials()
    if creds is not None:
        return boto3.Session(
            aws_access_key_id=creds.access_key_id,
            aws_secret_access_key=creds.secret_access_key,
            aws_session_token=creds.session_token,
            region_name=creds.region or default_region,
        )

    if _server_credentials_allowed():
        return boto3.Session(region_name=default_region)

    raise ValueError(
        "No AWS credentials provided in request headers and server credential "
        "fallback is disabled. Either pass credentials via X-AWS-* headers from "
        "the client, or enable server credentials with "
        "allow_server_credentials: true in the server config."
    )


def get_service_credentials_or_defaults(
    service: str,
    defaults: dict[str, str | None],
) -> dict[str, str | None]:
    """Get service credentials from context, with optional server-side defaults.

    Args:
        service: Service name (e.g., 'langfuse').
        defaults: Server-configured default values for this service.

    Returns:
        Merged credentials (client overrides server defaults).

    Raises:
        ValueError: If no client credentials and server fallback is disabled,
            and the defaults contain required values that are None.
    """
    client_creds = get_service_credentials(service) or {}

    # Client credentials take priority
    merged = dict(defaults)
    merged.update({k: v for k, v in client_creds.items() if v})

    # If we got credentials from the client, always allow
    if client_creds:
        return merged

    # No client credentials — check if server fallback is allowed
    if _server_credentials_allowed():
        return merged

    # Check if the server defaults actually have values
    has_defaults = any(v for v in defaults.values() if v)
    if has_defaults:
        raise ValueError(
            f"No {service} credentials provided in request headers and server "
            f"credential fallback is disabled. Either pass credentials via "
            f"X-Cred-{service.capitalize()}-* headers from the client, or enable "
            f"server credentials with allow_server_credentials: true in the server config."
        )

    return merged
