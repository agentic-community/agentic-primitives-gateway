"""Abstract base class for audit sinks.

Each sink consumes ``AuditEvent`` instances from its own queue driven by
an :class:`AuditRouter` worker task.  Implementations should be
non-blocking where possible; the router enforces a per-call timeout and
isolates failures so a slow or broken sink does not hold up others.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from agentic_primitives_gateway.audit.models import AuditEvent


class AuditSink(ABC):
    """A single destination for audit events.

    Attributes:
        name: Stable identifier used in metric labels.  Set by the
            router from the config entry; subclasses may provide a
            default via ``__init__``.
    """

    name: str = "unnamed"

    @abstractmethod
    async def emit(self, event: AuditEvent) -> None:
        """Persist or forward a single audit event."""
        ...

    async def flush(self) -> None:  # noqa: B027
        """Flush any buffered state.  Default is a no-op."""

    async def close(self) -> None:  # noqa: B027
        """Release resources.  Called once on shutdown after drain."""
