import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from starlette.responses import StreamingResponse

from agentic_primitives_gateway.agents.namespace import resolve_actor_id, resolve_knowledge_namespace_for_name
from agentic_primitives_gateway.agents.runner import AgentRunner
from agentic_primitives_gateway.agents.store import AgentStore
from agentic_primitives_gateway.agents.tools import _TOOL_CATALOG, build_tool_list
from agentic_primitives_gateway.auth.access import require_access, require_owner_or_admin
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import (
    get_authenticated_principal,
    get_provider_override,
    set_provider_overrides,
)
from agentic_primitives_gateway.models.agents import (
    AgentListResponse,
    AgentMemoryResponse,
    AgentSpec,
    AgentToolInfo,
    AgentToolsResponse,
    ChatRequest,
    ChatResponse,
    CreateAgentRequest,
    MemoryStoreInfo,
    SessionHistoryResponse,
    UpdateAgentRequest,
)
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway.routes._background import BackgroundRunManager, sse_response

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/agents", tags=["agents"])

_store: AgentStore | None = None
_runner = AgentRunner()
_bg = BackgroundRunManager(stale_seconds=600)
_active_runs = _bg.runs


def set_agent_bg(bg: BackgroundRunManager) -> None:
    """Replace the background run manager (called during app lifespan)."""
    global _bg, _active_runs
    _bg = bg
    _active_runs = bg.runs


def set_agent_store(store: AgentStore) -> None:
    """Set the module-level agent store (called during app lifespan)."""
    global _store
    _store = store
    _runner.set_store(store)


def _get_store() -> AgentStore:
    if _store is None:
        raise RuntimeError("Agent store not initialized")
    return _store


def _principal() -> AuthenticatedPrincipal:
    """Return the authenticated principal. Raises if not set."""
    principal = get_authenticated_principal()
    if principal is None:
        raise RuntimeError("No authenticated principal — auth middleware did not run")
    return principal


@router.post("", response_model=AgentSpec, status_code=201)
async def create_agent(request: CreateAgentRequest) -> AgentSpec:
    store = _get_store()
    principal = _principal()
    data = request.model_dump()
    data["owner_id"] = principal.id
    spec = AgentSpec(**data)
    existing = await store.get(spec.name)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Agent '{spec.name}' already exists")
    return await store.create(spec)


@router.get("", response_model=AgentListResponse)
async def list_agents() -> AgentListResponse:
    store = _get_store()
    principal = _principal()
    agents = await store.list_for_user(principal)
    return AgentListResponse(agents=agents)


@router.get("/tool-catalog")
async def get_tool_catalog() -> dict:
    """Return all available primitives and their tools for the agent builder UI."""
    catalog: dict[str, list[dict[str, str]]] = {}
    for primitive_name, tools in _TOOL_CATALOG.items():
        catalog[primitive_name] = [{"name": t.name, "description": t.description} for t in tools]
    # Add agents as a special primitive (tools are dynamic, so no static list)
    catalog["agents"] = []
    return {"primitives": catalog}


@router.get("/{name}", response_model=AgentSpec)
async def get_agent(name: str) -> AgentSpec:
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    require_access(_principal(), spec.owner_id, spec.shared_with)
    return spec


@router.put("/{name}", response_model=AgentSpec)
async def update_agent(name: str, request: UpdateAgentRequest) -> AgentSpec:
    store = _get_store()
    existing = await store.get(name)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    require_owner_or_admin(_principal(), existing.owner_id)
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    return await store.update(name, updates)


@router.delete("/{name}")
async def delete_agent(name: str) -> dict[str, str]:
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    require_owner_or_admin(_principal(), spec.owner_id)
    await store.delete(name)
    return {"status": "deleted"}


