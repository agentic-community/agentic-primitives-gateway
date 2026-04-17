from __future__ import annotations

import json

import pytest

from agentic_primitives_gateway.audit.models import AuditAction, AuditEvent, AuditOutcome
from agentic_primitives_gateway.audit.sinks.file import RotatingFileAuditSink


@pytest.mark.asyncio
async def test_writes_json_line_per_event(tmp_path):
    path = tmp_path / "audit.log"
    sink = RotatingFileAuditSink(path=str(path))
    try:
        await sink.emit(AuditEvent(action=AuditAction.AUTH_SUCCESS, outcome=AuditOutcome.SUCCESS))
        await sink.emit(AuditEvent(action=AuditAction.POLICY_ALLOW, outcome=AuditOutcome.ALLOW))
    finally:
        await sink.close()

    lines = path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["action"] == "auth.success"
    assert json.loads(lines[1])["action"] == "policy.allow"


@pytest.mark.asyncio
async def test_rotation_creates_backup(tmp_path):
    path = tmp_path / "audit.log"
    # Each serialized event is ~250 bytes; max_bytes=500 forces rotation after ~2 events.
    sink = RotatingFileAuditSink(path=str(path), max_bytes=500, backup_count=3)
    try:
        for _ in range(6):
            await sink.emit(AuditEvent(action=AuditAction.HTTP_REQUEST, outcome=AuditOutcome.SUCCESS))
    finally:
        await sink.close()

    # Primary file exists; at least one backup should have been created.
    assert path.exists()
    backups = list(tmp_path.glob("audit.log.*"))
    assert backups, "expected at least one rotated backup"


@pytest.mark.asyncio
async def test_creates_parent_directory(tmp_path):
    path = tmp_path / "nested" / "dir" / "audit.log"
    sink = RotatingFileAuditSink(path=str(path))
    try:
        await sink.emit(AuditEvent(action="x", outcome=AuditOutcome.SUCCESS))
    finally:
        await sink.close()
    assert path.exists()
