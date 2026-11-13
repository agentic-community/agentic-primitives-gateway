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


_request_path: ContextVar[str] = ContextVar("_request_path", default="")


def set_request_path(path: str) -> None:
    _request_path.set(path)


def get_request_path() -> str:
    return _request_path.get()


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


def set_service_credentials(creds: dict[str, dict[str, str]], *, source: str = "context") -> None:
    """Install caller service credentials for the current context.

    The single writer of the contextvar.  Empty values are dropped (an empty
    ``X-Cred-*`` header is "not supplied", and must not count as the caller
    owning that service's credentials), and endpoint-shaped keys (see
    ``credentials.security``) are dropped and audited, whatever the channel
    (``header``, ``oidc``, ``checkpoint``, …), so no provider can ever read a
    caller-chosen URL/host.
    """
    from agentic_primitives_gateway.credentials.security import drop_forbidden_credential_keys

    non_empty = {svc: kept for svc, kv in creds.items() if (kept := {k: v for k, v in kv.items() if v})}
    _service_credentials.set(drop_forbidden_credential_keys(non_empty, source=source))


def get_all_service_credentials() -> dict[str, dict[str, str]]:
    """Return every service's credentials installed for the current context (read-only use)."""
    return _service_credentials.get()


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


def require_server_credentials(service: str) -> None:
    """Gate an operation that can only run with the gateway's own credentials.

    For backends that are operator-owned by construction (e.g. a process-wide
    exporter signed with the server's AWS identity).  Raises ``ValueError``
    under ``allow_server_credentials: never``; otherwise records
    ``provider.server_credentials_used`` for non-admin callers.
    """
    if not _server_credentials_allowed():
        raise ValueError(
            f"{service} only runs with the gateway's own credentials, which "
            "allow_server_credentials: never forbids. Set allow_server_credentials: "
            "fallback or always, or use a different backend."
        )
    _emit_server_credentials_used(service)


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


def _caller_aws_credentials() -> AWSCredentials | None:
    """Caller AWS credentials in force for this request — ignored under ``always``."""
    from agentic_primitives_gateway.models.enums import ServerCredentialMode

    if _resolve_server_credential_mode() == ServerCredentialMode.ALWAYS:
        return None
    return get_aws_credentials()


def get_caller_resource_id(service: str, key: str) -> str | None:
    """Return a caller-selected AWS resource ID (``memory_id``, ``knowledgebase_id``, …).

    Honored only when the call runs under the caller's own AWS credentials.
    Under the operator's ambient identity (``fallback`` without caller
    credentials, or ``always``) a caller-chosen ID would aim the operator's
    role at any resource it can reach, so ``None`` is returned and providers
    use their configured default.
    """
    if _caller_aws_credentials() is None:
        return None
    return (get_service_credentials(service) or {}).get(key) or None


def get_boto3_session(default_region: str = "us-east-1") -> Any:
    """Create a boto3 Session for the current request.

    Caller AWS credentials in context are used except under ``always``.
    Otherwise, under ``fallback`` / ``always`` the server's credential chain
    (env vars, instance profile, etc.) is used and audited; under ``never``
    a ``ValueError`` is raised.
    """
    import boto3

    creds = _caller_aws_credentials()
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
        "allow_server_credentials: fallback in the server config."
    )


def get_service_credentials_or_defaults(
    service: str,
    defaults: dict[str, str | None],
) -> dict[str, str | None]:
    """Resolve service config by mode of ``allow_server_credentials``.

    Caller and operator credentials are never mixed within a service: a
    caller's partial set combined with operator secrets lets the caller pick
    *which* realm / tenant / client / project the operator's secret is used
    against.  So the caller's set is used whole or not at all:

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
    * ``FALLBACK`` — if the caller supplied any of *this provider's* keys
      (``defaults.keys()``) with a non-empty value, behave exactly like
      ``NEVER`` (caller's set only, missing keys ``None``).  Only when the
      caller supplied none are the operator defaults used, emitting
      ``provider.server_credentials_used``.  Unrelated keys sharing the
      service bucket (e.g. ``agentcore.memory_id`` vs the gateway's
      ``agentcore.gateway_token``) don't count.

    Endpoint keys (``base_url``, ``server_url``, ``hub_url``, ``domain``,
    … — see :func:`credentials.security.is_forbidden_credential_key`) are
    operator configuration in every mode.  ``set_service_credentials``
    never lets a caller value for them into the context, and they are
    always filled from ``defaults`` here.
    """
    from agentic_primitives_gateway.credentials.security import is_forbidden_credential_key
    from agentic_primitives_gateway.models.enums import ServerCredentialMode

    caller = get_service_credentials(service) or {}
    client_creds: dict[str, str] = {k: v for k, v in caller.items() if v and k in defaults}
    mode = _resolve_server_credential_mode()

    if mode == ServerCredentialMode.ALWAYS:
        _emit_server_credentials_used(service)
        return dict(defaults)

    if client_creds:
        # Caller's set, whole.  Preserve the shape of ``defaults`` so
        # providers using bracket access (``cfg["realm"]``) don't raise
        # ``KeyError`` when the caller omits a key.  ``None`` is the signal
        # "not configured", which providers reject on downstream use.
        # Endpoint keys keep the operator value — config, not credentials.
        shape_preserving: dict[str, str | None] = {
            k: (v if is_forbidden_credential_key(k) else None) for k, v in defaults.items()
        }
        shape_preserving.update(client_creds)
        return shape_preserving

    if mode == ServerCredentialMode.NEVER:
        raise ValueError(
            f"No {service} credentials provided in request headers and server "
            f"credential fallback is disabled. Either pass credentials via "
            f"X-Cred-{service.capitalize()}-* headers from the client, or enable "
            f"server credentials with allow_server_credentials: fallback in the server config."
        )

    # FALLBACK with no caller credentials — the operator's set, whole.
    if any(v for k, v in defaults.items() if not is_forbidden_credential_key(k)):
        _emit_server_credentials_used(service)
    return dict(defaults)


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
