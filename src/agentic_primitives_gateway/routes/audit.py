"""Admin-only audit viewer routes.

Backed by any :class:`AuditReader` on the configured audit router
(today only :class:`RedisStreamAuditSink`; future Postgres / SQLite /
object-store sinks can implement the same protocol without changes
here).  Three endpoints:

* ``GET /api/v1/audit/status`` — is a reader configured?  Backend
  metadata (stream name, MAXLEN, etc.) surfaced verbatim from
  ``reader.describe()``.
* ``GET /api/v1/audit/events`` — paginated historical browse via
  ``reader.list_events()`` with in-process filtering.
* ``GET /api/v1/audit/events/stream`` — SSE live tail via
  ``reader.tail()`` which yields new events as they land plus a
  keepalive tick when the backend is idle.

All three require the admin scope.  The enforcement middleware exempts
this subtree so operators without pre-loaded Cedar policies can still
reach the viewer (it's admin-gated server-side anyway).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from agentic_primitives_gateway.audit.base import AuditReader
from agentic_primitives_gateway.audit.emit import get_audit_router
from agentic_primitives_gateway.audit.models import AuditEvent, AuditOutcome, ResourceType
from agentic_primitives_gateway.routes._helpers import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/audit",
    tags=["audit"],
    dependencies=[Depends(require_admin)],
)

# Over-read factor: pull up to N*5 raw entries per reader call so
# in-process filters (action, outcome, actor) rarely starve the page.
# Capped at 2500 to bound worst-case bandwidth per request.  If a single
# batch doesn't yield ``count`` matches, we loop (advancing the cursor)
# up to ``_MAX_SCAN`` total events — protects rare-outcome filters from
# returning almost-empty pages without letting a pathological filter
# scan the whole stream.
_OVERREAD_MULTIPLIER = 5
_OVERREAD_CAP = 2500
_MAX_SCAN = 10_000


def _find_reader() -> AuditReader | None:
    """First sink on the installed router that implements :class:`AuditReader`.

    Multi-reader deployments (prod stream + archive DB, say) are rare
    enough that we pick the first match today; revisit if operators need
    per-reader views.
    """
    router_obj = get_audit_router()
    if router_obj is None:
        return None
    for sink in router_obj.sinks:
        if isinstance(sink, AuditReader):
            return sink
    return None


def _match_event(
    event: AuditEvent,
    *,
    action: str | None,
    action_category: str | None,
    outcome: list[AuditOutcome] | None,
    actor_id: str | None,
    resource_type: list[ResourceType] | None,
    resource_id: str | None,
    correlation_id: str | None,
) -> bool:
    """In-process filter predicate — backends have no native filtering.

    ``outcome`` and ``resource_type`` are lists (multi-select); an empty
    or ``None`` list means "any".  All other fields are exact-match.

    Keep in sync with ``matchesFilters`` in ``ui/src/pages/Audit.tsx`` —
    the UI live-tail filters client-side with the same semantics.
    """
    if action is not None and event.action != action:
        return False
    if action_category is not None:
        category = event.action.split(".", 1)[0] if "." in event.action else event.action
        if category != action_category:
            return False
    if outcome and event.outcome not in outcome:
        return False
    if actor_id is not None and event.actor_id != actor_id:
        return False
    if resource_type and event.resource_type not in resource_type:
        return False
    # A series of ``if mismatch: return False`` is more readable here than
    # collapsing the last check into a single boolean return.
    if resource_id is not None and event.resource_id != resource_id:
        return False
    if correlation_id is not None and event.correlation_id != correlation_id:  # noqa: SIM103
        return False
    return True


@router.get("/status")
async def audit_status() -> dict[str, Any]:
    """Report whether a reader-capable sink is configured and its size.

    The response shape is deliberately backend-agnostic.  Fields known to
    the UI today (``stream_sink_configured``, ``stream_name``,
    ``length``, ``maxlen``) are kept for compatibility; additional
    backend-specific fields returned by ``reader.describe()`` are spliced
    in under the top-level keys the UI already renders.
    """
    reader = _find_reader()
    if reader is None:
        return {
            "stream_sink_configured": False,
            "stream_name": None,
            "length": None,
            "maxlen": None,
            "backend": None,
        }
    describe = reader.describe()
    length = await reader.count()
    return {
        "stream_sink_configured": True,
        # Legacy fields preserved for the existing UI.  Readers that
        # don't have a "stream name" can omit ``stream_name`` from
        # ``describe()`` and the UI falls back to the backend label.
        "stream_name": describe.get("stream_name"),
        "maxlen": describe.get("maxlen"),
        "length": length,
        "backend": describe.get("backend"),
        # Pass through any additional backend-specific metadata for
        # forward-compatible UI rendering.
        **{k: v for k, v in describe.items() if k not in {"stream_name", "maxlen", "backend"}},
    }


@router.get("/events")
async def list_audit_events(
    start: str = Query("-", description="Backend-specific cursor for the start of the window. '-' = oldest."),
    end: str = Query("+", description="Backend-specific cursor for the end of the window. '+' = newest."),
    count: int = Query(100, ge=1, le=500, description="Max matching events to return."),
    action: str | None = None,
    action_category: str | None = None,
    outcome: Annotated[
        list[AuditOutcome] | None,
        Query(description="Repeatable — matches any of the given outcomes."),
    ] = None,
    actor_id: str | None = None,
    resource_type: Annotated[
        list[ResourceType] | None,
        Query(description="Repeatable — matches any of the given resource types."),
    ] = None,
    resource_id: str | None = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Paginated historical browse.

    Returns newest-first.  ``next`` is the cursor of the last entry the
    reader inspected — pass it as ``end`` on the next request to continue
    paging backward.  ``None`` indicates the window is exhausted.
    """
    reader = _find_reader()
    if reader is None:
        raise HTTPException(
            status_code=409,
            detail=("No audit reader configured. Enable a reader-capable sink (e.g. 'redis_stream') in audit.sinks."),
        )

    batch = min(count * _OVERREAD_MULTIPLIER, _OVERREAD_CAP)
    matched: list[dict[str, Any]] = []
    scanned = 0
    next_cursor: str | None = end
    cursor = end
    while True:
        try:
            raw_events, next_cursor = await reader.list_events(start=start, end=cursor, count=batch)
        except Exception as exc:
            logger.exception("Audit list: reader.list_events failed")
            raise HTTPException(status_code=503, detail=f"Audit read failed: {exc}") from None

        scanned += len(raw_events)
        # Defensive: a backend that returns an empty batch but advances
        # the cursor would otherwise spin forever.
        if not raw_events:
            break
        for event in raw_events:
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

        if len(matched) >= count or next_cursor is None or scanned >= _MAX_SCAN:
            break
        cursor = next_cursor

    return {"events": matched, "next": next_cursor, "scanned": scanned}


