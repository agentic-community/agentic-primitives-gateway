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


def emit_credential_key_denied(*, service: str, key: str, source: str, http_path: str | None = None) -> None:
    """Audit the rejection of an endpoint-shaped caller credential key.

    Emits ``network.access.denied`` so SIEM can alert on attempted SSRF via
    credential injection.  ``source`` is ``header``, ``oidc`` or ``write``.
    Key names only — values are never recorded.
    """
    from agentic_primitives_gateway.audit.emit import emit_audit_event
    from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType

    emit_audit_event(
        action=AuditAction.NETWORK_ACCESS_DENIED,
        outcome=AuditOutcome.DENY,
        resource_type=ResourceType.CREDENTIAL,
        resource_id=f"{service}:{key}",
        reason="blocked_cred_key",
        http_path=http_path,
        metadata={"service": service, "key": key, "source": source},
    )
