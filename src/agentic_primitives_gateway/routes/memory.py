from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from agentic_primitives_gateway.audit.emit import audit_mutation
from agentic_primitives_gateway.audit.models import AuditAction, ResourceType
from agentic_primitives_gateway.auth.access import require_pool_access, require_pool_delete
from agentic_primitives_gateway.models.enums import Primitive
from agentic_primitives_gateway.models.memory import (
    AddStrategyRequest,
    CreateEventRequest,
    CreateMemoryResourceRequest,
    EventInfo,
    EventMessage,
    ForkConversationRequest,
    GetTurnsResponse,
    ListEventsResponse,
    ListMemoryResponse,
    MemoryRecord,
    SearchMemoryRequest,
    SearchMemoryResponse,
    StoreMemoryRequest,
    TurnGroup,
)
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway.routes._helpers import (
    handle_provider_errors,
    require_principal,
    require_user_scoped,
)

router = APIRouter(
    prefix="/api/v1/memory",
    tags=[Primitive.MEMORY],
    dependencies=[Depends(require_principal)],
)


_USER_SCOPE_MARKER = ":u:"


def _check_actor(actor_id: str) -> None:
    """Validate the caller owns this actor_id."""
    require_user_scoped(actor_id, require_principal())


def _check_namespace(namespace: str) -> None:
    """User-scope check for ``:u:{id}`` namespaces.

    This only covers private per-user namespaces.  Unscoped (shared)
    namespaces skip this and must be additionally guarded by
    :func:`_check_pool_access` on each pool route.
    """
    require_user_scoped(namespace, require_principal())


async def _check_pool_access(namespace: str, *, for_delete: bool = False) -> None:
    """Gate unscoped shared-pool access via transitive-through-agents ACL.

    User-scoped namespaces (``…:u:{id}``) are handled upstream by
    :func:`_check_namespace` — we only need the transitive check on
    unscoped names, which is where the REST bypass previously lived.
    ``for_delete`` switches to the stricter owner-only rule.

    Admins short-circuit *before* we touch the agent/team stores so
    unit tests that exercise the routes without initializing the
    stores still pass — the noop auth backend treats tests as admin.
    Non-admin callers require the stores to be initialized; if they
    aren't, we fail closed with a 503 rather than let access slip
    through.
    """
    if _USER_SCOPE_MARKER in namespace:
        return
    principal = require_principal()
    if principal.is_admin:
        return
    from agentic_primitives_gateway.routes.agents import _get_store as _get_agent_store
    from agentic_primitives_gateway.routes.teams import _get_store as _get_team_store

    try:
        agent_store = _get_agent_store()
        team_store = _get_team_store()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="Pool access control unavailable: agent/team stores not initialized",
        ) from exc

    check = require_pool_delete if for_delete else require_pool_access
    await check(
        namespace,
        principal=principal,
        agent_store=agent_store,
        team_store=team_store,
    )


# ── Namespace discovery ──────────────────────────────────────────────
# Must be registered before /{namespace} catch-all routes.


@router.get("/namespaces")
async def list_namespaces() -> Any:
    """List memory namespaces visible to the current user.

    Non-admin callers only see namespaces scoped to their own user ID.
    """
    principal = require_principal()
    namespaces = await registry.memory.list_namespaces()
    if principal.is_admin:
        return {"namespaces": namespaces}
    # Filter to namespaces belonging to this user
    filtered = [ns for ns in namespaces if f":u:{principal.id}" in ns]
    return {"namespaces": filtered}


# ── Conversation events ─────────────────────────────────────────────
# These must be registered before /{namespace} catch-all routes.


@router.post(
    "/sessions/{actor_id}/{session_id}/events",
    response_model=EventInfo,
    status_code=201,
)
async def create_event(
    actor_id: str,
    session_id: str,
    request: CreateEventRequest,
) -> Any:
    _check_actor(actor_id)
    async with audit_mutation(
        AuditAction.MEMORY_EVENT_APPEND,
        resource_type=ResourceType.MEMORY,
        resource_id=f"{actor_id}/{session_id}",
        metadata={"message_count": len(request.messages)},
    ) as audit:
        try:
            result = await registry.memory.create_event(
                actor_id=actor_id,
                session_id=session_id,
                messages=[(m.text, m.role) for m in request.messages],
                metadata=request.metadata,
            )
        except NotImplementedError:
            raise HTTPException(status_code=501, detail="Conversation events not supported by this provider") from None
        if isinstance(result, dict) and "event_id" in result:
            audit.metadata["event_id"] = result["event_id"]
        return result


@router.get(
    "/sessions/{actor_id}/{session_id}/events",
    response_model=ListEventsResponse,
)
@handle_provider_errors("Conversation events not supported by this provider")
async def list_events(
    actor_id: str,
    session_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
) -> Any:
    _check_actor(actor_id)
    events = await registry.memory.list_events(
        actor_id=actor_id,
        session_id=session_id,
        limit=limit,
    )
    return ListEventsResponse(events=[EventInfo(**e) for e in events])


