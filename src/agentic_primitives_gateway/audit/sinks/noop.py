"""Noop audit sink — discards events.  Used in tests and when audit is disabled."""

from __future__ import annotations

from typing import Any

from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.models import AuditEvent


class NoopAuditSink(AuditSink):
    """Drops every event.  Counts toward metrics but emits nothing."""

    def __init__(self, *, name: str = "noop", **_: Any) -> None:
        self.name = name

    async def emit(self, event: AuditEvent) -> None:
        return None
