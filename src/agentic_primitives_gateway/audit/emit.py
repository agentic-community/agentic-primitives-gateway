"""Emit helper for audit events.

Call sites use :func:`emit_audit_event` with the action-specific fields.
Request-scoped fields (``request_id``, ``correlation_id``, ``actor_*``) are
filled in from contextvars automatically, and ``metadata`` is passed
through :func:`redact_mapping` so no secrets reach the sinks.

The helper is a no-op when no router has been installed, so it is safe
to call from tests that have not wired the audit subsystem.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from agentic_primitives_gateway import metrics
from agentic_primitives_gateway.audit.models import AuditEvent, AuditOutcome, ResourceType
from agentic_primitives_gateway.audit.redaction import redact_mapping
from agentic_primitives_gateway.audit.router import AuditRouter
from agentic_primitives_gateway.context import (
    get_authenticated_principal,
    get_correlation_id,
    get_request_id,
)

logger = logging.getLogger(__name__)

# Module-level router singleton.  Set once during app startup by ``main.py``
# and cleared on shutdown.  Tests that need to observe events can install
# a custom router via :func:`set_audit_router`.
_router: AuditRouter | None = None

# Extra redaction keys from config (keycloak tenant-specific, etc.).
_extra_redact_keys: tuple[str, ...] = ()

# If True, hash principal IDs before emission (multi-tenant k8s safety).
_redact_principal_id: bool = False


def set_audit_router(router: AuditRouter | None) -> None:
    """Install the process-wide audit router (or clear it)."""
    global _router
    _router = router


def get_audit_router() -> AuditRouter | None:
    return _router


def configure_redaction(
    extra_redact_keys: tuple[str, ...] = (),
    redact_principal_id: bool = False,
) -> None:
    """Configure redaction behavior for :func:`emit_audit_event`."""
    global _extra_redact_keys, _redact_principal_id
    _extra_redact_keys = tuple(extra_redact_keys)
    _redact_principal_id = redact_principal_id


def _maybe_redact_principal(principal_id: str | None) -> str | None:
    if principal_id is None or not _redact_principal_id:
        return principal_id
    # Stable short hash — enough to correlate events for the same user
    # within a deployment without exposing the raw identifier.
    import hashlib

    digest = hashlib.sha256(principal_id.encode("utf-8")).hexdigest()
    return digest[:16]


def emit_audit_event(
    action: str,
    outcome: AuditOutcome | str,
    *,
    resource_type: ResourceType | str | None = None,
    resource_id: str | None = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    http_method: str | None = None,
    http_path: str | None = None,
    http_status: int | None = None,
    duration_ms: float | None = None,
    source_ip: str | None = None,
    user_agent: str | None = None,
    actor_id: str | None = None,
    actor_type: str | None = None,
    actor_groups: list[str] | None = None,
) -> None:
    """Build and dispatch an :class:`AuditEvent`.

    Only ``action`` and ``outcome`` are required.  Actor + correlation
    fields are pulled from contextvars when not supplied explicitly, so
    most call sites need only 2-3 kwargs.

    Emission is **synchronous but non-blocking**: each sink gets a
    ``put_nowait`` into its own queue and the worker drains asynchronously.
    If the process has no router installed, the call is a no-op so tests
    and library usage without the audit subsystem stay clean.
    """
    principal = get_authenticated_principal()

    final_actor_id = actor_id if actor_id is not None else (principal.id if principal else None)
    final_actor_id = _maybe_redact_principal(final_actor_id)
    final_actor_type = actor_type if actor_type is not None else (principal.type if principal else None)
    final_actor_groups = actor_groups if actor_groups is not None else (sorted(principal.groups) if principal else [])

    safe_metadata = redact_mapping(metadata, _extra_redact_keys) if metadata else {}

    outcome_value = AuditOutcome(outcome) if not isinstance(outcome, AuditOutcome) else outcome
    resource_type_value: ResourceType | None
    if resource_type is None or isinstance(resource_type, ResourceType):
        resource_type_value = resource_type
    else:
        resource_type_value = ResourceType(resource_type)

    event = AuditEvent(
        action=action,
        outcome=outcome_value,
        actor_id=final_actor_id,
        actor_type=final_actor_type,
        actor_groups=final_actor_groups,
        resource_type=resource_type_value,
        resource_id=resource_id,
        request_id=get_request_id() or None,
        correlation_id=get_correlation_id() or None,
        source_ip=source_ip,
        user_agent=user_agent,
        http_method=http_method,
        http_path=http_path,
        http_status=http_status,
        duration_ms=duration_ms,
        reason=reason,
        metadata=safe_metadata,
    )

    category = action.split(".", 1)[0] if "." in action else action
    metrics.AUDIT_EVENTS.labels(action_category=category, outcome=outcome_value.value).inc()

    if _router is None:
        # Audit subsystem not wired (tests, library usage).  Drop silently.
        return
    _router.emit(event)


# ── Mutation helper ──────────────────────────────────────────────────────


@dataclass
class AuditContext:
    """Mutable context a mutation-route hands to ``audit_mutation``.

    Handlers set ``resource_id`` / ``metadata`` *after* the operation
    returns, so the audit event can carry values the operation itself
    produced (a freshly-minted ``version_id``, for example).
    """

    resource_type: ResourceType | None = None
    resource_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@asynccontextmanager
async def audit_mutation(
    action: str,
    *,
    resource_type: ResourceType | None = None,
    resource_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AsyncIterator[AuditContext]:
    """Wrap a mutation so every exit path fires a consistent audit event.

    On clean return the context manager emits one ``<action>`` event with
    ``outcome=SUCCESS``.  On exception it emits the same action with
    ``outcome=FAILURE`` + ``metadata.error_type`` and re-raises — so
    failure audit coverage is automatic rather than per-handler.

    The yielded :class:`AuditContext` lets the handler refine
    ``resource_id`` / ``metadata`` after the operation completes:

    .. code:: python

        async with audit_mutation(
            AuditAction.MEMORY_RESOURCE_CREATE,
            resource_type=ResourceType.MEMORY,
        ) as audit:
            result = await registry.memory.create_memory_resource(name=...)
            audit.resource_id = result["memory_id"]
            audit.metadata = {"name": result["name"]}

    Every emitted event carries ``duration_ms``; success events use the
    elapsed wall time, failure events the time until the exception was
    raised.  ``http_*`` + actor fields come from contextvars just like
    :func:`emit_audit_event`.
    """
    ctx = AuditContext(
        resource_type=resource_type,
        resource_id=resource_id,
        metadata=dict(metadata or {}),
    )
    start = time.perf_counter()
    try:
        yield ctx
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 3)
        emit_audit_event(
            action=action,
            outcome=AuditOutcome.FAILURE,
            resource_type=ctx.resource_type,
            resource_id=ctx.resource_id,
            metadata={
                **ctx.metadata,
                "error_type": type(exc).__name__,
            },
            duration_ms=elapsed_ms,
        )
        raise
    elapsed_ms = round((time.perf_counter() - start) * 1000, 3)
    emit_audit_event(
        action=action,
        outcome=AuditOutcome.SUCCESS,
        resource_type=ctx.resource_type,
        resource_id=ctx.resource_id,
        metadata=ctx.metadata or None,
        duration_ms=elapsed_ms,
    )