@router.get("/events/stream")
async def stream_audit_events(
    request: Request,
    action: str | None = None,
    action_category: str | None = None,
    outcome: Annotated[
        list[AuditOutcome] | None,
        Query(description="Repeatable — matches any of the given outcomes."),
    ] = None,
    actor_id: str | None = None,
    resource_type: Annotated[
        list[ResourceType] | None,
        Query(description="Repeatable — matches any of the given resource types."),
    ] = None,
    resource_id: str | None = None,
    correlation_id: str | None = None,
) -> StreamingResponse:
    """SSE live tail — yields new events as they are written.

    The underlying reader's ``tail()`` yields ``AuditEvent`` on write,
    or ``None`` as a keepalive tick — the route turns each into either
    a ``data:`` frame or a ``: keepalive`` comment frame.
    """
    reader = _find_reader()
    if reader is None:
        raise HTTPException(
            status_code=409,
            detail=("No audit reader configured. Enable a reader-capable sink (e.g. 'redis_stream') in audit.sinks."),
        )

    async def _generate() -> AsyncIterator[str]:
        async for item in reader.tail():
            if await request.is_disconnected():
                return
            if item is None:
                # Reader signaled "no new events this tick" — emit an SSE
                # keepalive so the connection stays warm behind reverse
                # proxies and the disconnect check runs.
                yield ": keepalive\n\n"
                continue
            if not _match_event(
                item,
                action=action,
                action_category=action_category,
                outcome=outcome,
                actor_id=actor_id,
                resource_type=resource_type,
                resource_id=resource_id,
                correlation_id=correlation_id,
            ):
                continue
            # Reuse the sink's JSON shape exactly (Pydantic's compact
            # ``model_dump_json``) so consumers see identical
            # serialization regardless of which endpoint they read from.
            yield f"data: {item.model_dump_json()}\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
