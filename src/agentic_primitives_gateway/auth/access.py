"""Resource-level access control based on ownership and group sharing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException

from agentic_primitives_gateway import metrics
from agentic_primitives_gateway.audit.emit import emit_audit_event
from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal

if TYPE_CHECKING:
    from agentic_primitives_gateway.agents.store import AgentStore
    from agentic_primitives_gateway.agents.team_store import TeamStore


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


# ── Transitive access to shared memory pools ─────────────────────────
#
# Shared memory pools are a gateway-authored resource: the gateway
# decides the pool-name → backend-namespace mapping, and agent specs
# declare which pools they reference via ``PrimitiveConfig.shared_namespaces``.
# The REST surface at ``/api/v1/memory/{namespace}/*`` has no native
# per-pool ACL — without a check, any authenticated caller can read,
# write, or delete any unscoped namespace by guessing its name.
#
# The chosen ACL grain is "transitive through accessible agents / teams":
# a caller has access to pool ``P`` if they can access some agent whose
# ``primitives["memory"].shared_namespaces`` contains ``P``, or some team
# whose ``shared_memory_namespace == P``.  Admin bypasses all checks.
#
# Rationale: the tool surface already exposes the same read/write
# operations against these pools (``pool_memory_store`` / ``_retrieve`` /
# ``_search`` / ``_list``), so a caller with access to such an agent
# already has full read/write via the agent-run path.  Granting REST
# parity for those same callers keeps the two surfaces aligned and does
# not widen the blast radius.  Delete is REST-only; it is handled by a
# narrower check (:func:`require_pool_delete`) below.
#
# Orphan pools — pools that exist in the backend (e.g. created out of
# band or left behind after an agent was deleted) but are not declared
# in any spec visible to the caller — resolve to admin-only under this
# rule, which is the safer default.


async def has_transitive_pool_access(
    pool: str,
    *,
    principal: AuthenticatedPrincipal,
    agent_store: AgentStore,
    team_store: TeamStore,
) -> bool:
    """Whether ``principal`` can access shared pool ``pool``.

    Admin short-circuits to True.  Otherwise walks specs visible to the
    principal (via each store's ``list_for_user``) and returns True on
    the first match.  Pool-level only — does not filter events within
    the pool.
    """
    if principal.is_admin:
        return True
    for agent in await agent_store.list_for_user(principal):
        mem = agent.primitives.get("memory")
        if mem is not None and mem.shared_namespaces and pool in mem.shared_namespaces:
            return True
    return any(team.shared_memory_namespace == pool for team in await team_store.list_for_user(principal))


async def require_pool_access(
    pool: str,
    *,
    principal: AuthenticatedPrincipal,
    agent_store: AgentStore,
    team_store: TeamStore,
) -> None:
    """Raise 403 unless ``principal`` has transitive access to ``pool``.

    Read/write parity with the agent-tool surface.  For destructive
    operations (``DELETE``) use :func:`require_pool_delete` instead.
    """
    if await has_transitive_pool_access(pool, principal=principal, agent_store=agent_store, team_store=team_store):
        return
    _emit_denial("pool_not_transitively_accessible", resource_owner="", resource_type="memory_pool")
    raise HTTPException(status_code=403, detail="Forbidden")


async def require_pool_delete(
    pool: str,
    *,
    principal: AuthenticatedPrincipal,
    agent_store: AgentStore,
    team_store: TeamStore,
) -> None:
    """Raise 403 unless ``principal`` owns a spec that declares ``pool``.

    Narrower than :func:`require_pool_access` — destructive ops go
    through this.  Admin bypasses.  Otherwise the principal must *own*
    (not just have shared access to) an agent or team that declares
    the pool — sharing an agent grants read/write on its pool, but
    only the owner (or admin) can wipe a key.

    Known operational trade-off: memory records carry no ``written_by``
    provenance field.  A sharee can write keys that only the owner
    (or an admin) can later delete — if a pool accumulates garbage
    from a misbehaving sharee, cleanup is an owner action.  This is
    deliberate: the alternative ("delete your own writes") would
    require per-record attribution we don't currently emit, and a
    less-scoped rule ("any sharee can delete any key") would let a
    sharee wipe the owner's data, which is strictly worse.
    """
    if principal.is_admin:
        return
    for agent in await agent_store.list_for_user(principal):
        if agent.owner_id != principal.id:
            continue
        mem = agent.primitives.get("memory")
        if mem is not None and mem.shared_namespaces and pool in mem.shared_namespaces:
            return
    for team in await team_store.list_for_user(principal):
        if team.owner_id != principal.id:
            continue
        if team.shared_memory_namespace == pool:
            return
    _emit_denial("pool_delete_requires_owner", resource_owner="", resource_type="memory_pool")
    raise HTTPException(status_code=403, detail="Forbidden")
