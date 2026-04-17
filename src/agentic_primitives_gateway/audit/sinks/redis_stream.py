"""Redis Streams sink — durable cross-replica audit log.

Each event becomes a Redis Stream entry via ``XADD``.  ``MAXLEN`` caps
the stream so the memory footprint stays bounded without an external
trimmer.  Downstream consumers (SIEM shipper, dashboards, etc.) can
read the stream with ``XREAD`` or ``XREADGROUP``.

This sink is optional — it requires the ``redis`` optional dependency.
The router isolates failures per-sink, so a broken Redis connection
will produce ``gateway_audit_sink_events_total{sink=...,outcome=error}``
increments but won't affect other sinks or the request path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.models import AuditEvent

if TYPE_CHECKING:
    import redis.asyncio as redis_asyncio


class RedisStreamAuditSink(AuditSink):
    """Push events to a Redis Stream with bounded retention."""

    def __init__(
        self,
        *,
        name: str = "redis_stream",
        redis_url: str = "redis://localhost:6379/0",
        stream: str = "gateway:audit",
        maxlen: int = 100_000,
        approximate: bool = True,
        **_: Any,
    ) -> None:
        # Import lazily so installations without the ``redis`` extra can
        # still import the rest of the audit subsystem without error.
        try:
            import redis.asyncio as redis_asyncio
        except ImportError as exc:
            raise ImportError(
                "RedisStreamAuditSink requires the 'redis' optional dependency. "
                "Install with: pip install 'agentic-primitives-gateway[redis]'"
            ) from exc

        self.name = name
        self._stream = stream
        self._maxlen = maxlen
        self._approximate = approximate
        self._redis = redis_asyncio.from_url(redis_url, decode_responses=True)

    # Public accessors — used by the audit UI routes to read from the
    # same stream the sink writes to.  redis-py async clients are
    # pool-based, so a long-running ``xread`` on the shared client does
    # not block concurrent ``xadd`` calls from the sink's worker.

    @property
    def stream(self) -> str:
        """Redis stream key this sink writes to."""
        return self._stream

    @property
    def redis(self) -> redis_asyncio.Redis:
        """The underlying async Redis client."""
        return self._redis

    @property
    def maxlen(self) -> int:
        """Configured MAXLEN bound for the stream."""
        return self._maxlen

    async def emit(self, event: AuditEvent) -> None:
        # Serialize the event as a single JSON field rather than flattening
        # to a field-map: keeps the stream entry small, preserves nested
        # metadata exactly, and lets consumers use one Pydantic round-trip.
        await self._redis.xadd(
            name=self._stream,
            fields={"event": event.model_dump_json()},
            maxlen=self._maxlen,
            approximate=self._approximate,
        )

    async def close(self) -> None:
        await self._redis.aclose()