@router.get(
    "/sessions/{actor_id}/{session_id}/events/{event_id}",
    response_model=EventInfo,
)
@handle_provider_errors("Conversation events not supported by this provider", not_found="Event not found")
async def get_event(actor_id: str, session_id: str, event_id: str) -> Any:
    _check_actor(actor_id)
    return await registry.memory.get_event(
        actor_id=actor_id,
        session_id=session_id,
        event_id=event_id,
    )


@router.delete("/sessions/{actor_id}/{session_id}/events/{event_id}")
@handle_provider_errors("Conversation events not supported by this provider", not_found="Event not found")
async def delete_event(actor_id: str, session_id: str, event_id: str) -> Response:
    _check_actor(actor_id)
    async with audit_mutation(
        AuditAction.MEMORY_EVENT_DELETE,
        resource_type=ResourceType.MEMORY,
        resource_id=f"{actor_id}/{session_id}/{event_id}",
    ):
        await registry.memory.delete_event(
            actor_id=actor_id,
            session_id=session_id,
            event_id=event_id,
        )
    return Response(status_code=204)


@router.get(
    "/sessions/{actor_id}/{session_id}/turns",
    response_model=GetTurnsResponse,
)
@handle_provider_errors("Conversation turns not supported by this provider")
async def get_last_turns(
    actor_id: str,
    session_id: str,
    k: int = Query(default=5, ge=1, le=100),
) -> Any:
    _check_actor(actor_id)
    turns = await registry.memory.get_last_turns(
        actor_id=actor_id,
        session_id=session_id,
        k=k,
    )
    return GetTurnsResponse(
        turns=[TurnGroup(messages=[EventMessage(text=m["text"], role=m["role"]) for m in turn]) for turn in turns]
    )


# ── Session management ──────────────────────────────────────────────


@router.get("/actors")
@handle_provider_errors("Actor listing not supported by this provider")
async def list_actors() -> Any:
    """List actors visible to the current user.

    Non-admin callers only see their own actor IDs.
    """
    principal = require_principal()
    actors = await registry.memory.list_actors()
    if principal.is_admin:
        return {"actors": actors}
    filtered = [a for a in actors if f":u:{principal.id}" in a]
    return {"actors": filtered}


@router.get("/actors/{actor_id}/sessions")
@handle_provider_errors("Session listing not supported by this provider")
async def list_sessions(actor_id: str) -> Any:
    _check_actor(actor_id)
    sessions = await registry.memory.list_sessions(actor_id=actor_id)
    return {"sessions": sessions}


# ── Branch management ───────────────────────────────────────────────


@router.post(
    "/sessions/{actor_id}/{session_id}/branches",
    status_code=201,
)
async def fork_conversation(
    actor_id: str,
    session_id: str,
    request: ForkConversationRequest,
) -> Any:
    _check_actor(actor_id)
    async with audit_mutation(
        AuditAction.MEMORY_BRANCH_CREATE,
        resource_type=ResourceType.MEMORY,
        resource_id=f"{actor_id}/{session_id}",
        metadata={
            "branch_name": request.branch_name,
            "root_event_id": request.root_event_id,
        },
    ):
        try:
            return await registry.memory.fork_conversation(
                actor_id=actor_id,
                session_id=session_id,
                root_event_id=request.root_event_id,
                branch_name=request.branch_name,
                messages=[(m.text, m.role) for m in request.messages],
            )
        except NotImplementedError:
            raise HTTPException(status_code=501, detail="Branch management not supported by this provider") from None


@router.get("/sessions/{actor_id}/{session_id}/branches")
@handle_provider_errors("Branch management not supported by this provider")
async def list_branches(actor_id: str, session_id: str) -> Any:
    _check_actor(actor_id)
    branches = await registry.memory.list_branches(
        actor_id=actor_id,
        session_id=session_id,
    )
    return {"branches": branches}


# ── Control plane (memory resource lifecycle) ────────────────────────


@router.post("/resources", status_code=201)
async def create_memory_resource(request: CreateMemoryResourceRequest) -> Any:
    async with audit_mutation(
        AuditAction.MEMORY_RESOURCE_CREATE,
        resource_type=ResourceType.MEMORY,
        metadata={"name": request.name, "strategy_count": len(request.strategies or [])},
    ) as audit:
        try:
            result = await registry.memory.create_memory_resource(
                name=request.name,
                strategies=request.strategies,
                description=request.description,
            )
        except NotImplementedError:
            raise HTTPException(
                status_code=501, detail="Memory resource management not supported by this provider"
            ) from None
        if isinstance(result, dict) and "memory_id" in result:
            audit.resource_id = result["memory_id"]
        return result


