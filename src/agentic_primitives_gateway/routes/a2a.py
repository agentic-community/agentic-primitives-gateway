"""A2A (Agent-to-Agent) protocol endpoints.

Exposes APG agents as A2A-compatible agents. External agents can discover
APG agents via agent cards and interact via the A2A message/task protocol.

Two levels of discovery:
- ``GET /.well-known/agent.json`` — gateway-level card listing all public agents
- ``GET /a2a/agents/{name}/.well-known/agent.json`` — per-agent card

Per-agent endpoints (no metadata routing needed):
- ``POST /a2a/agents/{name}/message:send``
- ``POST /a2a/agents/{name}/message:stream``
- ``GET  /a2a/agents/{name}/tasks/{task_id}``
- ``POST /a2a/agents/{name}/tasks/{task_id}:cancel``
- ``GET  /a2a/agents/{name}/tasks/{task_id}:subscribe``

A2A tasks map to APG sessions: task_id == session_id.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import StreamingResponse

from agentic_primitives_gateway.agents.runner import AgentRunner
from agentic_primitives_gateway.agents.store import AgentStore
from agentic_primitives_gateway.audit.emit import emit_audit_event
from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType
from agentic_primitives_gateway.auth.access import (
    ProviderOverrideSource,
    apply_filtered_provider_overrides,
)
from agentic_primitives_gateway.config import settings
from agentic_primitives_gateway.models.a2a import (
    A2AAgentCapabilities,
    A2AAgentCard,
    A2AAgentInterface,
    A2AAgentSkill,
    A2AArtifact,
    A2AMessage,
    A2APart,
    A2ASecurityScheme,
    A2ASendMessageRequest,
    A2ATask,
    A2ATaskArtifactUpdateEvent,
    A2ATaskState,
    A2ATaskStatus,
    A2ATaskStatusUpdateEvent,
)
from agentic_primitives_gateway.models.agents import AgentSpec
from agentic_primitives_gateway.routes._background import run_event_key
from agentic_primitives_gateway.routes._helpers import require_principal

logger = logging.getLogger(__name__)

router = APIRouter(tags=["a2a"])

_store: AgentStore | None = None
_runner = AgentRunner()


def set_a2a_dependencies(store: AgentStore, runner: AgentRunner) -> None:
    """Inject the agent store and runner (called during app lifespan)."""
    global _store, _runner
    _store = store
    _runner = runner


def _get_store() -> AgentStore:
    if _store is None:
        raise RuntimeError("A2A: Agent store not initialized")
    return _store


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _extract_text(message: A2AMessage) -> str:
    """Extract text content from A2A message parts."""
    texts: list[str] = []
    for part in message.parts:
        if part.text:
            texts.append(part.text)
        elif part.data:
            texts.append(json.dumps(part.data))
    return "\n".join(texts) if texts else ""


async def _get_public_agents() -> list[AgentSpec]:
    """Return agents that are shared with everyone."""
    store = _get_store()
    all_agents = await store.list()
    return [a for a in all_agents if "*" in a.shared_with]


async def _require_agent(name: str) -> AgentSpec:
    """Load an agent spec and check access. Raises 404/403."""
    from agentic_primitives_gateway.routes._helpers import resolve_agent_spec

    store = _get_store()
    spec: AgentSpec = await resolve_agent_spec(store, name, require_principal())
    apply_filtered_provider_overrides(
        spec.provider_overrides,
        source=ProviderOverrideSource.A2A,
        resource_id=spec.name,
    )
    return spec


def _task_key(spec: AgentSpec, task_id: str) -> str:
    return run_event_key("agent", require_principal().id, spec.owner_id, spec.name, task_id)


async def _reserve_task(spec: AgentSpec, task_id: str) -> str:
    from agentic_primitives_gateway.routes.agents import _bg

    key = _task_key(spec, task_id)
    if not await _bg.reserve(
        key, require_principal().id, claim_key=f"agent-id:{require_principal().id}:{task_id}", claim_identity=key
    ):
        raise HTTPException(status_code=409, detail="Task run ID already in use")
    try:
        await _bg.reset_events(key)
    except BaseException:
        await _bg.abandon(key)
        raise
    return key


def _safe_event(event: dict[str, Any]) -> dict[str, Any]:
    """Persist only the same public fields exposed to agent chat subscribers."""
    return {k: v for k, v in event.items() if k not in ("full_result", "tool_input")}


def _task_state(status: str, events: list[dict[str, Any]]) -> A2ATaskState:
    if status == "cancelled" or any(evt.get("type") == "cancelled" for evt in events):
        return A2ATaskState.CANCELED
    if any(evt.get("type") == "error" for evt in events):
        return A2ATaskState.FAILED
    if any(evt.get("type") == "done" for evt in events):
        return A2ATaskState.COMPLETED
    if status == "running":
        return A2ATaskState.WORKING
    return A2ATaskState.SUBMITTED


def _task_from_events(task_id: str, name: str, status: str, events: list[dict[str, Any]]) -> A2ATask:
    state = _task_state(status, events)
    context_id = next((evt["context_id"] for evt in events if evt.get("type") == "a2a_started"), task_id)
    response = "".join(str(evt.get("content", "")) for evt in events if evt.get("type") == "token")
    for evt in events:
        if evt.get("type") == "done":
            response = str(evt.get("response", response))
    artifacts = None
    if response and state == A2ATaskState.COMPLETED:
        artifacts = [A2AArtifact(artifact_id=str(uuid.uuid4()), name="response", parts=[A2APart(text=response)])]
    return A2ATask(
        id=task_id,
        context_id=context_id,
        status=A2ATaskStatus(state=state, timestamp=_now_iso()),
        artifacts=artifacts,
        metadata={"agent_name": name},
    )


# ── Security scheme helpers ─────────────────────────────────────────


def _build_security_schemes() -> tuple[
    dict[str, A2ASecurityScheme] | None,
    list[dict[str, list[str]]] | None,
]:
    """Derive A2A security schemes from the current auth config."""
    auth_backend = settings.auth.backend
    schemes: dict[str, A2ASecurityScheme] | None = None
    reqs: list[dict[str, list[str]]] | None = None

    if auth_backend == "api_key":
        schemes = {
            "apiKey": A2ASecurityScheme(
                type="apiKey",
                name="Authorization",
                location="header",
                description="API key authentication via Authorization header",
            )
        }
        reqs = [{"apiKey": []}]
    elif auth_backend == "jwt":
        jwt_cfg = settings.auth.jwt
        oidc_url = jwt_cfg.get("jwks_url", "")
        schemes = {
            "openIdConnect": A2ASecurityScheme(
                type="openIdConnect",
                description="JWT/OIDC authentication",
                open_id_connect_url=oidc_url,
            )
        }
        reqs = [{"openIdConnect": []}]

    return schemes, reqs


def _build_skill(agent: AgentSpec) -> A2AAgentSkill:
    """Convert an agent spec to an A2A skill."""
    tags = sorted(agent.primitives.keys()) if agent.primitives else []
    return A2AAgentSkill(
        id=agent.name,
        name=agent.name,
        description=agent.description or f"Agent: {agent.name}",
        tags=tags,
    )


def _build_agent_card(
    agent: AgentSpec,
    base_url: str,
    security_schemes: dict[str, A2ASecurityScheme] | None,
    security_requirements: list[dict[str, list[str]]] | None,
) -> A2AAgentCard:
    """Build an A2A agent card for a single agent."""
    return A2AAgentCard(
        name=agent.name,
        description=agent.description or f"Agent: {agent.name}",
        version="0.1.0",
        supported_interfaces=[
            A2AAgentInterface(
                url=f"{base_url}/a2a/agents/{agent.name}",
                protocol_binding="http+json",
                protocol_version="0.2",
            )
        ],
        capabilities=A2AAgentCapabilities(streaming=True, push_notifications=False),
        skills=[_build_skill(agent)],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        security_schemes=security_schemes,
        security_requirements=security_requirements,
        provider={"organization": "Agentic Primitives Gateway"},
    )


# ── Discovery ────────────────────────────────────────────────────────


@router.get("/.well-known/agent.json", response_model=A2AAgentCard)
async def get_gateway_agent_card(request: Request) -> A2AAgentCard:
    """Gateway-level agent card listing all public agents as skills. Auth-exempt."""
    agents = await _get_public_agents()
    skills = [_build_skill(a) for a in agents]
    security_schemes, security_requirements = _build_security_schemes()
    base_url = str(request.base_url).rstrip("/")

    return A2AAgentCard(
        name="Agentic Primitives Gateway",
        description="Multi-agent gateway exposing APG agents via the A2A protocol",
        version="0.1.0",
        supported_interfaces=[
            A2AAgentInterface(
                url=f"{base_url}/a2a",
                protocol_binding="http+json",
                protocol_version="0.2",
            )
        ],
        capabilities=A2AAgentCapabilities(streaming=True, push_notifications=False),
        skills=skills,
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        security_schemes=security_schemes,
        security_requirements=security_requirements,
        provider={"organization": "Agentic Primitives Gateway"},
    )


@router.get("/a2a/agents/{name}/.well-known/agent.json", response_model=A2AAgentCard)
async def get_per_agent_card(name: str, request: Request) -> A2AAgentCard:
    """Per-agent A2A card. Auth-exempt for public agents, requires auth otherwise.

    Uses the bare-name ``store.get(name)`` lookup because this endpoint is
    discovered by external clients that don't know the owner namespace.
    Treats the first-matching deployed spec as the agent card — intended
    only for publicly shared (``"*"``) agents.
    """
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    # Only public agents are discoverable without auth — return 404
    # (not 403) for private agents to avoid leaking existence.
    if "*" not in spec.shared_with:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    security_schemes, security_requirements = _build_security_schemes()
    base_url = str(request.base_url).rstrip("/")
    return _build_agent_card(spec, base_url, security_schemes, security_requirements)


# ── Send Message (synchronous) ───────────────────────────────────────


@router.post("/a2a/agents/{name}/message:send", response_model=A2ATask)
async def send_message(name: str, request: A2ASendMessageRequest) -> A2ATask:
    """Execute a claimed task independently of the HTTP connection."""
    spec = await _require_agent(name)
    task_id = request.message.task_id or str(uuid.uuid4())
    context_id = request.message.context_id or task_id
    key = await _reserve_task(spec, task_id)
    from agentic_primitives_gateway.routes.agents import _bg

    async def _run() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "a2a_started", "context_id": context_id}
        result = await _runner.run(spec=spec, message=_extract_text(request.message), session_id=task_id)
        yield {
            "type": "done",
            "response": result.response,
            "session_id": result.session_id,
            "turns_used": result.turns_used,
            "tools_called": result.tools_called,
        }

    try:
        queue, _ = _bg.start(key, _run(), owner_id=require_principal().id, record_events=True)
    except BaseException:
        await _bg.abandon(key)
        raise

    events: list[dict[str, Any]] = []
    while (event := await queue.get()) is not None:
        events.append(event)

    result = _task_from_events(task_id, name, await _bg.get_status_async(key), events)
    if result.status.state == A2ATaskState.COMPLETED:
        done = next(evt for evt in reversed(events) if evt.get("type") == "done")
        result.artifacts = [
            A2AArtifact(
                artifact_id=str(uuid.uuid4()),
                name="response",
                parts=[A2APart(text=done["response"])],
                metadata={"turns_used": done["turns_used"], "tools_called": done["tools_called"]},
            )
        ]
        result.history = [
            request.message,
            A2AMessage(
                message_id=str(uuid.uuid4()),
                role="agent",
                parts=[A2APart(text=done["response"])],
                task_id=task_id,
            ),
        ]
        result.metadata = {"agent_name": name, "session_id": done["session_id"]}
    elif result.status.state == A2ATaskState.FAILED:
        detail = next(
            (str(evt.get("detail", "Unknown error")) for evt in reversed(events) if evt.get("type") == "error"),
            "Unknown error",
        )
        result.status.message = A2AMessage(
            message_id=str(uuid.uuid4()), role="agent", parts=[A2APart(text=f"Error: {detail}")]
        )
    return result


# ── Send Streaming Message ───────────────────────────────────────────


@router.post("/a2a/agents/{name}/message:stream")
async def send_message_stream(name: str, request: A2ASendMessageRequest) -> StreamingResponse:
    """Stream task updates; the producer continues if the client disconnects."""
    spec = await _require_agent(name)
    task_id = request.message.task_id or str(uuid.uuid4())
    context_id = request.message.context_id or task_id
    key = await _reserve_task(spec, task_id)
    from agentic_primitives_gateway.routes.agents import _bg

    async def _run() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "a2a_started", "context_id": context_id}
        async for event in _runner.run_stream(spec=spec, message=_extract_text(request.message), session_id=task_id):
            yield _safe_event(event)

    try:
        queue, _ = _bg.start(key, _run(), owner_id=require_principal().id, record_events=True)
    except BaseException:
        await _bg.abandon(key)
        raise

    async def _generate() -> AsyncIterator[str]:
        status_event = A2ATaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            status=A2ATaskStatus(state=A2ATaskState.WORKING, timestamp=_now_iso()),
        )
        yield f"data: {json.dumps({'type': 'status_update', **status_event.model_dump()})}\n\n"

        events: list[dict[str, Any]] = []
        while (event := await queue.get()) is not None:
            events.append(event)
            translated = _translate_stream_event(event, task_id, context_id)
            if translated:
                yield f"data: {json.dumps(translated)}\n\n"

        final_task = _task_from_events(task_id, name, await _bg.get_status_async(key), events)
        yield f"data: {json.dumps({'type': 'task', **final_task.model_dump()})}\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Task operations ──────────────────────────────────────────────────


@router.post("/a2a/agents/{name}/tasks/{task_id}:cancel", response_model=A2ATask)
async def cancel_task(name: str, task_id: str) -> A2ATask:
    """Cancel a claimed A2A task on this or another replica."""
    spec = await _require_agent(name)
    from agentic_primitives_gateway.agents.checkpoint import cancel_recovery_task
    from agentic_primitives_gateway.routes.agents import _bg

    key = _task_key(spec, task_id)
    if await _bg.get_owner_async(key) != require_principal().id:
        raise HTTPException(status_code=403, detail="Forbidden")

    events = await _bg.get_events_async(key)
    status = await _bg.get_status_async(key)
    state = _task_state(status, events)
    if state in (A2ATaskState.SUBMITTED, A2ATaskState.WORKING):
        checkpoint_store = _runner._checkpoint_store
        if checkpoint_store:
            # Signal first: a remote producer must see the marker even if cleanup fails.
            await checkpoint_store.mark_cancelled(key)
        await _bg.cancel(key)
        cancel_recovery_task(key)
        if checkpoint_store:
            with contextlib.suppress(Exception):
                await checkpoint_store.delete(key)
        if _bg._event_store:
            await _bg._event_store.set_status(key, "cancelled")
            await _bg._event_store.append_event(key, {"type": "cancelled"})
        state = A2ATaskState.CANCELED

    emit_audit_event(
        action=AuditAction.AGENT_RUN_CANCELLED,
        outcome=AuditOutcome.SUCCESS if state == A2ATaskState.CANCELED else AuditOutcome.FAILURE,
        resource_type=ResourceType.AGENT,
        resource_id=name,
        metadata={"task_id": task_id, "source": "a2a"},
    )
    return A2ATask(
        id=task_id,
        context_id=next((evt["context_id"] for evt in events if evt.get("type") == "a2a_started"), task_id),
        status=A2ATaskStatus(state=state, timestamp=_now_iso()),
        metadata={"agent_name": name},
    )


@router.get("/a2a/agents/{name}/tasks/{task_id}:subscribe")
async def subscribe_task(name: str, task_id: str) -> StreamingResponse:
    """Replay updates for an owned task, then follow its remaining events."""
    spec = await _require_agent(name)
    from agentic_primitives_gateway.routes.agents import _bg

    key = _task_key(spec, task_id)
    if await _bg.get_owner_async(key) != require_principal().id:
        raise HTTPException(status_code=403, detail="Forbidden")

    async def _generate() -> AsyncIterator[str]:
        sent = 0
        idle_count = 0
        while idle_count < 900:
            events = await _bg.get_events_async(key)
            status = await _bg.get_status_async(key)
            context_id = next((evt["context_id"] for evt in events if evt.get("type") == "a2a_started"), task_id)
            for evt in events[sent:]:
                translated = _translate_stream_event(evt, task_id, context_id)
                if translated:
                    yield f"data: {json.dumps(translated)}\n\n"
            if len(events) > sent:
                sent = len(events)
                idle_count = 0
            else:
                idle_count += 1

            state = _task_state(status, events)
            if state in (A2ATaskState.COMPLETED, A2ATaskState.FAILED, A2ATaskState.CANCELED):
                final_task = _task_from_events(task_id, name, status, events)
                yield f"data: {json.dumps({'type': 'task', **final_task.model_dump()})}\n\n"
                break
            await asyncio.sleep(0.2)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/a2a/agents/{name}/tasks/{task_id}", response_model=A2ATask)
async def get_task(name: str, task_id: str) -> A2ATask:
    """Get task state only after proving ownership of the scoped run."""
    spec = await _require_agent(name)
    from agentic_primitives_gateway.routes.agents import _bg

    key = _task_key(spec, task_id)
    if await _bg.get_owner_async(key) != require_principal().id:
        raise HTTPException(status_code=403, detail="Forbidden")

    return _task_from_events(task_id, name, await _bg.get_status_async(key), await _bg.get_events_async(key))


# ── Event translation ────────────────────────────────────────────────


def _translate_stream_event(evt: dict[str, Any], task_id: str, context_id: str) -> dict[str, Any] | None:
    """Translate an APG SSE event dict to an A2A StreamResponse dict."""
    evt_type = evt.get("type", "")

    if evt_type in ("token", "sub_agent_token"):
        msg = A2AMessage(
            message_id=str(uuid.uuid4()),
            role="agent",
            parts=[A2APart(text=evt.get("content", ""))],
            task_id=task_id,
        )
        if evt_type == "sub_agent_token":
            msg.metadata = {"sub_agent": evt.get("agent_name", "")}
        return {"type": "message", **msg.model_dump()}

    if evt_type in ("tool_call_start", "tool_call_result", "sub_agent_tool"):
        tool_data: dict[str, Any] = {"tool_name": evt.get("tool_name", evt.get("name", ""))}
        if evt_type == "tool_call_result":
            tool_data["result"] = evt.get("result", "")
        if evt_type == "sub_agent_tool":
            tool_data["agent_name"] = evt.get("agent_name", "")
        msg = A2AMessage(
            message_id=str(uuid.uuid4()),
            role="agent",
            parts=[A2APart(data=tool_data, media_type="application/json")],
            task_id=task_id,
            metadata={"event_type": evt_type},
        )
        return {"type": "message", **msg.model_dump()}

    if evt_type == "done":
        artifact = A2AArtifact(
            artifact_id=str(uuid.uuid4()),
            name="response",
            parts=[A2APart(text=evt.get("response", ""))],
        )
        artifact_event = A2ATaskArtifactUpdateEvent(
            task_id=task_id, context_id=context_id, artifact=artifact, last_chunk=True
        )
        return {"type": "artifact_update", **artifact_event.model_dump()}

    if evt_type == "error":
        status_event = A2ATaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            status=A2ATaskStatus(
                state=A2ATaskState.FAILED,
                message=A2AMessage(
                    message_id=str(uuid.uuid4()),
                    role="agent",
                    parts=[A2APart(text=evt.get("detail", "Unknown error"))],
                ),
                timestamp=_now_iso(),
            ),
        )
        return {"type": "status_update", **status_event.model_dump()}

    if evt_type == "cancelled":
        status_event = A2ATaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            status=A2ATaskStatus(state=A2ATaskState.CANCELED, timestamp=_now_iso()),
        )
        return {"type": "status_update", **status_event.model_dump()}

    return None
