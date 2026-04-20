"""End-to-end: agent + team CRUD routes emit audit events."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from agentic_primitives_gateway.agents.store import FileAgentStore
from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.emit import set_audit_router
from agentic_primitives_gateway.audit.models import AuditAction, AuditEvent
from agentic_primitives_gateway.audit.router import AuditRouter
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.routes.agents import set_agent_store


class _CollectorSink(AuditSink):
    def __init__(self) -> None:
        self.name = "collector"
        self.events: list[AuditEvent] = []

    async def emit(self, event: AuditEvent) -> None:
        self.events.append(event)


@pytest.fixture
async def audit_client(tmp_path):
    # Wire up a clean agent store so POST /api/v1/agents works.
    store = FileAgentStore(path=str(tmp_path / "agents.json"))
    set_agent_store(store)

    sink = _CollectorSink()
    router = AuditRouter([sink])
    await router.start()
    set_audit_router(router)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, sink
    finally:
        await router.shutdown(timeout=1.0)
        set_audit_router(None)


@pytest.mark.asyncio
async def test_agent_create_update_delete_emits(audit_client):
    client, sink = audit_client

    create_body = {
        "name": "audit-test-agent",
        "model": "test-model",
        "system_prompt": "test",
        "primitives": {"memory": {"enabled": False}},
    }
    resp = await client.post("/api/v1/agents", json=create_body)
    assert resp.status_code == 201

    resp = await client.put("/api/v1/agents/audit-test-agent", json={"description": "updated"})
    assert resp.status_code == 200

    resp = await client.delete("/api/v1/agents/audit-test-agent")
    assert resp.status_code == 200

    await asyncio.sleep(0.05)

    # resource_id is the qualified identity ``"{owner}:{name}"`` under the
    # versioned store; the test fixture uses the noop auth principal ``"noop"``.
    qualified = "noop:audit-test-agent"
    seen = {e.action for e in sink.events if e.resource_id == qualified}
    assert AuditAction.AGENT_CREATE in seen
    assert AuditAction.AGENT_UPDATE in seen
    assert AuditAction.AGENT_DELETE in seen


@pytest.mark.asyncio
async def test_agent_create_event_carries_model_metadata(audit_client):
    client, sink = audit_client

    resp = await client.post(
        "/api/v1/agents",
        json={
            "name": "audit-test-agent-2",
            "model": "my-special-model",
            "system_prompt": "test",
            "primitives": {"memory": {"enabled": False}},
        },
    )
    assert resp.status_code == 201
    await asyncio.sleep(0.05)

    try:
        matches = [
            e
            for e in sink.events
            if e.action == AuditAction.AGENT_CREATE and e.resource_id == "noop:audit-test-agent-2"
        ]
        assert matches, "expected agent.create event"
        assert matches[0].metadata["model"] == "my-special-model"
    finally:
        await client.delete("/api/v1/agents/audit-test-agent-2")
