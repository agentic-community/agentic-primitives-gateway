"""Observability provider sink — route audit events into the observability primitive.

Delegates to whichever :class:`ObservabilityProvider` the registry
currently resolves (Langfuse, AgentCore, etc.).  Audit events are
serialized to the same ``ingest_log`` shape the observability API
exposes publicly, so configuring this sink is a one-line opt-in that
sends governance signal to the same backend operators already use for
traces and scores.

Per-request credential overrides (``X-Cred-{Service}-*`` headers) still
work because the registry resolves the provider at call time, not at
sink construction.
"""

from __future__ import annotations

from typing import Any

from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.models import AuditEvent


class ObservabilityProviderSink(AuditSink):
    """Forward audit events to ``registry.observability.ingest_log``."""

    def __init__(self, *, name: str = "observability", **_: Any) -> None:
        self.name = name

    async def emit(self, event: AuditEvent) -> None:
        # Import at call time to avoid a startup-time circular import
        # (registry → metrics → audit.emit → audit.router → sinks).
        from agentic_primitives_gateway.registry import registry

        payload = event.model_dump(mode="json")
        await registry.observability.ingest_log(payload)
