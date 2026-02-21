from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from agentic_primitives_gateway.models.enums import HealthStatus, Primitive
from agentic_primitives_gateway.models.observability import (
    IngestLogRequest,
    IngestTraceRequest,
    QueryTracesResponse,
    Trace,
)
from agentic_primitives_gateway.registry import registry

router = APIRouter(prefix="/api/v1/observability", tags=[Primitive.OBSERVABILITY])


@router.post("/traces", status_code=202)
async def ingest_trace(request: IngestTraceRequest) -> dict[str, str]:
    await registry.observability.ingest_trace(request.model_dump())
    return {"status": HealthStatus.ACCEPTED}


@router.post("/logs", status_code=202)
async def ingest_log(request: IngestLogRequest) -> dict[str, str]:
    await registry.observability.ingest_log(request.model_dump())
    return {"status": HealthStatus.ACCEPTED}


@router.get("/traces", response_model=QueryTracesResponse)
async def query_traces(
    trace_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> QueryTracesResponse:
    filters: dict[str, Any] = {}
    if trace_id:
        filters["trace_id"] = trace_id
    filters["limit"] = limit
    results = await registry.observability.query_traces(filters=filters)
    return QueryTracesResponse(traces=[Trace(**r) for r in results])
