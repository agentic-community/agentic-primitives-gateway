from __future__ import annotations

import pytest
from fastapi import HTTPException

from agentic_primitives_gateway.audit import emit as emit_module
from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.emit import set_audit_router
from agentic_primitives_gateway.audit.models import AuditAction, AuditEvent
from agentic_primitives_gateway.audit.router import AuditRouter
from agentic_primitives_gateway.auth.access import require_access, require_owner_or_admin
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal


class _CollectorSink(AuditSink):
    def __init__(self) -> None:
        self.name = "collector"
        self.events: list[AuditEvent] = []

    async def emit(self, event: AuditEvent) -> None:
        self.events.append(event)


@pytest.fixture
async def router_fixture():
    sink = _CollectorSink()
    router = AuditRouter([sink])
    await router.start()
    set_audit_router(router)
    try:
        yield router, sink
    finally:
        await router.shutdown(timeout=1.0)
        set_audit_router(None)
        emit_module.configure_redaction()


@pytest.mark.asyncio
async def test_require_access_emits_denial_event(router_fixture):
    _router, sink = router_fixture
    import asyncio

    alice = AuthenticatedPrincipal(id="alice", type="user")
    with pytest.raises(HTTPException) as exc:
        require_access(alice, resource_owner="bob", resource_shared_with=[], resource_type="agent")
    assert exc.value.status_code == 403

    await asyncio.sleep(0.02)
    denials = [e for e in sink.events if e.action == AuditAction.RESOURCE_ACCESS_DENIED]
    assert len(denials) == 1
    assert denials[0].reason == "not_shared"
    assert denials[0].metadata["resource_owner"] == "bob"
    assert denials[0].metadata["resource_type_hint"] == "agent"


@pytest.mark.asyncio
async def test_require_owner_or_admin_emits_denial_event(router_fixture):
    _router, sink = router_fixture
    import asyncio

    alice = AuthenticatedPrincipal(id="alice", type="user")
    with pytest.raises(HTTPException):
        require_owner_or_admin(alice, resource_owner="bob", resource_type="agent")

    await asyncio.sleep(0.02)
    denials = [e for e in sink.events if e.action == AuditAction.RESOURCE_ACCESS_DENIED]
    assert len(denials) == 1
    assert denials[0].reason == "not_owner"


@pytest.mark.asyncio
async def test_owner_access_does_not_emit_denial(router_fixture):
    _router, sink = router_fixture
    import asyncio

    alice = AuthenticatedPrincipal(id="alice", type="user")
    # Not supposed to raise — alice owns the resource.
    require_owner_or_admin(alice, resource_owner="alice")

    await asyncio.sleep(0.02)
    assert not any(e.action == AuditAction.RESOURCE_ACCESS_DENIED for e in sink.events)


@pytest.mark.asyncio
async def test_unknown_resource_type_still_recorded(router_fixture):
    _router, sink = router_fixture
    import asyncio

    alice = AuthenticatedPrincipal(id="alice", type="user")
    with pytest.raises(HTTPException):
        require_access(alice, resource_owner="bob", resource_shared_with=[], resource_type="widget")

    await asyncio.sleep(0.02)
    denials = [e for e in sink.events if e.action == AuditAction.RESOURCE_ACCESS_DENIED]
    assert len(denials) == 1
    # Unknown resource types map to resource_type=None but keep the hint.
    assert denials[0].resource_type is None
    assert denials[0].metadata["resource_type_hint"] == "widget"
