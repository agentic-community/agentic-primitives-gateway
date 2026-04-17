"""Admin-only audit viewer routes.

Reads the ``gateway:audit`` Redis stream written by
:class:`RedisStreamAuditSink`.  Three endpoints:

* ``GET /api/v1/audit/status`` — is the stream sink configured? what's the
  current stream length and MAXLEN?
* ``GET /api/v1/audit/events`` — paginated historical browse via
  ``XREVRANGE`` with in-process filtering.
* ``GET /api/v1/audit/events/stream`` — SSE live tail via ``XREAD`` with
  ``$`` so the connection only returns new events.

All three require the admin scope.  The enforcement middleware exempts
this subtree so operators without pre-loaded Cedar policies can still
reach the viewer (it's admin-gated server-side anyway).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from agentic_primitives_gateway.audit.emit import get_audit_router
from agentic_primitives_gateway.audit.models import AuditEvent, AuditOutcome, ResourceType
from agentic_primitives_gateway.audit.sinks.redis_stream import RedisStreamAuditSink
from agentic_primitives_gateway.routes._helpers import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/audit",
    tags=["audit"],
    dependencies=[Depends(require_admin)],
)

# Over-read factor: we pull up to N*5 raw entries per XRANGE call so that
# in-process filters (action, outcome, actor) rarely starve the page.
# Capped at 2500 to bound worst-case Redis bandwidth per request.
_OVERREAD_MULTIPLIER = 5
_OVERREAD_CAP = 2500

# XREAD block interval for the live stream.  Short enough that
# keepalive frames fire roughly once a second (proxies tend to close
# idle SSE connections around 30-60s).
_XREAD_BLOCK_MS = 1000


def _find_stream_sink() -> RedisStreamAuditSink | None:
    """Locate the first ``RedisStreamAuditSink`` on the installed router.

    Multi-sink deployments (prod + archive) are rare enough that we pick
    the first match today; revisit if operators need per-sink views.
    """
    router_obj = get_audit_router()
    if router_obj is None:
        return None
    for sink in router_obj.sinks:
        if isinstance(sink, RedisStreamAuditSink):
            return sink
    return None


def _match_event(
    event: AuditEvent,
    *,
    action: str | None,
    action_category: str | None,
    outcome: AuditOutcome | None,
    actor_id: str | None,
    resource_type: ResourceType | None,
    resource_id: str | None,
    correlation_id: str | None,
) -> bool:
    """In-process filter predicate — Redis streams have no native filtering."""
    if action is not None and event.action != action:
        return False
    if action_category is not None:
        category = event.action.split(".", 1)[0] if "." in event.action else event.action
        if category != action_category:
            return False
    if outcome is not None and event.outcome != outcome:
        return False
    if actor_id is not None and event.actor_id != actor_id:
        return False
    if resource_type is not None and event.resource_type != resource_type:
        return False
    if resource_id is not None and event.resource_id != resource_id:
        return False
    # A series of ``if mismatch: return False`` is more readable here than
    # collapsing the last check into a single boolean return.
    if correlation_id is not None and event.correlation_id != correlation_id:  # noqa: SIM103
        return False
    return True


def _parse_entry(entry_id: str, fields: dict[str, str]) -> AuditEvent | None:
    """Decode a single stream entry's ``event`` field.

    Drops entries that fail to parse — a malformed event on the stream
    (e.g. schema v1 reader meeting a v2 entry) should not 500 the list
    endpoint.  We log and skip.
    """
    raw = fields.get("event")
    if raw is None:
        return None
    try:
        return AuditEvent.model_validate_json(raw)
    except Exception:
        logger.warning("Audit stream entry %s failed to parse; skipping", entry_id)
        return None


@router.get("/status")
async def audit_status() -> dict[str, Any]:
    """Report whether the audit stream sink is configured and its size."""
    sink = _find_stream_sink()
    if sink is None:
        return {
            "stream_sink_configured": False,
            "stream_name": None,
            "length": None,
            "maxlen": None,
        }
    try:
        length = await sink.redis.xlen(sink.stream)
    except Exception:
        logger.exception("Audit status: xlen failed")
        length = None
    return {
        "stream_sink_configured": True,
        "stream_name": sink.stream,
        "length": length,
        "maxlen": sink.maxlen,
    }


@router.get("/events")
async def list_audit_events(
    start: str = Query("-", description="Stream ID to start from (inclusive). '-' = oldest, or an explicit XRANGE id."),
    end: str = Query("+", description="Stream ID to end at (inclusive). '+' = newest."),
    count: int = Query(100, ge=1, le=500, description="Max number of matching events to return."),
    action: str | None = None,
    action_category: str | None = None,
    outcome: AuditOutcome | None = None,
    actor_id: str | None = None,
    resource_type: ResourceType | None = None,
    resource_id: str | None = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Paginated historical browse backed by ``XREVRANGE``.

    Returns newest-first.  ``next`` is the oldest entry ID we inspected;
    pass it as ``end`` on the next request to continue paging backward.
    """
    sink = _find_stream_sink()
    if sink is None:
        raise HTTPException(
            status_code=409,
            detail="Audit stream sink is not configured. Enable 'redis_stream' in audit.sinks.",
        )

    batch = min(count * _OVERREAD_MULTIPLIER, _OVERREAD_CAP)
    try:
        entries: list[tuple[str, dict[str, str]]] = await sink.redis.xrevrange(
            sink.stream, max=end, min=start, count=batch
        )
    except Exception as exc:
        logger.exception("Audit list: xrevrange failed")
        raise HTTPException(status_code=503, detail=f"Audit stream read failed: {exc}") from None

    matched: list[dict[str, Any]] = []
    last_scanned_id: str | None = None
    for entry_id, fields in entries:
        last_scanned_id = entry_id
        event = _parse_entry(entry_id, fields)
        if event is None:
            continue
        if _match_event(
            event,
            action=action,
            action_category=action_category,
            outcome=outcome,
            actor_id=actor_id,
            resource_type=resource_type,
            resource_id=resource_id,
            correlation_id=correlation_id,
        ):
            matched.append(event.model_dump(mode="json"))
            if len(matched) >= count:
                break

    # ``next`` is the ID of the last entry we inspected — continuing from it
    # means "older than this" on the subsequent request.  None when the
    # stream is exhausted.
    next_cursor = last_scanned_id if len(entries) == batch else None
    return {"events": matched, "next": next_cursor, "scanned": len(entries)}


