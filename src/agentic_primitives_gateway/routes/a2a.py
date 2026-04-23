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
from agentic_primitives_gateway.auth.access import require_access
from agentic_primitives_gateway.config import settings
from agentic_primitives_gateway.context import set_provider_overrides
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
    if spec.provider_overrides:
        set_provider_overrides(spec.provider_overrides)
    return spec


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
    # Only public agents are discoverable without auth
    if "*" not in spec.shared_with:
        require_access(require_principal(), spec.owner_id, spec.shared_with)

    security_schemes, security_requirements = _build_security_schemes()
    base_url = str(request.base_url).rstrip("/")
    return _build_agent_card(spec, base_url, security_schemes, security_requirements)


# ── Send Message (synchronous) ───────────────────────────────────────


@router.post("/a2a/agents/{name}/message:send", response_model=A2ATask)
async def send_message(name: str, request: A2ASendMessageRequest) -> A2ATask:
    """Send a message to a specific agent and receive a completed task."""
    spec = await _require_agent(name)

    message_text = _extract_text(request.message)
    task_id = request.message.task_id or str(uuid.uuid4())
    context_id = request.message.context_id or task_id

    try:
        result = await _runner.run(spec=spec, message=message_text, session_id=task_id)
    except Exception as exc:
        logger.exception("A2A send_message failed for agent=%s", name)
        return A2ATask(
            id=task_id,
            context_id=context_id,
            status=A2ATaskStatus(
                state=A2ATaskState.FAILED,
                message=A2AMessage(
                    message_id=str(uuid.uuid4()),
                    role="agent",
                    parts=[A2APart(text=f"Error: {exc}")],
                ),
                timestamp=_now_iso(),
            ),
        )

    artifact = A2AArtifact(
        artifact_id=str(uuid.uuid4()),
        name="response",
        parts=[A2APart(text=result.response)],
        metadata={"turns_used": result.turns_used, "tools_called": result.tools_called},
    )

    return A2ATask(
        id=task_id,
        context_id=context_id,
        status=A2ATaskStatus(state=A2ATaskState.COMPLETED, timestamp=_now_iso()),
        artifacts=[artifact],
        history=[
            request.message,
            A2AMessage(
                message_id=str(uuid.uuid4()),
                role="agent",
                parts=[A2APart(text=result.response)],
                task_id=task_id,
            ),
        ],
        metadata={"agent_name": name, "session_id": result.session_id},
    )


# ── Send Streaming Message ───────────────────────────────────────────


@router.post("/a2a/agents/{name}/message:stream")
async def send_message_stream(name: str, request: A2ASendMessageRequest) -> StreamingResponse:
    """Send a message to an agent and receive streaming A2A events via SSE."""
    spec = await _require_agent(name)

    message_text = _extract_text(request.message)
    task_id = request.message.task_id or str(uuid.uuid4())
    context_id = request.message.context_id or task_id

    async def _generate() -> AsyncIterator[str]:
        # Emit initial status: working
        status_event = A2ATaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            status=A2ATaskStatus(state=A2ATaskState.WORKING, timestamp=_now_iso()),
        )
        yield f"data: {json.dumps({'type': 'status_update', **status_event.model_dump()})}\n\n"

        accumulated_text = ""
        final_state = A2ATaskState.COMPLETED

        try:
            async for event in _runner.run_stream(spec=spec, message=message_text, session_id=task_id):
                a2a_event = _translate_stream_event(event, task_id, context_id)
                if a2a_event:
                    yield f"data: {json.dumps(a2a_event)}\n\n"

                evt_type = event.get("type", "")
                if evt_type in ("token", "sub_agent_token"):
                    accumulated_text += event.get("content", "")
                elif evt_type == "done":
                    accumulated_text = event.get("response", accumulated_text)
                elif evt_type == "error":
                    final_state = A2ATaskState.FAILED
                    accumulated_text = event.get("detail", "Unknown error")

        except Exception as exc:
            logger.exception("A2A streaming failed for agent=%s", name)
            final_state = A2ATaskState.FAILED
            accumulated_text = f"Error: {exc}"

        # Emit final artifact
        artifact = A2AArtifact(
            artifact_id=str(uuid.uuid4()),
            name="response",
            parts=[A2APart(text=accumulated_text)],
        )
        artifact_event = A2ATaskArtifactUpdateEvent(
            task_id=task_id, context_id=context_id, artifact=artifact, last_chunk=True
        )
        yield f"data: {json.dumps({'type': 'artifact_update', **artifact_event.model_dump()})}\n\n"

        # Emit final task
        final_task = A2ATask(
            id=task_id,
            context_id=context_id,
            status=A2ATaskStatus(state=final_state, timestamp=_now_iso()),
            artifacts=[artifact],
            metadata={"agent_name": name},
        )
        yield f"data: {json.dumps({'type': 'task', **final_task.model_dump()})}\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Task operations ──────────────────────────────────────────────────


