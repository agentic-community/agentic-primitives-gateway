"""Abstract base class for audit sinks + optional reader protocol.

Each sink consumes ``AuditEvent`` instances from its own queue driven by
an :class:`AuditRouter` worker task.  Implementations should be
non-blocking where possible; the router enforces a per-call timeout and
isolates failures so a slow or broken sink does not hold up others.

Writing and reading are intentionally split.  The :class:`AuditSink` ABC
owns write semantics — every backend implements ``emit``.  The
:class:`AuditReader` protocol is *optional*: sinks whose backing store
can be queried (Redis Streams today; potentially Postgres, SQLite, or
an object-store index tomorrow) implement it and the UI routes use
them.  Write-only sinks (``stdout_json``, ``file``, ``noop``, the
observability-provider proxy) simply do not implement the reader —
their corresponding audit consumer is whatever external system they
ship to (SIEM, Loki, Langfuse trace explorer, etc.).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

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


@runtime_checkable
class AuditReader(Protocol):
    """Optional protocol for sinks whose backing store supports read-back.

    Implemented by sinks whose store can be queried in-process — today
    only :class:`RedisStreamAuditSink`, but structured to let a future
    Postgres/SQLite/DynamoDB-backed sink plug in without changing any
    route code.

    The shape is intentionally narrow:

    * ``describe()`` — backend-specific metadata the UI surfaces on the
      status panel (e.g. stream name + MAXLEN for Redis; table name +
      retention for a SQL sink).  Keys are backend-defined and
      serialized as-is; the UI treats unknown keys as informational.
    * ``count()`` — current number of retained events (``XLEN`` equivalent).
      Returns ``None`` when the backend doesn't expose a count cheaply.
    * ``list_events()`` — newest-first pagination window.  Backends with
      opaque cursors (Redis stream IDs, Postgres ``id < ?``, etc.)
      interpret ``start`` / ``end`` however their storage requires;
      ``"-"`` and ``"+"`` are reserved sentinels meaning "oldest" and
      "newest".  Returns ``(events, next_cursor)`` where ``next_cursor``
      is the ID of the last entry inspected (to continue paging backward)
      or ``None`` when the window was exhausted.
    * ``tail()`` — async generator yielding newly-written events as
      they land.  No backfill.  Yields ``None`` as a keepalive tick
      (no new events since the last yield); the caller uses this to
      emit SSE keepalive frames, check cancellation, or probe for
      client disconnect without busy-waiting.  Lifetime follows the
      generator — cancelling it closes the underlying connection.

    Post-filtering (action/outcome/actor/correlation) is intentionally
    NOT part of the contract — it operates on ``AuditEvent`` fields and
    is backend-independent, so it lives in the route layer once.
    """

    def describe(self) -> dict[str, Any]: ...

    async def count(self) -> int | None: ...

    async def list_events(
        self,
        *,
        start: str,
        end: str,
        count: int,
    ) -> tuple[list[AuditEvent], str | None]: ...

    def tail(self) -> AsyncIterator[AuditEvent | None]: ...
