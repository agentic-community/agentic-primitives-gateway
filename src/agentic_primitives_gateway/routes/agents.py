import contextlib
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from starlette.responses import Response, StreamingResponse

from agentic_primitives_gateway import metrics
from agentic_primitives_gateway.agents.namespace import (
    resolve_actor_id,
    resolve_memory_namespace_for_identity,
)
from agentic_primitives_gateway.agents.runner import AgentRunner
from agentic_primitives_gateway.agents.store import AgentStore
from agentic_primitives_gateway.agents.tools import _TOOL_CATALOG, build_tool_list
from agentic_primitives_gateway.audit.emit import emit_audit_event
from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType
from agentic_primitives_gateway.auth.access import (
    ProviderOverrideSource,
    apply_filtered_provider_overrides,
    require_owner_or_admin,
    validate_agent_pool_references,
)
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import (
    get_provider_override,
)
from agentic_primitives_gateway.models.agents import (
    AgentLineage,
    AgentListResponse,
    AgentMemoryResponse,
    AgentSpec,
    AgentToolInfo,
    AgentToolsResponse,
    AgentVersion,
    AgentVersionListResponse,
    ChatRequest,
    ChatResponse,
    CreateAgentRequest,
    CreateVersionRequest,
    ForkRequest,
    MemoryStoreInfo,
    RejectionRequest,
    SessionHistoryResponse,
    UpdateAgentRequest,
)
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway.routes._background import (
    BackgroundRunManager,
    reconnect_event_generator,
    run_event_key,
    sse_response,
)
from agentic_primitives_gateway.routes._helpers import (
    require_admin,
    require_principal,
    resolve_agent_spec,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


async def _validate_agent_pools(spec: AgentSpec, principal: AuthenticatedPrincipal, store: AgentStore) -> None:
    mem = spec.primitives.get("memory")
    if not mem or not mem.shared_namespaces:
        return
    from agentic_primitives_gateway.routes.teams import _get_store as _get_team_store

    await validate_agent_pool_references(spec, principal, store, _get_team_store())


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


def _session_run_key(spec: AgentSpec, session_id: str) -> str:
    return run_event_key("agent", require_principal().id, spec.owner_id, spec.name, session_id)


async def _require_session_run_owner(spec: AgentSpec, session_id: str) -> str:
    key = _session_run_key(spec, session_id)
    owner = await _bg.get_owner_async(key)
    principal = require_principal()
    if owner is None or (owner != principal.id and not principal.is_admin):
        raise HTTPException(status_code=403, detail="Forbidden")
    return key


def _ns_kind(owner_id: str) -> str:
    """Collapse an owner_id down to the bounded label we emit on metrics.

    Metrics can't carry per-user labels or they'd explode in cardinality;
    the ``ns_kind`` label distinguishes ``system`` (YAML-seeded identities)
    from ``user`` (anything a caller created or forked).
    """
    return "system" if owner_id == "system" else "user"


@router.post("", response_model=AgentSpec, status_code=201)
async def create_agent(request: CreateAgentRequest) -> AgentSpec:
    """Create a new agent identity in the caller's namespace.

    The agent is created as version 1.  When the admin-approval gate is
    OFF, version 1 is auto-deployed and this endpoint returns the
    deployed ``AgentSpec``.  When the gate is ON, version 1 is saved as
    ``draft`` and the caller must take it through the
    propose→approve→deploy flow.
    """
    store = _get_store()
    principal = require_principal()
    if principal.id == "system" and not principal.is_admin:
        raise HTTPException(status_code=403, detail="System namespace requires admin access")
    data = request.model_dump()
    data["owner_id"] = principal.id
    spec = AgentSpec(**data)
    await _validate_agent_pools(spec, principal, store)

    existing = await store.get_deployed(spec.name, principal.id)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Agent '{spec.name}' already exists")
    version = await store.create_version(
        name=spec.name,
        owner_id=principal.id,
        spec=spec,
        created_by=principal.id,
        commit_message="initial version",
        auto_deploy=True,
    )
    emit_audit_event(
        action=AuditAction.AGENT_CREATE,
        outcome=AuditOutcome.SUCCESS,
        resource_type=ResourceType.AGENT,
        resource_id=f"{principal.id}:{spec.name}",
        metadata={"model": spec.model, "version_id": version.version_id},
    )
    metrics.AGENT_VERSIONS_CREATED.labels(
        ns_kind=_ns_kind(principal.id),
        auto_deployed=str(version.status.value == "deployed").lower(),
    ).inc()
    return version.spec


@router.get("", response_model=AgentListResponse)
async def list_agents() -> AgentListResponse:
    store = _get_store()
    principal = require_principal()
    agents = await store.list_for_user(principal)
    return AgentListResponse(agents=agents)


@router.get("/tool-catalog")
async def get_tool_catalog() -> dict:
    """Return all available primitives and their tools.

    Each tool includes its name, description, and input_schema (JSON Schema)
    so clients can dynamically build framework-specific tool wrappers.
    """
    catalog: dict[str, list[dict]] = {}
    for primitive_name, tools in _TOOL_CATALOG.items():
        catalog[primitive_name] = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools
        ]
    # Add agents as a special primitive (tools are dynamic, so no static list)
    catalog["agents"] = []
    return {"primitives": catalog}


