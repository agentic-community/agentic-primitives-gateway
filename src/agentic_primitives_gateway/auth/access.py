"""Resource-level access control based on ownership and group sharing."""

from __future__ import annotations

from fastapi import HTTPException

from agentic_primitives_gateway import metrics
from agentic_primitives_gateway.audit.emit import emit_audit_event
from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal


def check_access(
    principal: AuthenticatedPrincipal,
    resource_owner: str,
    resource_shared_with: list[str],
) -> bool:
    """Check if a principal can access a resource.

    Access is granted if any of:
    - The principal has the ``admin`` scope.
    - The principal owns the resource.
    - The resource is shared with ``"*"`` (all authenticated users).
    - The principal belongs to a group in ``resource_shared_with``.
    """
    if principal.is_admin:
        return True
    if principal.id == resource_owner:
        return True
    if "*" in resource_shared_with:
        return True
    return bool(principal.groups & set(resource_shared_with))


def check_owner_or_admin(
    principal: AuthenticatedPrincipal,
    resource_owner: str,
) -> bool:
    """Check if a principal can modify (edit/delete) a resource.

    Only the owner or an admin can modify.
    """
    if principal.is_admin:
        return True
    return principal.id == resource_owner


def _emit_denial(reason: str, resource_owner: str, resource_type: str) -> None:
    """Record a resource-level access denial.

    Called from ``require_access`` / ``require_owner_or_admin`` on the raise
    path.  ``resource_type`` is free-form (the caller doesn't know the
    resource shape) — we map it to the ``ResourceType`` enum when possible
    and fall back to recording the literal on ``metadata``.
    """
    rt: ResourceType | None
    try:
        rt = ResourceType(resource_type)
    except ValueError:
        rt = None
    emit_audit_event(
        action=AuditAction.RESOURCE_ACCESS_DENIED,
        outcome=AuditOutcome.DENY,
        resource_type=rt,
        reason=reason,
        metadata={"resource_owner": resource_owner, "resource_type_hint": resource_type},
    )
    metrics.ACCESS_DENIALS.labels(resource_type=resource_type).inc()


def require_access(
    principal: AuthenticatedPrincipal | None,
    resource_owner: str,
    resource_shared_with: list[str],
    *,
    resource_type: str = "unknown",
) -> AuthenticatedPrincipal:
    """Raise 403 if the principal cannot access the resource.

    Returns the principal for convenience.
    """
    if principal is None:
        _emit_denial("no_principal", resource_owner, resource_type)
        raise HTTPException(status_code=403, detail="Forbidden")
    if not check_access(principal, resource_owner, resource_shared_with):
        _emit_denial("not_shared", resource_owner, resource_type)
        raise HTTPException(status_code=403, detail="Forbidden")
    return principal


def require_owner_or_admin(
    principal: AuthenticatedPrincipal | None,
    resource_owner: str,
    *,
    resource_type: str = "unknown",
) -> AuthenticatedPrincipal:
    """Raise 403 if the principal cannot modify the resource.

    Returns the principal for convenience.
    """
    if principal is None:
        _emit_denial("no_principal", resource_owner, resource_type)
        raise HTTPException(status_code=403, detail="Forbidden")
    if not check_owner_or_admin(principal, resource_owner):
        _emit_denial("not_owner", resource_owner, resource_type)
        raise HTTPException(status_code=403, detail="Forbidden")
    return principal
