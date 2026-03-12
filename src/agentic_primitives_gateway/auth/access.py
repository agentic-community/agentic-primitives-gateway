"""Resource-level access control based on ownership and group sharing."""

from __future__ import annotations

from fastapi import HTTPException

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


def require_access(
    principal: AuthenticatedPrincipal | None,
    resource_owner: str,
    resource_shared_with: list[str],
) -> AuthenticatedPrincipal:
    """Raise 403 if the principal cannot access the resource.

    Returns the principal for convenience.
    """
    if principal is None:
        raise HTTPException(status_code=403, detail="Forbidden")
    if not check_access(principal, resource_owner, resource_shared_with):
        raise HTTPException(status_code=403, detail="Forbidden")
    return principal


def require_owner_or_admin(
    principal: AuthenticatedPrincipal | None,
    resource_owner: str,
) -> AuthenticatedPrincipal:
    """Raise 403 if the principal cannot modify the resource.

    Returns the principal for convenience.
    """
    if principal is None:
        raise HTTPException(status_code=403, detail="Forbidden")
    if not check_owner_or_admin(principal, resource_owner):
        raise HTTPException(status_code=403, detail="Forbidden")
    return principal
