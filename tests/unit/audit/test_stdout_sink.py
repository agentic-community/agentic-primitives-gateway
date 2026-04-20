from __future__ import annotations

import io
import json

import pytest

from agentic_primitives_gateway.audit.models import AuditAction, AuditEvent, AuditOutcome
from agentic_primitives_gateway.audit.sinks.noop import NoopAuditSink
from agentic_primitives_gateway.audit.sinks.stdout_json import StdoutJsonSink


@pytest.mark.asyncio
async def test_stdout_json_sink_writes_one_line_per_event():
    buffer = io.StringIO()
    sink = StdoutJsonSink(name="test_stdout", stream=buffer)
    await sink.emit(AuditEvent(action=AuditAction.AUTH_SUCCESS, outcome=AuditOutcome.SUCCESS))
    await sink.emit(AuditEvent(action=AuditAction.POLICY_DENY, outcome=AuditOutcome.DENY))

    lines = buffer.getvalue().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["action"] == "auth.success"
    assert first["outcome"] == "success"
    second = json.loads(lines[1])
    assert second["action"] == "policy.deny"
    assert second["outcome"] == "deny"


@pytest.mark.asyncio
async def test_stdout_json_sink_emits_schema_version_and_timestamp():
    buffer = io.StringIO()
    sink = StdoutJsonSink(stream=buffer)
    await sink.emit(AuditEvent(action="x", outcome=AuditOutcome.SUCCESS))
    payload = json.loads(buffer.getvalue())
    assert payload["schema_version"] == "1"
    assert "timestamp" in payload


@pytest.mark.asyncio
async def test_noop_sink_accepts_events():
    sink = NoopAuditSink()
    await sink.emit(AuditEvent(action="x", outcome=AuditOutcome.SUCCESS))
    assert sink.name == "noop"
