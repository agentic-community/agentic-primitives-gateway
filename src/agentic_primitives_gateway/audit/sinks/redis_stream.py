"""Redis Streams sink — durable cross-replica audit log.

Each event becomes a Redis Stream entry via ``XADD``.  ``MAXLEN`` caps
the stream so the memory footprint stays bounded without an external
trimmer.  Downstream consumers (SIEM shipper, dashboards, etc.) can
read the stream with ``XREAD`` or ``XREADGROUP``.

This sink is optional — it requires the ``redis`` optional dependency.
The router isolates failures per-sink, so a broken Redis connection
will produce ``gateway_audit_sink_events_total{sink=...,outcome=error}``
increments but won't affect other sinks or the request path.

Implements :class:`AuditReader` so the admin UI routes can query the
same stream the sink writes to.  A future Postgres or object-store sink
can implement the same protocol without changing the route layer.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.models import AuditEvent

logger = logging.getLogger(__name__)

# XREAD block interval for the live tail.  Short enough that proxies
# don't idle-close the SSE connection; long enough to avoid busy-waiting.
_XREAD_BLOCK_MS = 1000


class RedisStreamAuditSink(AuditSink):
    """Push events to a Redis Stream with bounded retention.

    Also implements the :class:`AuditReader` protocol
    (``count`` / ``list_events`` / ``tail``) so the admin UI can read
    from the same stream.  redis-py async clients are pool-based, so a
    long-running ``xread`` on the shared client does not block
    concurrent ``xadd`` calls from the sink's writer worker.
    """

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

    @property
    def stream(self) -> str:
        """Redis stream key this sink writes to."""
        return self._stream

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

    # ── AuditReader protocol ───────────────────────────────────────

    def describe(self) -> dict[str, Any]:
        """Backend metadata surfaced by ``GET /api/v1/audit/status``."""
        return {
            "backend": "redis_stream",
            "stream_name": self._stream,
            "maxlen": self._maxlen,
        }

    async def count(self) -> int | None:
        """Number of retained entries in the stream (``XLEN``)."""
        try:
            return int(await self._redis.xlen(self._stream))
        except Exception:
            logger.exception("RedisStreamAuditSink.count: xlen failed")
            return None

    async def list_events(
        self,
        *,
        start: str,
        end: str,
        count: int,
    ) -> tuple[list[AuditEvent], str | None]:
        """Newest-first page of events via ``XREVRANGE``.

        ``start`` / ``end`` are Redis stream IDs; ``"-"`` and ``"+"``
        mean "oldest" and "newest".  Returns ``(events, next_cursor)``
        where ``next_cursor`` is the ID of the last entry inspected
        (pass as ``end`` on the next call to continue paging backward),
        or ``None`` when the stream is exhausted.
        """
        entries: list[tuple[str, dict[str, str]]] = await self._redis.xrevrange(
            self._stream, max=end, min=start, count=count
        )
        events: list[AuditEvent] = []
        last_id: str | None = None
        for entry_id, fields in entries:
            last_id = entry_id
            event = _parse_entry(entry_id, fields)
            if event is not None:
                events.append(event)
        next_cursor = last_id if len(entries) == count else None
        return events, next_cursor

    async def tail(self) -> AsyncIterator[AuditEvent | None]:
        """Async generator yielding new events as they are XADD'd.

        Starts at ``$`` (strictly-new entries only).  Yields ``None`` as
        a keepalive tick when ``XREAD`` returns empty — the caller can
        use it to emit SSE keepalive frames and check for client
        disconnect without busy-waiting.
        """
        last_id: str = "$"
        while True:
            try:
                resp = await self._redis.xread(
                    {self._stream: last_id},
                    count=100,
                    block=_XREAD_BLOCK_MS,
                )
            except Exception:
                logger.exception("RedisStreamAuditSink.tail: xread failed")
                # Back off briefly so a persistent Redis outage doesn't
                # spin the event loop.
                import asyncio

                await asyncio.sleep(1.0)
                continue

            if not resp:
                yield None  # keepalive tick
                continue

            for _stream, stream_entries in resp:
                for entry_id, fields in stream_entries:
                    last_id = entry_id
                    event = _parse_entry(entry_id, fields)
                    if event is not None:
                        yield event


def _parse_entry(entry_id: str, fields: dict[str, str]) -> AuditEvent | None:
    """Decode a single stream entry's ``event`` field, skipping malformed rows."""
    raw = fields.get("event")
    if raw is None:
        return None
    try:
        return AuditEvent.model_validate_json(raw)
    except Exception:
        logger.warning("Audit stream entry %s failed to parse; skipping", entry_id)
        return None