@router.get("/{name}/tools", response_model=AgentToolsResponse)
async def get_agent_tools(name: str) -> AgentToolsResponse:
    """List the tools available to an agent and which providers back them."""
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    require_access(_principal(), spec.owner_id, spec.shared_with)

    # Apply agent-level provider overrides so we resolve the correct providers
    if spec.provider_overrides:
        set_provider_overrides(spec.provider_overrides)

    # Build the tool list (same as the runner does)
    tools = build_tool_list(
        spec.primitives,
        namespace="__introspect__",
        agent_store=_store,
        agent_runner=_runner,
    )

    # Resolve the active provider name for each primitive
    tool_infos: list[AgentToolInfo] = []
    for tool in tools:
        # Determine which provider is active for this primitive
        if tool.primitive == "agents":
            provider_name = "agent_delegation"
        else:
            try:
                prim_providers = registry.get_primitive(tool.primitive)
                override = get_provider_override(tool.primitive)
                provider_name = override or prim_providers.default_name
            except Exception:
                provider_name = "unknown"

        tool_infos.append(
            AgentToolInfo(
                name=tool.name,
                description=tool.description,
                primitive=tool.primitive,
                provider=provider_name,
            )
        )

    return AgentToolsResponse(agent_name=name, tools=tool_infos)


@router.get("/{name}/memory", response_model=AgentMemoryResponse)
async def get_agent_memory(name: str, session_id: str | None = None) -> AgentMemoryResponse:
    """Introspect memory stores available to an agent."""
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    require_access(_principal(), spec.owner_id, spec.shared_with)

    # Apply agent-level provider overrides so we read from the same provider the agent writes to
    if spec.provider_overrides:
        set_provider_overrides(spec.provider_overrides)

    mem_config = spec.primitives.get("memory")
    if not mem_config or not mem_config.enabled:
        return AgentMemoryResponse(
            agent_name=name,
            memory_enabled=False,
            namespace="",
            stores=[],
        )

    # Resolve knowledge namespace (user-scoped — same as runner)
    namespace = resolve_knowledge_namespace_for_name(name, mem_config.namespace, _principal())

    # Get all known namespaces from the provider
    stores: list[MemoryStoreInfo] = []
    try:
        all_namespaces = await registry.memory.list_namespaces()
        logger.info(
            "Agent[%s] memory introspection: resolved_namespace=%s, all_namespaces=%s",
            name,
            namespace,
            all_namespaces,
        )
        # Filter to namespaces belonging to this agent.
        # Use "namespace:" prefix to avoid matching other agents whose names
        # share a prefix (e.g. "agent:bot" must not match "agent:bot-2").
        child_prefix = namespace + ":"
        for ns_name in all_namespaces:
            if ns_name == namespace or ns_name.startswith(child_prefix):
                records = await registry.memory.list_memories(namespace=ns_name, limit=50)
                stores.append(
                    MemoryStoreInfo(
                        namespace=ns_name,
                        memory_count=len(records),
                        memories=[
                            {"key": r.key, "content": r.content[:200], "updated_at": r.updated_at.isoformat()}
                            for r in records
                        ],
                    )
                )

        # If the resolved namespace wasn't found via list_namespaces, still try to list its contents
        if not any(s.namespace == namespace for s in stores):
            records = await registry.memory.list_memories(namespace=namespace, limit=50)
            logger.info(
                "Agent[%s] fallback list_memories for namespace=%s returned %d records",
                name,
                namespace,
                len(records),
            )
            if records:
                stores.insert(
                    0,
                    MemoryStoreInfo(
                        namespace=namespace,
                        memory_count=len(records),
                        memories=[
                            {"key": r.key, "content": r.content[:200], "updated_at": r.updated_at.isoformat()}
                            for r in records
                        ],
                    ),
                )
    except Exception:
        logger.exception("Failed to introspect memory for agent %s", name)

    return AgentMemoryResponse(
        agent_name=name,
        memory_enabled=True,
        namespace=namespace,
        stores=stores,
    )


