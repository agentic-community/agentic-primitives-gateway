"""tool.call audit events + gateway_tool_calls_total metric."""

from __future__ import annotations

import asyncio

import pytest

from agentic_primitives_gateway.agents.tools.catalog import ToolDefinition, execute_tool
from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.emit import set_audit_router
from agentic_primitives_gateway.audit.models import AuditAction, AuditEvent, AuditOutcome
from agentic_primitives_gateway.audit.router import AuditRouter


class _CollectorSink(AuditSink):
    def __init__(self) -> None:
        self.name = "collector"
        self.events: list[AuditEvent] = []

    async def emit(self, event: AuditEvent) -> None:
        self.events.append(event)


@pytest.fixture
async def audit_router():
    sink = _CollectorSink()
    router = AuditRouter([sink])
    await router.start()
    set_audit_router(router)
    try:
        yield sink
    finally:
        await router.shutdown(timeout=1.0)
        set_audit_router(None)


def _make_tool(
    name: str,
    handler,  # type: ignore[no-untyped-def]
    properties: dict | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="test",
        input_schema={
            "type": "object",
            # ``execute_tool`` filters ``tool_input`` to declared
            # properties (anti-override guard); tests that want to pass
            # kwargs must declare them here.
            "properties": properties or {},
        },
        primitive="memory",
        handler=handler,
    )


@pytest.mark.asyncio
async def test_successful_tool_call_emits_success_event(audit_router):
    async def handler(x: int = 0) -> str:
        return f"ok:{x}"

    tool = _make_tool("do_thing", handler, properties={"x": {"type": "integer"}})
    result = await execute_tool("do_thing", {"x": 42}, [tool])
    assert result == "ok:42"

    await asyncio.sleep(0.02)
    tool_events = [e for e in audit_router.events if e.action == AuditAction.TOOL_CALL]
    assert len(tool_events) == 1
    event = tool_events[0]
    assert event.outcome == AuditOutcome.SUCCESS
    assert event.resource_id == "do_thing"
    assert event.metadata["tool_name"] == "do_thing"
    assert event.metadata["primitive"] == "memory"
    assert event.duration_ms is not None and event.duration_ms >= 0
    # Never logs the input (could carry secrets passed by the LLM).
    assert "x" not in event.metadata
    assert "42" not in str(event.metadata)


@pytest.mark.asyncio
async def test_failing_tool_call_emits_failure_event_with_error_type(audit_router):
    async def handler() -> str:
        raise RuntimeError("kapow")

    tool = _make_tool("boom", handler)
    with pytest.raises(RuntimeError):
        await execute_tool("boom", {}, [tool])

    await asyncio.sleep(0.02)
    tool_events = [e for e in audit_router.events if e.action == AuditAction.TOOL_CALL]
    assert len(tool_events) == 1
    event = tool_events[0]
    assert event.outcome == AuditOutcome.FAILURE
    assert event.metadata["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_unknown_tool_does_not_emit(audit_router):
    with pytest.raises(ValueError, match="Unknown tool"):
        await execute_tool("nope", {}, [])
    await asyncio.sleep(0.02)
    assert not any(e.action == AuditAction.TOOL_CALL for e in audit_router.events)


@pytest.mark.asyncio
async def test_tool_calls_metric_increments(audit_router):
    from agentic_primitives_gateway import metrics

    async def ok() -> str:
        return "x"

    async def bad() -> str:
        raise ValueError("no")

    start_success = metrics.TOOL_CALLS.labels(tool_name="ok_tool", status="success")._value.get()
    start_failure = metrics.TOOL_CALLS.labels(tool_name="bad_tool", status="failure")._value.get()

    await execute_tool("ok_tool", {}, [_make_tool("ok_tool", ok)])
    with pytest.raises(ValueError):
        await execute_tool("bad_tool", {}, [_make_tool("bad_tool", bad)])

    assert metrics.TOOL_CALLS.labels(tool_name="ok_tool", status="success")._value.get() == start_success + 1
    assert metrics.TOOL_CALLS.labels(tool_name="bad_tool", status="failure")._value.get() == start_failure + 1
