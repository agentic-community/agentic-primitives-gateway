"""Endpoint-key policy shared by every caller-controlled credential channel.

Caller-controlled channels are ``X-Cred-{service}-{key}`` headers and the
``apg.{service}.{key}`` OIDC user attributes users can edit through
``PUT /api/v1/credentials``.  Both feed the same ``service_credentials``
contextvar, so both must apply the same rule: URL / host / endpoint /
port / domain keys select *where* a provider sends traffic (often together
with operator-held secrets under ``allow_server_credentials: fallback``).
That is operator configuration, never a per-request credential — accepting
it from a caller is a one-step SSRF and server-secret exfiltration.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_FORBIDDEN_KEY_SUBSTRINGS: frozenset[str] = frozenset({"url", "uri", "host", "endpoint", "origin", "port", "domain"})


def is_forbidden_credential_key(key: str) -> bool:
    """Return whether a caller-supplied credential key selects an endpoint.

    Substring match (case-insensitive) so ``base_url``, ``hub_url``,
    ``server_url``, ``baseUrl`` and ``endpoint`` are all caught.
    """
    lowered = key.lower()
    return any(substr in lowered for substr in _FORBIDDEN_KEY_SUBSTRINGS)


def split_credential_attribute(remainder: str) -> tuple[str, str] | None:
    """Split an ``apg.``-stripped attribute name into ``(service, key)``.

    ``langfuse.public_key`` → ``("langfuse", "public_key")``; a name without a
    service segment maps to the ``_global`` service.  Returns ``None`` for
    an empty name.
    """
    if not remainder:
        return None
    parts = remainder.split(".", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return "_global", parts[0]


def drop_forbidden_credential_keys(creds: dict[str, dict[str, str]], *, source: str) -> dict[str, dict[str, str]]:
    """Return ``creds`` without endpoint-shaped keys, auditing each one dropped.

    Services left with no keys are omitted.  Returns ``creds`` itself when
    nothing is dropped (the common case) to avoid copying on every request.
    """
    if not any(is_forbidden_credential_key(k) for keys in creds.values() for k in keys):
        return creds
    clean: dict[str, dict[str, str]] = {}
    for service, keys in creds.items():
        kept: dict[str, str] = {}
        for key, value in keys.items():
            if is_forbidden_credential_key(key):
                logger.warning(
                    "Ignoring endpoint-shaped %s credential key %r on service %r — "
                    "URL/host/endpoint fields are server-config only",
                    source,
                    key,
                    service,
                )
                emit_credential_key_denied(service=service, key=key, source=source)
                continue
            kept[key] = value
        if kept:
            clean[service] = kept
    return clean


def emit_credential_key_denied(*, service: str, key: str, source: str) -> None:
    """Audit the rejection of an endpoint-shaped caller credential key.

    Emits ``network.access.denied`` so SIEM can alert on attempted SSRF via
    credential injection.  ``source`` is ``header``, ``oidc``, ``write`` or
    ``checkpoint``; ``http_path`` is the path of the triggering request (set
    by ``RequestContextMiddleware``; empty for background checkpoint resume).
    Key names only — values are never recorded.
    """
    from agentic_primitives_gateway.audit.emit import emit_audit_event
    from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType
    from agentic_primitives_gateway.context import get_request_path

    emit_audit_event(
        action=AuditAction.NETWORK_ACCESS_DENIED,
        outcome=AuditOutcome.DENY,
        resource_type=ResourceType.CREDENTIAL,
        resource_id=f"{service}:{key}",
        reason="blocked_cred_key",
        http_path=get_request_path() or None,
        metadata={"service": service, "key": key, "source": source},
    )