@router.post("/{name}/chat", response_model=ChatResponse)
async def chat_with_agent(name: str, request: ChatRequest) -> ChatResponse:
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    require_access(_principal(), spec.owner_id, spec.shared_with)

    # Apply agent-level provider overrides to the current request context
    if spec.provider_overrides:
        set_provider_overrides(spec.provider_overrides)

    return await _runner.run(
        spec=spec,
        message=request.message,
        session_id=request.session_id,
    )


@router.post("/{name}/chat/stream")
async def chat_with_agent_stream(name: str, request: ChatRequest) -> StreamingResponse:
    """Streaming variant of chat. Returns SSE events.

    The agent run executes in a background task so that ``_finalize`` (which
    stores the conversation turn) completes even if the client disconnects
    mid-stream.
    """
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    require_access(_principal(), spec.owner_id, spec.shared_with)

    if spec.provider_overrides:
        set_provider_overrides(spec.provider_overrides)

    session_id = request.session_id or ""
    queue, _ = _bg.start(
        session_id,
        _runner.run_stream(spec=spec, message=request.message, session_id=request.session_id),
        owner_id=_principal().id,
    )
    return sse_response(queue, strip_fields=frozenset({"full_result", "tool_input"}))


@router.get("/{name}/sessions")
async def list_sessions(name: str) -> dict:
    """List all conversation sessions for an agent."""
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    require_access(_principal(), spec.owner_id, spec.shared_with)

    if spec.provider_overrides:
        set_provider_overrides(spec.provider_overrides)

    actor_id = resolve_actor_id(name, _principal())
    sessions: list[dict[str, Any]] = []
    try:
        sessions = await registry.memory.list_sessions(actor_id)
    except (NotImplementedError, Exception):
        logger.debug("Failed to list sessions for %s", name)

    return {"agent_name": name, "sessions": sessions}


@router.delete("/{name}/sessions/{session_id}")
async def delete_session(name: str, session_id: str) -> dict:
    """Delete a session's conversation history."""
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    require_access(_principal(), spec.owner_id, spec.shared_with)

    if spec.provider_overrides:
        set_provider_overrides(spec.provider_overrides)

    actor_id = resolve_actor_id(name, _principal())
    try:
        await registry.memory.delete_session(actor_id=actor_id, session_id=session_id)
    except (NotImplementedError, Exception):
        logger.debug("Failed to delete session %s/%s", name, session_id)

    return {"status": "deleted"}


@router.get("/{name}/sessions/{session_id}/status")
async def get_session_status(name: str, session_id: str) -> dict:
    """Check if a run is currently active for this session."""
    _bg.cleanup()
    return {"status": await _bg.get_status_async(session_id)}


@router.delete("/{name}/sessions/{session_id}/run")
async def cancel_session_run(name: str, session_id: str) -> dict:
    """Cancel an active agent run for this session."""
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    require_access(_principal(), spec.owner_id, spec.shared_with)

    # Verify run ownership via event store
    owner = await _bg.get_owner_async(session_id)
    if owner and owner != _principal().id and not _principal().is_admin:
        raise HTTPException(status_code=403, detail="Forbidden")

    cancelled = await _bg.cancel(session_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="No active run found for this session")
    return {"status": "cancelled"}


@router.get("/{name}/sessions/{session_id}", response_model=SessionHistoryResponse)
async def get_session_history(name: str, session_id: str) -> SessionHistoryResponse:
    """Retrieve conversation history for a specific agent session."""
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    require_access(_principal(), spec.owner_id, spec.shared_with)

    if spec.provider_overrides:
        set_provider_overrides(spec.provider_overrides)

    actor_id = resolve_actor_id(name, _principal())
    messages: list[dict[str, str]] = []
    try:
        turns = await registry.memory.get_last_turns(actor_id=actor_id, session_id=session_id, k=50)
        for turn in turns:
            for msg in turn:
                messages.append({"role": msg.get("role", "user"), "content": msg.get("text", "")})
    except (NotImplementedError, Exception):
        logger.debug("Failed to load session history for %s/%s", name, session_id)

    return SessionHistoryResponse(
        agent_name=name,
        session_id=session_id,
        messages=messages,
    )