@router.get("/resources")
@handle_provider_errors("Memory resource management not supported by this provider")
async def list_memory_resources() -> Any:
    resources = await registry.memory.list_memory_resources()
    return {"resources": resources}


@router.get("/resources/{memory_id}")
@handle_provider_errors("Memory resource management not supported by this provider")
async def get_memory_resource(memory_id: str) -> Any:
    return await registry.memory.get_memory_resource(memory_id=memory_id)


@router.delete("/resources/{memory_id}")
@handle_provider_errors("Memory resource management not supported by this provider")
async def delete_memory_resource(memory_id: str) -> Response:
    async with audit_mutation(
        AuditAction.MEMORY_RESOURCE_DELETE,
        resource_type=ResourceType.MEMORY,
        resource_id=memory_id,
    ):
        await registry.memory.delete_memory_resource(memory_id=memory_id)
    return Response(status_code=204)


# ── Strategy management ─────────────────────────────────────────────


@router.get("/resources/{memory_id}/strategies")
@handle_provider_errors("Strategy management not supported by this provider")
async def list_strategies(memory_id: str) -> Any:
    strategies = await registry.memory.list_strategies(memory_id=memory_id)
    return {"strategies": strategies}


@router.post("/resources/{memory_id}/strategies", status_code=201)
async def add_strategy(memory_id: str, request: AddStrategyRequest) -> Any:
    async with audit_mutation(
        AuditAction.MEMORY_STRATEGY_CREATE,
        resource_type=ResourceType.MEMORY,
        resource_id=memory_id,
    ) as audit:
        try:
            result = await registry.memory.add_strategy(
                memory_id=memory_id,
                strategy=request.strategy,
            )
        except NotImplementedError:
            raise HTTPException(status_code=501, detail="Strategy management not supported by this provider") from None
        if isinstance(result, dict) and "strategy_id" in result:
            audit.metadata["strategy_id"] = result["strategy_id"]
        return result


@router.delete("/resources/{memory_id}/strategies/{strategy_id}")
@handle_provider_errors("Strategy management not supported by this provider")
async def delete_strategy(memory_id: str, strategy_id: str) -> Response:
    async with audit_mutation(
        AuditAction.MEMORY_STRATEGY_DELETE,
        resource_type=ResourceType.MEMORY,
        resource_id=memory_id,
        metadata={"strategy_id": strategy_id},
    ):
        await registry.memory.delete_strategy(memory_id=memory_id, strategy_id=strategy_id)
    return Response(status_code=204)


# ── Key-value memory (original endpoints) ────────────────────────────
# These use /{namespace} catch-all paths, so they must be registered last.


@router.post("/{namespace}", response_model=MemoryRecord, status_code=201)
async def store_memory(namespace: str, request: StoreMemoryRequest) -> MemoryRecord:
    _check_namespace(namespace)
    await _check_pool_access(namespace)
    async with audit_mutation(
        AuditAction.MEMORY_RECORD_WRITE,
        resource_type=ResourceType.MEMORY,
        resource_id=f"{namespace}/{request.key}",
    ):
        return await registry.memory.store(
            namespace=namespace,
            key=request.key,
            content=request.content,
            metadata=request.metadata,
        )


@router.get("/{namespace}/{key}", response_model=MemoryRecord)
async def retrieve_memory(namespace: str, key: str) -> MemoryRecord:
    _check_namespace(namespace)
    await _check_pool_access(namespace)
    record = await registry.memory.retrieve(namespace=namespace, key=key)
    if record is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return record


@router.get("/{namespace}", response_model=ListMemoryResponse)
async def list_memories(
    namespace: str,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> ListMemoryResponse:
    _check_namespace(namespace)
    await _check_pool_access(namespace)
    records = await registry.memory.list_memories(namespace=namespace, limit=limit, offset=offset)
    return ListMemoryResponse(records=records, total=len(records))


@router.post("/{namespace}/search", response_model=SearchMemoryResponse)
async def search_memories(namespace: str, request: SearchMemoryRequest) -> SearchMemoryResponse:
    _check_namespace(namespace)
    await _check_pool_access(namespace)
    results = await registry.memory.search(
        namespace=namespace,
        query=request.query,
        top_k=request.top_k,
        filters=request.filters,
    )
    return SearchMemoryResponse(results=results)


@router.delete("/{namespace}/{key}")
async def delete_memory(namespace: str, key: str) -> Response:
    _check_namespace(namespace)
    await _check_pool_access(namespace, for_delete=True)
    async with audit_mutation(
        AuditAction.MEMORY_RECORD_DELETE,
        resource_type=ResourceType.MEMORY,
        resource_id=f"{namespace}/{key}",
    ):
        deleted = await registry.memory.delete(namespace=namespace, key=key)
        if not deleted:
            raise HTTPException(status_code=404, detail="Memory not found")
    return Response(status_code=204)