@router.get("/{name}/export")
async def export_agent(name: str, owner: str | None = None) -> Response:
    """Export an agent spec as a standalone Python script.

    The generated script uses ``agentic-primitives-gateway-client`` for
    primitive calls and raw boto3 Bedrock ``converse()`` for the LLM loop.
    """
    from agentic_primitives_gateway.agents.export import export_agent as _export

    store = _get_store()
    principal = require_principal()
    spec: AgentSpec = await resolve_agent_spec(store, name, principal, owner_query=owner)

    # Load sub-agent specs for delegation export.  Sub-refs resolve in the
    # agent's owner namespace first (matching the run-time resolver in
    # ``agents/tools/delegation.py``).
    all_specs: dict[str, Any] = {}
    agents_cfg = spec.primitives.get("agents")
    if agents_cfg and agents_cfg.enabled and agents_cfg.tools:
        from agentic_primitives_gateway.auth.access import check_access

        for ref in agents_cfg.tools:
            if ":" in ref:
                owner_id, _, bare = ref.partition(":")
                sa_spec = await store.resolve_qualified(owner_id, bare)
            else:
                sa_spec = await store.resolve_qualified(spec.owner_id, ref)
                if sa_spec is None:
                    sa_spec = await store.resolve_qualified("system", ref)
            if sa_spec and check_access(principal, sa_spec.owner_id, sa_spec.shared_with):
                all_specs[ref] = sa_spec

    code = _export(spec, all_specs=all_specs if all_specs else None)
    return Response(
        content=code,
        media_type="text/x-python",
        headers={"Content-Disposition": f'attachment; filename="{name}.py"'},
    )


@router.get("/{name}", response_model=AgentSpec)
async def get_agent(name: str, owner: str | None = None) -> AgentSpec:
    store = _get_store()
    spec: AgentSpec = await resolve_agent_spec(store, name, require_principal(), owner_query=owner)
    return spec


@router.put("/{name}", response_model=AgentSpec)
async def update_agent(name: str, request: UpdateAgentRequest, owner: str | None = None) -> AgentSpec:
    """Create + auto-deploy a new version of an agent.

    When ``governance.require_admin_approval_for_deploy`` is active, this
    endpoint returns **409 Conflict** — the approval gate cannot be
    bypassed via an implicit update, and callers must go through
    ``POST /versions`` → ``/propose`` → ``/approve`` → ``/deploy``.
    """
    from agentic_primitives_gateway.config import settings

    store = _get_store()
    principal = require_principal()
    existing: AgentSpec = await resolve_agent_spec(store, name, principal, owner_query=owner)
    require_owner_or_admin(principal, existing.owner_id)

    if settings.governance.require_admin_approval_for_deploy:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Direct PUT is disabled while the admin-approval gate is on. Create a version via POST /versions and follow the propose/approve/deploy flow.",
                "versions_url": f"/api/v1/agents/{existing.owner_id}:{existing.name}/versions",
            },
        )

    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    merged = existing.model_dump() | updates
    merged["owner_id"] = existing.owner_id
    merged["name"] = existing.name
    new_spec = AgentSpec(**merged)
    await _validate_agent_pools(new_spec, principal, store)
    current_deployed = await store.get_deployed(existing.name, existing.owner_id)
    version = await store.create_version(
        name=existing.name,
        owner_id=existing.owner_id,
        spec=new_spec,
        created_by=principal.id,
        parent_version_id=current_deployed.version_id if current_deployed else None,
        commit_message="updated via PUT /agents/{name}",
        auto_deploy=True,
    )
    emit_audit_event(
        action=AuditAction.AGENT_UPDATE,
        outcome=AuditOutcome.SUCCESS,
        resource_type=ResourceType.AGENT,
        resource_id=f"{existing.owner_id}:{existing.name}",
        metadata={"fields": sorted(updates.keys()), "version_id": version.version_id},
    )
    return version.spec


