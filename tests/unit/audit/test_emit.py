from __future__ import annotations

import hashlib

import pytest

from agentic_primitives_gateway.audit import emit as emit_module
from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.emit import (
    configure_redaction,
    emit_audit_event,
    set_audit_router,
)
from agentic_primitives_gateway.audit.models import AuditEvent, AuditOutcome, ResourceType
from agentic_primitives_gateway.audit.router import AuditRouter
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import (
    set_authenticated_principal,
    set_correlation_id,
    set_request_id,
)


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
    # Ensure tests don't leak configuration between each other.
    set_audit_router(None)
    configure_redaction(extra_redact_keys=(), redact_principal_id=False)
    set_authenticated_principal(None)
    set_request_id("")
    set_correlation_id("")
    yield
    set_audit_router(None)
    configure_redaction(extra_redact_keys=(), redact_principal_id=False)
    set_authenticated_principal(None)
    set_request_id("")
    set_correlation_id("")


def test_emit_without_router_is_no_op():
    # Should not raise.
    emit_audit_event("x", AuditOutcome.SUCCESS)
    assert emit_module.get_audit_router() is None


@pytest.mark.asyncio
async def test_emit_fills_contextvars(router_fixture):
    router, sink = router_fixture
    await router.start()
    set_audit_router(router)
    set_request_id("req-abc")
    set_correlation_id("corr-xyz")
    set_authenticated_principal(
        AuthenticatedPrincipal(
            id="alice",
            type="user",
            groups=frozenset({"admins", "beta"}),
        )
    )
    try:
        emit_audit_event(
            "auth.success",
            AuditOutcome.SUCCESS,
            http_method="GET",
            http_path="/api/v1/memory/ns1",
        )
        import asyncio

        await asyncio.sleep(0.02)
    finally:
        await router.shutdown(timeout=1.0)

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.request_id == "req-abc"
    assert event.correlation_id == "corr-xyz"
    assert event.actor_id == "alice"
    assert event.actor_type == "user"
    assert sorted(event.actor_groups) == ["admins", "beta"]
    assert event.http_path == "/api/v1/memory/ns1"


@pytest.mark.asyncio
async def test_emit_redacts_metadata_by_default(router_fixture):
    router, sink = router_fixture
    await router.start()
    set_audit_router(router)
    try:
        emit_audit_event(
            "credential.write",
            AuditOutcome.SUCCESS,
            metadata={"token": "super-secret", "keys": ["apg.langfuse.public_key"]},
        )
        import asyncio

        await asyncio.sleep(0.02)
    finally:
        await router.shutdown(timeout=1.0)

    assert sink.events[0].metadata["token"] == "***"
    assert sink.events[0].metadata["keys"] == ["apg.langfuse.public_key"]


@pytest.mark.asyncio
async def test_redact_principal_id_hashes(router_fixture):
    router, sink = router_fixture
    await router.start()
    set_audit_router(router)
    configure_redaction(redact_principal_id=True)
    set_authenticated_principal(AuthenticatedPrincipal(id="alice@example.com", type="user"))
    try:
        emit_audit_event("auth.success", AuditOutcome.SUCCESS)
        import asyncio

        await asyncio.sleep(0.02)
    finally:
        await router.shutdown(timeout=1.0)

    expected = hashlib.sha256(b"alice@example.com").hexdigest()[:16]
    assert sink.events[0].actor_id == expected
    assert sink.events[0].actor_id != "alice@example.com"


@pytest.mark.asyncio
async def test_string_resource_type_coerced_to_enum(router_fixture):
    router, sink = router_fixture
    await router.start()
    set_audit_router(router)
    try:
        emit_audit_event(
            "agent.create",
            "success",
            resource_type="agent",
            resource_id="pirate",
        )
        import asyncio

        await asyncio.sleep(0.02)
    finally:
        await router.shutdown(timeout=1.0)

    assert sink.events[0].resource_type == ResourceType.AGENT
    assert sink.events[0].outcome == AuditOutcome.SUCCESS
