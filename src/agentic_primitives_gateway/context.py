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


def get_provider_overrides() -> dict[str, str]:
    """Return a snapshot of the currently-active provider overrides."""
    return dict(_provider_overrides.get())


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


def _emit_server_credentials_used(service: str) -> None:
    """Emit ``provider.server_credentials_used`` for non-admin callers.

    Admin callers are deliberately excluded — ambient-cred use by admins
    is the expected operator path.  The event exists to surface the
    "backend sees one shared principal" state for non-admin requests, so
    audit consumers can tell when per-user isolation has collapsed.
    """
    principal = get_authenticated_principal()
    if principal is not None and principal.is_admin:
        return

    from agentic_primitives_gateway.audit.emit import emit_audit_event
    from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType
    from agentic_primitives_gateway.config import settings

    mode = settings.allow_server_credentials
    mode_str = str(mode.value) if hasattr(mode, "value") else str(mode)
    emit_audit_event(
        action=AuditAction.PROVIDER_SERVER_CREDENTIALS_USED,
        outcome=AuditOutcome.SUCCESS,
        resource_type=ResourceType.CREDENTIAL,
        resource_id=service,
        metadata={"service": service, "mode": mode_str},
    )


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
        _emit_server_credentials_used("aws")
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
    """Resolve service config by mode of ``allow_server_credentials``.

    Three modes, three rules — no per-key classification, no silent
    merge paths that mix caller and operator values except in the one
    mode that is explicitly defined as "allow mixing":

    * ``NEVER`` — caller supplies the entire shape.  ``defaults`` is
      **not consulted for values**.  We do return a dict keyed on
      ``defaults.keys()`` so providers that use bracket access don't
      explode on a missing key; caller-supplied values land on their
      keys, everything else is ``None``.  If ``client_creds`` is empty
      we raise — the gateway has no credentials at all and the caller
      has to know that up-front rather than see a cryptic backend
      error.
    * ``ALWAYS`` — operator controls credentials.  Caller-supplied
      values are ignored entirely; we return ``dict(defaults)``
      unchanged.  A non-admin caller under this mode triggers the
      ``provider.server_credentials_used`` event so the degraded
      state is auditable.
    * ``FALLBACK`` — merge, caller wins on leaves.  The only mode
      where mixing is allowed.  If any key in ``defaults`` was filled
      from the operator side (i.e. the caller didn't supply a truthy
      value for it), emit ``provider.server_credentials_used`` once.
    """
    from agentic_primitives_gateway.models.enums import ServerCredentialMode

    client_creds: dict[str, str] = {k: v for k, v in (get_service_credentials(service) or {}).items() if v}
    mode = _resolve_server_credential_mode()

    if mode == ServerCredentialMode.ALWAYS:
        _emit_server_credentials_used(service)
        return dict(defaults)

    if mode == ServerCredentialMode.NEVER:
        if not client_creds:
            raise ValueError(
                f"No {service} credentials provided in request headers and server "
                f"credential fallback is disabled. Either pass credentials via "
                f"X-Cred-{service.capitalize()}-* headers from the client, or enable "
                f"server credentials with allow_server_credentials: fallback in the server config."
            )
        # Preserve the shape of ``defaults`` so providers using
        # bracket access (``cfg["realm"]``) don't raise ``KeyError``
        # when the caller omits a key.  ``None`` is the signal "not
        # configured", which providers should already be prepared to
        # reject on downstream use.
        shape_preserving: dict[str, str | None] = dict.fromkeys(defaults)
        shape_preserving.update(client_creds)
        return shape_preserving

    # FALLBACK — merge with caller winning.  Track which keys the
    # operator filled so the audit event fires at the right granularity.
    merged: dict[str, str | None] = dict(defaults)
    merged.update(client_creds)
    server_filled = [k for k, v in defaults.items() if v and k not in client_creds]
    if server_filled:
        _emit_server_credentials_used(service)
    return merged


def _resolve_server_credential_mode() -> Any:
    """Read ``allow_server_credentials`` and normalise to the enum.

    ``settings.allow_server_credentials`` can be either a bool (legacy)
    or a :class:`ServerCredentialMode` value.  The three-mode semantics
    of this module treat a bool as ``ALWAYS`` (``True``) or ``NEVER``
    (``False``); there is no bool equivalent of ``FALLBACK``.  Config
    validation converts bools at load time for shipped configs, but
    we defend here in case an operator sets a bool programmatically.
    """
    from agentic_primitives_gateway.config import settings
    from agentic_primitives_gateway.models.enums import ServerCredentialMode

    mode = settings.allow_server_credentials
    if isinstance(mode, bool):
        return ServerCredentialMode.ALWAYS if mode else ServerCredentialMode.NEVER
    return mode
