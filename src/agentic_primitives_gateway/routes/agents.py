from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from agentic_primitives_gateway.agents.runner import AgentRunner
from agentic_primitives_gateway.agents.store import AgentStore
from agentic_primitives_gateway.context import set_provider_overrides
from agentic_primitives_gateway.models.agents import (
    AgentListResponse,
    AgentSpec,
    ChatRequest,
    ChatResponse,
    CreateAgentRequest,
    UpdateAgentRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/agents", tags=["agents"])

_store: AgentStore | None = None
_runner = AgentRunner()


def set_agent_store(store: AgentStore) -> None:
    """Set the module-level agent store (called during app lifespan)."""
    global _store
    _store = store


def _get_store() -> AgentStore:
    if _store is None:
        raise RuntimeError("Agent store not initialized")
    return _store


@router.post("", response_model=AgentSpec, status_code=201)
async def create_agent(request: CreateAgentRequest) -> AgentSpec:
    store = _get_store()
    spec = AgentSpec(**request.model_dump())
    existing = await store.get(spec.name)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Agent '{spec.name}' already exists")
    return await store.create(spec)


@router.get("", response_model=AgentListResponse)
async def list_agents() -> AgentListResponse:
    store = _get_store()
    agents = await store.list()
    return AgentListResponse(agents=agents)


@router.get("/{name}", response_model=AgentSpec)
async def get_agent(name: str) -> AgentSpec:
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    return spec


@router.put("/{name}", response_model=AgentSpec)
async def update_agent(name: str, request: UpdateAgentRequest) -> AgentSpec:
    store = _get_store()
    existing = await store.get(name)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    return await store.update(name, updates)


@router.delete("/{name}")
async def delete_agent(name: str) -> dict[str, str]:
    store = _get_store()
    deleted = await store.delete(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    return {"status": "deleted"}


@router.post("/{name}/chat", response_model=ChatResponse)
async def chat_with_agent(name: str, request: ChatRequest) -> ChatResponse:
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    # Apply agent-level provider overrides to the current request context
    if spec.provider_overrides:
        set_provider_overrides(spec.provider_overrides)

    return await _runner.run(
        spec=spec,
        message=request.message,
        session_id=request.session_id,
    )
