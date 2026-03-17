from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

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
from agentic_primitives_gateway.routes._helpers import handle_provider_errors, require_principal

router = APIRouter(
    prefix="/api/v1/observability",
    tags=[Primitive.OBSERVABILITY],
    dependencies=[Depends(require_principal)],
)


# ── Flush ────────────────────────────────────────────────────────────


@router.post("/flush", response_model=FlushResponse, status_code=202)
@handle_provider_errors("flush not supported by this provider")
async def flush() -> FlushResponse:
    await registry.observability.flush()
    return FlushResponse(status=HealthStatus.ACCEPTED)


# ── Sessions ─────────────────────────────────────────────────────────


@router.get("/sessions", response_model=ListSessionsResponse)
@handle_provider_errors("list_sessions not supported by this provider")
async def list_sessions(
    user_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> Any:
    sessions = await registry.observability.list_sessions(user_id=user_id, limit=limit)
    return ListSessionsResponse(sessions=[ObservabilitySessionInfo(**s) for s in sessions])


@router.get("/sessions/{session_id}", response_model=ObservabilitySessionInfo)
@handle_provider_errors("get_session not supported by this provider")
async def get_session(session_id: str) -> Any:
    result = await registry.observability.get_session(session_id)
    return ObservabilitySessionInfo(**result)


# ── Trace sub-resources (generations, scores) ────────────────────────
# Register these BEFORE /traces/{trace_id} to avoid path conflicts.


@router.post("/traces/{trace_id}/generations", response_model=GenerationInfo, status_code=201)
async def log_generation(trace_id: str, request: LogGenerationRequest) -> Any:
    usage_dict = request.usage.model_dump(exclude_none=True) if request.usage else None
    try:
        return await registry.observability.log_generation(
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


@router.post("/traces/{trace_id}/scores", response_model=ScoreInfo, status_code=201)
async def score_trace(trace_id: str, request: ScoreRequest) -> Any:
    try:
        return await registry.observability.score_trace(
            trace_id=trace_id,
            name=request.name,
            value=request.value,
            comment=request.comment,
            data_type=request.data_type,
        )
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="score_trace not supported by this provider") from None


@router.get("/traces/{trace_id}/scores", response_model=ListScoresResponse)
@handle_provider_errors("list_scores not supported by this provider")
async def list_scores(trace_id: str) -> Any:
    scores = await registry.observability.list_scores(trace_id)
    return ListScoresResponse(scores=[ScoreInfo(**s) for s in scores])


# ── Single trace retrieval & update ──────────────────────────────────


@router.get("/traces/{trace_id}", response_model=Trace)
@handle_provider_errors("get_trace not supported by this provider", not_found="Trace not found")
async def get_trace(trace_id: str) -> Any:
    result = await registry.observability.get_trace(trace_id)
    return Trace(**result)


@router.put("/traces/{trace_id}")
async def update_trace(trace_id: str, request: UpdateTraceRequest) -> Any:
    try:
        return await registry.observability.update_trace(
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
