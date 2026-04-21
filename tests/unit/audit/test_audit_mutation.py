"""Tests for the ``audit_mutation`` async context manager helper."""

from __future__ import annotations

import asyncio

import pytest

from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.emit import (
    audit_mutation,
    configure_redaction,
    set_audit_router,
)
from agentic_primitives_gateway.audit.models import AuditEvent, AuditOutcome, ResourceType
from agentic_primitives_gateway.audit.router import AuditRouter


class _CollectorSink(AuditSink):
    def __init__(self) -> None:
        self.name = "collector"
        self.events: list[AuditEvent] = []

    async def emit(self, event: AuditEvent) -> None:
        self.events.append(event)


@pytest.fixture
def router_fixture():
    sink = _CollectorSink()
    router = AuditRouter([sink])
    return router, sink


@pytest.fixture(autouse=True)
def reset_module_state():
    set_audit_router(None)
    configure_redaction(extra_redact_keys=(), redact_principal_id=False)
    yield
    set_audit_router(None)


@pytest.mark.asyncio
async def test_audit_mutation_emits_success_on_clean_exit(router_fixture):
    router, sink = router_fixture
    await router.start()
    set_audit_router(router)
    try:
        async with audit_mutation(
            "memory.resource.create",
            resource_type=ResourceType.MEMORY,
        ) as audit:
            audit.resource_id = "mem-123"
            audit.metadata = {"name": "scratch"}
        await asyncio.sleep(0.02)
    finally:
        await router.shutdown(timeout=1.0)

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.action == "memory.resource.create"
    assert event.outcome == AuditOutcome.SUCCESS
    assert event.resource_id == "mem-123"
    assert event.metadata == {"name": "scratch"}
    assert event.duration_ms is not None and event.duration_ms >= 0


@pytest.mark.asyncio
async def test_audit_mutation_emits_failure_and_reraises(router_fixture):
    router, sink = router_fixture
    await router.start()
    set_audit_router(router)
    try:
        with pytest.raises(RuntimeError, match="boom"):
            async with audit_mutation(
                "agent.version.create",
                resource_type=ResourceType.AGENT,
                resource_id="alice:researcher",
            ):
                raise RuntimeError("boom")
        await asyncio.sleep(0.02)
    finally:
        await router.shutdown(timeout=1.0)

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.outcome == AuditOutcome.FAILURE
    assert event.resource_id == "alice:researcher"
    assert event.metadata["error_type"] == "RuntimeError"
    assert event.duration_ms is not None and event.duration_ms >= 0


@pytest.mark.asyncio
async def test_audit_mutation_metadata_refinement_survives_exception(router_fixture):
    router, sink = router_fixture
    await router.start()
    set_audit_router(router)
    try:
        with pytest.raises(ValueError):
            async with audit_mutation(
                "team.version.create",
                resource_type=ResourceType.TEAM,
            ) as audit:
                audit.metadata["trace_id"] = "t-42"
                raise ValueError("bad payload")
        await asyncio.sleep(0.02)
    finally:
        await router.shutdown(timeout=1.0)

    event = sink.events[0]
    assert event.metadata["trace_id"] == "t-42"
    assert event.metadata["error_type"] == "ValueError"
