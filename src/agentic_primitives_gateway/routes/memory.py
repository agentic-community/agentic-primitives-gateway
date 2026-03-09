from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response

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
from agentic_primitives_gateway.routes._helpers import handle_provider_errors

router = APIRouter(prefix="/api/v1/memory", tags=[Primitive.MEMORY])


# ── Namespace discovery ──────────────────────────────────────────────
# Must be registered before /{namespace} catch-all routes.


@router.get("/namespaces")
async def list_namespaces() -> Any:
    """List all known memory namespaces that contain stored memories."""
    namespaces = await registry.memory.list_namespaces()
    return {"namespaces": namespaces}


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
    try:
        return await registry.memory.create_event(
            actor_id=actor_id,
            session_id=session_id,
            messages=[(m.text, m.role) for m in request.messages],
            metadata=request.metadata,
        )
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="Conversation events not supported by this provider") from None


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
    return await registry.memory.get_event(
        actor_id=actor_id,
        session_id=session_id,
        event_id=event_id,
    )


@router.delete("/sessions/{actor_id}/{session_id}/events/{event_id}")
@handle_provider_errors("Conversation events not supported by this provider", not_found="Event not found")
async def delete_event(actor_id: str, session_id: str, event_id: str) -> Response:
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
    actors = await registry.memory.list_actors()
    return {"actors": actors}


@router.get("/actors/{actor_id}/sessions")
@handle_provider_errors("Session listing not supported by this provider")
async def list_sessions(actor_id: str) -> Any:
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
    branches = await registry.memory.list_branches(
        actor_id=actor_id,
        session_id=session_id,
    )
    return {"branches": branches}


# ── Control plane (memory resource lifecycle) ────────────────────────


@router.post("/resources", status_code=201)
async def create_memory_resource(request: CreateMemoryResourceRequest) -> Any:
    try:
        return await registry.memory.create_memory_resource(
            name=request.name,
            strategies=request.strategies,
            description=request.description,
        )
    except NotImplementedError:
        raise HTTPException(
            status_code=501, detail="Memory resource management not supported by this provider"
        ) from None


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
    try:
        return await registry.memory.add_strategy(
            memory_id=memory_id,
            strategy=request.strategy,
        )
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="Strategy management not supported by this provider") from None


@router.delete("/resources/{memory_id}/strategies/{strategy_id}")
@handle_provider_errors("Strategy management not supported by this provider")
async def delete_strategy(memory_id: str, strategy_id: str) -> Response:
    await registry.memory.delete_strategy(memory_id=memory_id, strategy_id=strategy_id)
    return Response(status_code=204)


# ── Key-value memory (original endpoints) ────────────────────────────
# These use /{namespace} catch-all paths, so they must be registered last.


@router.post("/{namespace}", response_model=MemoryRecord, status_code=201)
async def store_memory(namespace: str, request: StoreMemoryRequest) -> MemoryRecord:
    return await registry.memory.store(
        namespace=namespace,
        key=request.key,
        content=request.content,
        metadata=request.metadata,
    )


@router.get("/{namespace}/{key}", response_model=MemoryRecord)
async def retrieve_memory(namespace: str, key: str) -> MemoryRecord:
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
    records = await registry.memory.list_memories(namespace=namespace, limit=limit, offset=offset)
    return ListMemoryResponse(records=records, total=len(records))


@router.post("/{namespace}/search", response_model=SearchMemoryResponse)
async def search_memories(namespace: str, request: SearchMemoryRequest) -> SearchMemoryResponse:
    results = await registry.memory.search(
        namespace=namespace,
        query=request.query,
        top_k=request.top_k,
        filters=request.filters,
    )
    return SearchMemoryResponse(results=results)


@router.delete("/{namespace}/{key}")
async def delete_memory(namespace: str, key: str) -> Response:
    deleted = await registry.memory.delete(namespace=namespace, key=key)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    return Response(status_code=204)