@router.get("/events/stream")
async def stream_audit_events(
    request: Request,
    action: str | None = None,
    action_category: str | None = None,
    outcome: AuditOutcome | None = None,
    actor_id: str | None = None,
    resource_type: ResourceType | None = None,
    resource_id: str | None = None,
    correlation_id: str | None = None,
) -> StreamingResponse:
    """SSE live tail — yields new events as they are XADD'd.

    Starts at ``$`` (strictly-new events only).  Emits comment frames as
    keepalives every poll interval so idle connections stay warm behind
    reverse proxies.
    """
    sink = _find_stream_sink()
    if sink is None:
        raise HTTPException(
            status_code=409,
            detail="Audit stream sink is not configured. Enable 'redis_stream' in audit.sinks.",
        )

    redis_client = sink.redis
    stream_name = sink.stream

    async def _generate() -> AsyncIterator[str]:
        last_id: str = "$"
        while True:
            if await request.is_disconnected():
                return
            try:
                resp = await redis_client.xread(
                    {stream_name: last_id},
                    count=100,
                    block=_XREAD_BLOCK_MS,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Audit SSE: xread failed")
                # Back off briefly so a persistent Redis outage doesn't
                # spin the event loop.
                await asyncio.sleep(1.0)
                continue

            if not resp:
                # No new entries this tick — emit a comment frame to keep
                # the SSE connection alive (ignored by clients).
                yield ": keepalive\n\n"
                continue

            for _stream, stream_entries in resp:
                for entry_id, fields in stream_entries:
                    last_id = entry_id
                    event = _parse_entry(entry_id, fields)
                    if event is None:
                        continue
                    if not _match_event(
                        event,
                        action=action,
                        action_category=action_category,
                        outcome=outcome,
                        actor_id=actor_id,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        correlation_id=correlation_id,
                    ):
                        continue
                    # Reuse the sink's JSON shape exactly (Pydantic's
                    # ``model_dump_json`` is compact + identical to what
                    # ``RedisStreamAuditSink`` writes) so consumers see
                    # the same serialization regardless of which endpoint
                    # they read from.
                    yield f"data: {event.model_dump_json()}\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
