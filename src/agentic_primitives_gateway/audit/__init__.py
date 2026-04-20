"""Audit subsystem — structured governance events with pluggable fan-out.

Emits strongly-typed ``AuditEvent`` records for authentication, policy,
credential, resource, and run-lifecycle activity.  Events are fanned out
through an ``AuditRouter`` to one or more ``AuditSink`` implementations
(stdout JSON, file, Redis stream, observability provider, noop).

Application logs and audit events are separate streams:

* Audit events flow through the router to configured sinks.
* Application logs (``logging.getLogger(...)``) remain for diagnostics.

The ``StdoutJsonSink`` is always enabled by default so operators who prefer
log-shipping (k8s → Fluent Bit → Loki/Datadog/SIEM) get a working stream
out of the box; additional sinks layer on top without replacing stdout.
"""

from __future__ import annotations

from agentic_primitives_gateway.audit.emit import emit_audit_event
from agentic_primitives_gateway.audit.models import (
    AuditAction,
    AuditEvent,
    AuditOutcome,
    ResourceType,
)
from agentic_primitives_gateway.audit.router import AuditRouter

__all__ = [
    "AuditAction",
    "AuditEvent",
    "AuditOutcome",
    "AuditRouter",
    "ResourceType",
    "emit_audit_event",
]