@router.delete("/{name}")
async def delete_agent(name: str, owner: str | None = None) -> dict[str, Any]:
    store = _get_store()
    principal = require_principal()
    spec: AgentSpec = await resolve_agent_spec(store, name, principal, owner_query=owner)
    require_owner_or_admin(principal, spec.owner_id)
    archived = await store.delete_qualified(spec.name, spec.owner_id)
    emit_audit_event(
        action=AuditAction.AGENT_DELETE,
        outcome=AuditOutcome.SUCCESS,
        resource_type=ResourceType.AGENT,
        resource_id=f"{spec.owner_id}:{spec.name}",
        metadata={"versions_archived": archived},
    )
    return {"status": "deleted", "versions_archived": archived}


@router.get("/{name}/tools", response_model=AgentToolsResponse)
async def get_agent_tools(name: str, owner: str | None = None) -> AgentToolsResponse:
    """List the tools available to an agent and which providers back them."""
    store = _get_store()
    spec: AgentSpec = await resolve_agent_spec(store, name, require_principal(), owner_query=owner)

    # Apply agent-level provider overrides so we resolve the correct providers
    apply_filtered_provider_overrides(
        spec.provider_overrides,
        source=ProviderOverrideSource.AGENT_RUN,
        resource_id=spec.name,
    )

    # Build the tool list (same as the runner does).  Per-primitive
    # context (memory namespace, knowledge namespace, session IDs,
    # team_run_id) comes from contextvars at call time — for an
    # introspection endpoint that never actually dispatches a tool,
    # leaving the contextvars unset is correct.
    tools = build_tool_list(
        spec.primitives,
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
async def get_agent_memory(name: str, session_id: str | None = None, owner: str | None = None) -> AgentMemoryResponse:
    """Introspect memory stores available to an agent."""
    store = _get_store()
    spec: AgentSpec = await resolve_agent_spec(store, name, require_principal(), owner_query=owner)

    # Apply agent-level provider overrides so we read from the same provider the agent writes to
    apply_filtered_provider_overrides(
        spec.provider_overrides,
        source=ProviderOverrideSource.AGENT_RUN,
        resource_id=spec.name,
    )

    mem_config = spec.primitives.get("memory")
    if not mem_config or not mem_config.enabled:
        return AgentMemoryResponse(
            agent_name=name,
            memory_enabled=False,
            namespace="",
            stores=[],
        )

    # Resolve memory namespace (user-scoped — same as runner)
    namespace = resolve_memory_namespace_for_identity(
        name=name,
        owner_id=spec.owner_id,
        namespace_template=mem_config.namespace,
        principal=require_principal(),
    )

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
        # Private namespaces have a terminal user segment; a longer ID
        # (e.g. bob:admin) is another principal, never a child of bob.
        for ns_name in all_namespaces:
            if ns_name == namespace:
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
    spec = await resolve_agent_spec(store, name, require_principal())

    # Apply agent-level provider overrides to the current request context
    apply_filtered_provider_overrides(
        spec.provider_overrides,
        source=ProviderOverrideSource.AGENT_RUN,
        resource_id=spec.name,
    )

    principal = require_principal()
    session_id = request.session_id or uuid.uuid4().hex[:16]
    key = _session_run_key(spec, session_id)
    if not await _bg.reserve(
        key, principal.id, claim_key=f"agent-id:{principal.id}:{session_id}", claim_identity=key, reuse=True
    ):
        raise HTTPException(status_code=409, detail="Session run ID already in use")
    try:
        previous = await _bg.get_events_async(key)
        if previous and previous[0].get("type") == "a2a_started":
            raise HTTPException(status_code=409, detail="A2A task ID cannot be reused as a chat session")
        if _runner._checkpoint_store:
            await _runner._checkpoint_store.clear_cancelled(key)

        async def execute():
            response = await _runner.run(spec=spec, message=request.message, session_id=session_id)
            yield {"type": "done", "response": response.model_dump()}

        queue, _ = _bg.start(key, execute(), owner_id=principal.id)
        result: dict | None = None
        while (event := await queue.get()) is not None:
            result = event
        if result is None:
            raise RuntimeError("Chat run ended without a result")
        if result.get("type") == "error":
            raise RuntimeError(str(result.get("detail", "Chat run failed")))
        if result.get("type") == "cancelled":
            raise HTTPException(status_code=409, detail="Chat run cancelled")
        return ChatResponse(**result["response"])
    finally:
        if not _bg.is_running(key):
            await _bg.abandon(key)


@router.post("/{name}/chat/stream")
async def chat_with_agent_stream(name: str, request: ChatRequest) -> StreamingResponse:
    """Streaming variant of chat. Returns SSE events.

    The agent run executes in a background task so that ``_finalize`` (which
    stores the conversation turn) completes even if the client disconnects
    mid-stream.
    """
    store = _get_store()
    spec = await resolve_agent_spec(store, name, require_principal())

    apply_filtered_provider_overrides(
        spec.provider_overrides,
        source=ProviderOverrideSource.AGENT_RUN,
        resource_id=spec.name,
    )

    principal = require_principal()
    session_id = request.session_id or uuid.uuid4().hex[:16]
    key = _session_run_key(spec, session_id)
    if not await _bg.reserve(
        key, principal.id, claim_key=f"agent-id:{principal.id}:{session_id}", claim_identity=key, reuse=True
    ):
        raise HTTPException(status_code=409, detail="Session run ID already in use")
    try:
        previous = await _bg.get_events_async(key)
        if previous and previous[0].get("type") == "a2a_started":
            raise HTTPException(status_code=409, detail="A2A task ID cannot be reused as a chat session")
        if _runner._checkpoint_store:
            await _runner._checkpoint_store.clear_cancelled(key)
        await _bg.reset_events(key)
        queue, _ = _bg.start(
            key,
            _runner.run_stream(spec=spec, message=request.message, session_id=session_id),
            owner_id=principal.id,
            record_events=True,
        )
    except BaseException:
        await _bg.abandon(key)
        raise
    return sse_response(queue, strip_fields=frozenset({"full_result", "tool_input"}))


@router.get("/{name}/sessions")
async def list_sessions(name: str) -> dict:
    """List all conversation sessions for an agent."""
    store = _get_store()
    spec = await resolve_agent_spec(store, name, require_principal())

    apply_filtered_provider_overrides(
        spec.provider_overrides,
        source=ProviderOverrideSource.AGENT_RUN,
        resource_id=spec.name,
    )

    actor_id = resolve_actor_id(spec, require_principal())
    sessions: list[dict[str, Any]] = []
    try:
        sessions = await registry.memory.list_sessions(actor_id)
    except (NotImplementedError, Exception):
        logger.debug("Failed to list sessions for %s", name)

    return {"agent_name": name, "sessions": sessions}


@router.post("/{name}/sessions/cleanup")
async def cleanup_sessions(name: str, keep: int = 5) -> dict:
    """Delete old sessions, keeping the most recent ``keep`` sessions.

    Sessions are sorted by last activity (most recent first).
    The ``keep`` most recent sessions are preserved.
    """
    store = _get_store()
    spec = await resolve_agent_spec(store, name, require_principal())

    apply_filtered_provider_overrides(
        spec.provider_overrides,
        source=ProviderOverrideSource.AGENT_RUN,
        resource_id=spec.name,
    )

    actor_id = resolve_actor_id(spec, require_principal())
    deleted_count = 0
    try:
        sessions = await registry.memory.list_sessions(actor_id)
        # Sessions should already be sorted by last_activity (newest first)
        # from the provider. Delete everything beyond the keep threshold.
        to_delete = sessions[keep:]
        for sess in to_delete:
            sid = sess.get("session_id", "")
            if sid:
                await registry.memory.delete_session(actor_id=actor_id, session_id=sid)
                deleted_count += 1
    except (NotImplementedError, Exception):
        logger.debug("Failed to cleanup sessions for %s", name)

    return {"deleted": deleted_count, "kept": keep}


@router.delete("/{name}/sessions/{session_id}")
async def delete_session(name: str, session_id: str) -> dict:
    """Delete a session's conversation history."""
    store = _get_store()
    spec = await resolve_agent_spec(store, name, require_principal())

    apply_filtered_provider_overrides(
        spec.provider_overrides,
        source=ProviderOverrideSource.AGENT_RUN,
        resource_id=spec.name,
    )

    actor_id = resolve_actor_id(spec, require_principal())
    try:
        await registry.memory.delete_session(actor_id=actor_id, session_id=session_id)
    except (NotImplementedError, Exception):
        logger.debug("Failed to delete session %s/%s", name, session_id)

    return {"status": "deleted"}


@router.get("/{name}/sessions/{session_id}/stream")
async def stream_session_events(name: str, session_id: str) -> StreamingResponse:
    """SSE stream of session events from the event store.

    Replays existing events, then polls for new events every second.
    Used by the UI to reconnect after a stream drop.
    """
    store = _get_store()
    spec = await resolve_agent_spec(store, name, require_principal())
    key = await _require_session_run_owner(spec, session_id)

    return StreamingResponse(
        reconnect_event_generator(_bg, key, done_event_types=frozenset({"done"})),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{name}/sessions/{session_id}/status")
async def get_session_status(name: str, session_id: str) -> dict:
    """Check if a run is currently active for this session."""
    store = _get_store()
    spec = await resolve_agent_spec(store, name, require_principal())
    key = _session_run_key(spec, session_id)
    owner = await _bg.get_owner_async(key)
    principal = require_principal()
    if owner is not None and owner != principal.id and not principal.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden")
    if owner is None:
        raw_owner = await _bg.get_owner_async(f"agent-id:{principal.id}:{session_id}")
        if raw_owner is not None and raw_owner != key:
            raise HTTPException(status_code=403, detail="Forbidden")
        return {"status": "idle"}
    _bg.cleanup()
    return {"status": await _bg.get_status_async(key)}


@router.delete("/{name}/sessions/{session_id}/run")
async def cancel_session_run(name: str, session_id: str) -> dict:
    """Cancel an active agent run for this session."""
    store = _get_store()
    spec = await resolve_agent_spec(store, name, require_principal())
    key = await _require_session_run_owner(spec, session_id)
    if _runner._checkpoint_store:
        await _runner._checkpoint_store.mark_cancelled(key)

    # Try to cancel the local background task (works for non-recovered runs)
    await _bg.cancel(key)

    # Also try to cancel a recovery task (works for resumed runs on this replica)
    from agentic_primitives_gateway.agents.checkpoint import cancel_recovery_task

    cancel_recovery_task(key)

    # Set event store status to cancelled (works for both local and recovered runs)
    if _bg._event_store:
        with contextlib.suppress(Exception):
            await _bg._event_store.set_status(key, "cancelled")
            await _bg._event_store.append_event(key, {"type": "cancelled"})

    # Delete checkpoint so orphan recovery doesn't resume this run
    if _runner._checkpoint_store:
        with contextlib.suppress(Exception):
            await _runner._checkpoint_store.delete(key)

    return {"status": "cancelled"}


@router.get("/{name}/sessions/{session_id}", response_model=SessionHistoryResponse)
async def get_session_history(name: str, session_id: str) -> SessionHistoryResponse:
    """Retrieve conversation history for a specific agent session."""
    store = _get_store()
    spec = await resolve_agent_spec(store, name, require_principal())

    apply_filtered_provider_overrides(
        spec.provider_overrides,
        source=ProviderOverrideSource.AGENT_RUN,
        resource_id=spec.name,
    )

    actor_id = resolve_actor_id(spec, require_principal())
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


# ── Versioning / fork / lineage / proposals ────────────────────────────


@router.get("/{name}/versions", response_model=AgentVersionListResponse)
async def list_versions(name: str, owner: str | None = None) -> AgentVersionListResponse:
    """List all versions for an agent identity (all statuses)."""
    store = _get_store()
    principal = require_principal()
    spec: AgentSpec = await resolve_agent_spec(store, name, principal, owner_query=owner)
    versions = await store.list_versions(spec.name, spec.owner_id)
    return AgentVersionListResponse(versions=versions)


@router.get("/{name}/versions/{version_id}", response_model=AgentVersion)
async def get_version(name: str, version_id: str, owner: str | None = None) -> AgentVersion:
    """Fetch a single historical version."""
    store = _get_store()
    principal = require_principal()
    spec: AgentSpec = await resolve_agent_spec(store, name, principal, owner_query=owner)
    v = await store.get_version(spec.name, spec.owner_id, version_id)
    if v is None:
        raise HTTPException(status_code=404, detail=f"Version '{version_id}' not found")
    return v


@router.post("/{name}/versions", response_model=AgentVersion, status_code=201)
async def create_version(name: str, request: CreateVersionRequest, owner: str | None = None) -> AgentVersion:
    """Create a new version of an agent.

    Auto-deploys when the admin-approval gate is off; otherwise lands as
    ``draft`` — callers must then ``propose`` → admin ``approve`` →
    ``deploy`` to serve traffic.
    """
    store = _get_store()
    principal = require_principal()
    existing: AgentSpec = await resolve_agent_spec(store, name, principal, owner_query=owner)
    require_owner_or_admin(principal, existing.owner_id)

    updates = {
        k: v for k, v in request.model_dump(exclude={"commit_message", "parent_version_id"}).items() if v is not None
    }
    merged = existing.model_dump() | updates
    merged["owner_id"] = existing.owner_id
    merged["name"] = existing.name
    new_spec = AgentSpec(**merged)
    await _validate_agent_pools(new_spec, principal, store)
    parent_id = request.parent_version_id
    if parent_id is None:
        current_deployed = await store.get_deployed(existing.name, existing.owner_id)
        parent_id = current_deployed.version_id if current_deployed else None
    version = await store.create_version(
        name=existing.name,
        owner_id=existing.owner_id,
        spec=new_spec,
        created_by=principal.id,
        parent_version_id=parent_id,
        commit_message=request.commit_message,
        auto_deploy=True,
    )
    emit_audit_event(
        action=AuditAction.AGENT_VERSION_CREATE,
        outcome=AuditOutcome.SUCCESS,
        resource_type=ResourceType.AGENT,
        resource_id=f"{existing.owner_id}:{existing.name}",
        metadata={
            "version_id": version.version_id,
            "version_number": version.version_number,
            "parent_version_id": parent_id,
            "commit_message": request.commit_message,
            "auto_deployed": version.status.value == "deployed",
        },
    )
    metrics.AGENT_VERSIONS_CREATED.labels(
        ns_kind=_ns_kind(existing.owner_id),
        auto_deployed=str(version.status.value == "deployed").lower(),
    ).inc()
    return version


@router.post("/{name}/versions/{version_id}/propose", response_model=AgentVersion)
async def propose_version(name: str, version_id: str, owner: str | None = None) -> AgentVersion:
    """Transition a draft version → proposed (admin-approval mode only)."""
    store = _get_store()
    principal = require_principal()
    spec: AgentSpec = await resolve_agent_spec(store, name, principal, owner_query=owner)
    require_owner_or_admin(principal, spec.owner_id)
    try:
        version = await store.propose_version(spec.name, spec.owner_id, version_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Version '{version_id}' not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    emit_audit_event(
        action=AuditAction.AGENT_VERSION_PROPOSE,
        outcome=AuditOutcome.SUCCESS,
        resource_type=ResourceType.AGENT,
        resource_id=f"{spec.owner_id}:{spec.name}",
        metadata={"version_id": version.version_id, "version_number": version.version_number},
    )
    return version


@router.post("/{name}/versions/{version_id}/approve", response_model=AgentVersion)
async def approve_version(name: str, version_id: str, owner: str | None = None) -> AgentVersion:
    """Admin-only approval of a proposed version."""
    principal = require_admin()
    store = _get_store()
    spec: AgentSpec = await resolve_agent_spec(store, name, principal, owner_query=owner)
    try:
        version = await store.approve_version(spec.name, spec.owner_id, version_id, approver_id=principal.id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Version '{version_id}' not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    emit_audit_event(
        action=AuditAction.AGENT_VERSION_APPROVE,
        outcome=AuditOutcome.SUCCESS,
        resource_type=ResourceType.AGENT,
        resource_id=f"{spec.owner_id}:{spec.name}",
        metadata={
            "version_id": version.version_id,
            "version_number": version.version_number,
            "approver_id": principal.id,
        },
    )
    metrics.AGENT_VERSION_APPROVALS.labels(outcome="approved").inc()
    return version


@router.post("/{name}/versions/{version_id}/reject", response_model=AgentVersion)
async def reject_version(
    name: str, version_id: str, request: RejectionRequest, owner: str | None = None
) -> AgentVersion:
    """Admin-only rejection of a proposed version."""
    principal = require_admin()
    store = _get_store()
    spec: AgentSpec = await resolve_agent_spec(store, name, principal, owner_query=owner)
    try:
        version = await store.reject_version(
            spec.name,
            spec.owner_id,
            version_id,
            approver_id=principal.id,
            reason=request.reason,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Version '{version_id}' not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    emit_audit_event(
        action=AuditAction.AGENT_VERSION_REJECT,
        outcome=AuditOutcome.SUCCESS,
        resource_type=ResourceType.AGENT,
        resource_id=f"{spec.owner_id}:{spec.name}",
        metadata={
            "version_id": version.version_id,
            "version_number": version.version_number,
            "approver_id": principal.id,
            "reason_truncated": request.reason[:200],
        },
    )
    metrics.AGENT_VERSION_APPROVALS.labels(outcome="rejected").inc()
    return version


@router.post("/{name}/versions/{version_id}/deploy", response_model=AgentVersion)
async def deploy_version(name: str, version_id: str, owner: str | None = None) -> AgentVersion:
    """Flip the deployed pointer to ``version_id``."""
    store = _get_store()
    principal = require_principal()
    spec: AgentSpec = await resolve_agent_spec(store, name, principal, owner_query=owner)
    require_owner_or_admin(principal, spec.owner_id)
    candidate = await store.get_version(spec.name, spec.owner_id, version_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail=f"Version '{version_id}' not found")
    await _validate_agent_pools(candidate.spec, principal, store)
    previous = await store.get_deployed(spec.name, spec.owner_id)
    try:
        version = await store.deploy_version(spec.name, spec.owner_id, version_id, deployed_by=principal.id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Version '{version_id}' not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    emit_audit_event(
        action=AuditAction.AGENT_VERSION_DEPLOY,
        outcome=AuditOutcome.SUCCESS,
        resource_type=ResourceType.AGENT,
        resource_id=f"{spec.owner_id}:{spec.name}",
        metadata={
            "version_id": version.version_id,
            "version_number": version.version_number,
            "previous_version_id": previous.version_id if previous else None,
        },
    )
    metrics.AGENT_VERSION_APPROVALS.labels(outcome="deployed").inc()
    return version


@router.post("/{name}/fork", response_model=AgentVersion, status_code=201)
async def fork_agent(name: str, request: ForkRequest, owner: str | None = None) -> AgentVersion:
    """Fork an accessible agent into the caller's namespace.

    Sub-agent references in the forked spec are auto-qualified to the
    source owner's namespace so the forked graph keeps working.
    """
    store = _get_store()
    principal = require_principal()
    if principal.id == "system" and not principal.is_admin:
        raise HTTPException(status_code=403, detail="System namespace requires admin access")
    source: AgentSpec = await resolve_agent_spec(store, name, principal, owner_query=owner)
    target = source.model_copy(update={"owner_id": principal.id, "name": request.target_name or source.name})
    await _validate_agent_pools(target, principal, store)
    try:
        version = await store.fork(
            source_name=source.name,
            source_owner_id=source.owner_id,
            target_owner_id=principal.id,
            target_name=request.target_name,
            created_by=principal.id,
            commit_message=request.commit_message,
        )
    except KeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    emit_audit_event(
        action=AuditAction.AGENT_FORK,
        outcome=AuditOutcome.SUCCESS,
        resource_type=ResourceType.AGENT,
        resource_id=f"{principal.id}:{version.agent_name}",
        metadata={
            "source_owner_id": source.owner_id,
            "source_name": source.name,
            "source_version_id": version.forked_from.version_id if version.forked_from else None,
            "target_owner_id": principal.id,
            "target_name": version.agent_name,
            "target_version_id": version.version_id,
        },
    )
    metrics.AGENT_FORKS.labels(source_ns_kind=_ns_kind(source.owner_id)).inc()
    return version


@router.get("/{name}/lineage", response_model=AgentLineage)
async def get_lineage(name: str, owner: str | None = None) -> AgentLineage:
    """Return the full lineage DAG rooted at this agent identity."""
    store = _get_store()
    principal = require_principal()
    spec: AgentSpec = await resolve_agent_spec(store, name, principal, owner_query=owner)
    return await store.get_lineage_model(spec.name, spec.owner_id)