@router.get("/a2a/agents/{name}/tasks/{task_id}", response_model=A2ATask)
async def get_task(name: str, task_id: str) -> A2ATask:
    """Get the current state of an A2A task."""
    await _require_agent(name)

    from agentic_primitives_gateway.routes.agents import _bg as agent_bg

    # Verify run ownership
    principal = require_principal()
    owner = await agent_bg.get_owner_async(task_id)
    if owner and owner != principal.id and not principal.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden")

    status_str = await agent_bg.get_status_async(task_id)
    events = await agent_bg.get_events_async(task_id)

    if status_str == "running":
        state = A2ATaskState.WORKING
    elif events:
        last = events[-1] if events else {}
        if isinstance(last, dict) and last.get("type") == "error":
            state = A2ATaskState.FAILED
        else:
            state = A2ATaskState.COMPLETED
    else:
        state = A2ATaskState.SUBMITTED

    # Reconstruct response from events
    accumulated_text = ""
    for evt in events:
        if not isinstance(evt, dict):
            continue
        if evt.get("type") == "token":
            accumulated_text += evt.get("content", "")
        elif evt.get("type") == "done":
            accumulated_text = evt.get("response", accumulated_text)

    artifacts: list[A2AArtifact] | None = None
    if accumulated_text and state == A2ATaskState.COMPLETED:
        artifacts = [
            A2AArtifact(
                artifact_id=str(uuid.uuid4()),
                name="response",
                parts=[A2APart(text=accumulated_text)],
            )
        ]

    return A2ATask(
        id=task_id,
        context_id=task_id,
        status=A2ATaskStatus(state=state, timestamp=_now_iso()),
        artifacts=artifacts,
        metadata={"agent_name": name},
    )


@router.post("/a2a/agents/{name}/tasks/{task_id}:cancel", response_model=A2ATask)
async def cancel_task(name: str, task_id: str) -> A2ATask:
    """Cancel a running A2A task."""
    await _require_agent(name)
    principal = require_principal()

    from agentic_primitives_gateway.routes.agents import _bg as agent_bg

    owner = await agent_bg.get_owner_async(task_id)
    if owner and owner != principal.id and not principal.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden")

    cancelled = await agent_bg.cancel(task_id)
    state = A2ATaskState.CANCELED if cancelled else A2ATaskState.COMPLETED

    emit_audit_event(
        action=AuditAction.AGENT_RUN_CANCELLED,
        outcome=AuditOutcome.SUCCESS if cancelled else AuditOutcome.FAILURE,
        resource_type=ResourceType.AGENT,
        resource_id=name,
        metadata={"task_id": task_id, "source": "a2a"},
    )

    return A2ATask(
        id=task_id,
        context_id=task_id,
        status=A2ATaskStatus(state=state, timestamp=_now_iso()),
        metadata={"agent_name": name},
    )


@router.get("/a2a/agents/{name}/tasks/{task_id}:subscribe")
async def subscribe_task(name: str, task_id: str) -> StreamingResponse:
    """Subscribe to task updates via SSE with event replay."""
    await _require_agent(name)

    from agentic_primitives_gateway.routes.agents import _bg as agent_bg

    # Verify run ownership
    principal = require_principal()
    owner = await agent_bg.get_owner_async(task_id)
    if owner and owner != principal.id and not principal.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden")

    async def _generate() -> AsyncIterator[str]:
        context_id = task_id
        sent = 0
        idle_count = 0
        max_idle = 900  # 900 x 0.2s = 3 min

        while idle_count < max_idle:
            events = await agent_bg.get_events_async(task_id)
            status_str = await agent_bg.get_status_async(task_id)

            if len(events) > sent:
                for evt in events[sent:]:
                    if not isinstance(evt, dict):
                        continue
                    a2a_event = _translate_stream_event(evt, task_id, context_id)
                    if a2a_event:
                        yield f"data: {json.dumps(a2a_event)}\n\n"
                sent = len(events)
                idle_count = 0

                last_evt = events[-1] if events else {}
                if isinstance(last_evt, dict) and last_evt.get("type") in ("done", "cancelled"):
                    break
            else:
                idle_count += 1

            if status_str == "cancelled":
                break
            if status_str == "idle" and idle_count > 25:
                break

            await asyncio.sleep(0.2)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
