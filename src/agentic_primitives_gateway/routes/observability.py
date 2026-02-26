from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from agentic_primitives_gateway.models.enums import HealthStatus, Primitive
from agentic_primitives_gateway.models.observability import (
    FlushResponse,
    GenerationInfo,
    IngestLogRequest,
    IngestTraceRequest,
    ListScoresResponse,
    ListSessionsResponse,
    LogGenerationRequest,
    ObservabilitySessionInfo,
    QueryTracesResponse,
    ScoreInfo,
    ScoreRequest,
    Trace,
    UpdateTraceRequest,
)
from agentic_primitives_gateway.registry import registry

router = APIRouter(prefix="/api/v1/observability", tags=[Primitive.OBSERVABILITY])


# ── Flush ────────────────────────────────────────────────────────────


@router.post("/flush", response_model=FlushResponse, status_code=202)
async def flush() -> FlushResponse:
    try:
        await registry.observability.flush()
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="flush not supported by this provider") from None
    return FlushResponse(status=HealthStatus.ACCEPTED)


# ── Sessions ─────────────────────────────────────────────────────────


@router.get("/sessions", response_model=ListSessionsResponse)
async def list_sessions(
    user_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> Any:
    try:
        sessions = await registry.observability.list_sessions(user_id=user_id, limit=limit)
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="list_sessions not supported by this provider") from None
    return ListSessionsResponse(sessions=[ObservabilitySessionInfo(**s) for s in sessions])


@router.get("/sessions/{session_id}", response_model=ObservabilitySessionInfo)
async def get_session(session_id: str) -> Any:
    try:
        result = await registry.observability.get_session(session_id)
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="get_session not supported by this provider") from None
    return ObservabilitySessionInfo(**result)


# ── Trace sub-resources (generations, scores) ────────────────────────
# Register these BEFORE /traces/{trace_id} to avoid path conflicts.


@router.post("/traces/{trace_id}/generations", response_model=GenerationInfo, status_code=201)
async def log_generation(trace_id: str, request: LogGenerationRequest) -> Any:
    try:
        usage_dict = request.usage.model_dump(exclude_none=True) if request.usage else None
        result = await registry.observability.log_generation(
            trace_id=trace_id,
            name=request.name,
            model=request.model,
            input=request.input,
            output=request.output,
            usage=usage_dict,
            metadata=request.metadata or None,
            level=request.level,
        )
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="log_generation not supported by this provider") from None
    return result


@router.post("/traces/{trace_id}/scores", response_model=ScoreInfo, status_code=201)
async def score_trace(trace_id: str, request: ScoreRequest) -> Any:
    try:
        result = await registry.observability.score_trace(
            trace_id=trace_id,
            name=request.name,
            value=request.value,
            comment=request.comment,
            data_type=request.data_type,
        )
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="score_trace not supported by this provider") from None
    return result


@router.get("/traces/{trace_id}/scores", response_model=ListScoresResponse)
async def list_scores(trace_id: str) -> Any:
    try:
        scores = await registry.observability.list_scores(trace_id)
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="list_scores not supported by this provider") from None
    return ListScoresResponse(scores=[ScoreInfo(**s) for s in scores])


# ── Single trace retrieval & update ──────────────────────────────────


@router.get("/traces/{trace_id}", response_model=Trace)
async def get_trace(trace_id: str) -> Any:
    try:
        result = await registry.observability.get_trace(trace_id)
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="get_trace not supported by this provider") from None
    except KeyError:
        raise HTTPException(status_code=404, detail="Trace not found") from None
    return Trace(**result)


@router.put("/traces/{trace_id}")
async def update_trace(trace_id: str, request: UpdateTraceRequest) -> Any:
    try:
        result = await registry.observability.update_trace(
            trace_id,
            name=request.name,
            user_id=request.user_id,
            session_id=request.session_id,
            input=request.input,
            output=request.output,
            metadata=request.metadata,
            tags=request.tags,
        )
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="update_trace not supported by this provider") from None
    return result


# ── Original endpoints (trace/log ingestion, query) ──────────────────


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
