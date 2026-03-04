from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response

from agentic_primitives_gateway.models.enums import Primitive
from agentic_primitives_gateway.models.evaluations import (
    CreateEvaluatorRequest,
    CreateOnlineEvalConfigRequest,
    EvaluateRequest,
    UpdateEvaluatorRequest,
)
from agentic_primitives_gateway.registry import registry

router = APIRouter(prefix="/api/v1/evaluations", tags=[Primitive.EVALUATIONS])


# ── Evaluator CRUD ─────────────────────────────────────────────────


@router.post("/evaluators", status_code=201)
async def create_evaluator(request: CreateEvaluatorRequest) -> Any:
    return await registry.evaluations.create_evaluator(
        name=request.name,
        evaluator_type=request.evaluator_type,
        config=request.config or None,
        description=request.description,
    )


@router.get("/evaluators")
async def list_evaluators(
    max_results: int = Query(default=100, ge=1, le=1000),
    next_token: str | None = Query(default=None),
) -> Any:
    return await registry.evaluations.list_evaluators(
        max_results=max_results,
        next_token=next_token,
    )


@router.get("/evaluators/{evaluator_id}")
async def get_evaluator(evaluator_id: str) -> Any:
    return await registry.evaluations.get_evaluator(evaluator_id=evaluator_id)


@router.put("/evaluators/{evaluator_id}")
async def update_evaluator(evaluator_id: str, request: UpdateEvaluatorRequest) -> Any:
    return await registry.evaluations.update_evaluator(
        evaluator_id=evaluator_id,
        config=request.config,
        description=request.description,
    )


@router.delete("/evaluators/{evaluator_id}")
async def delete_evaluator(evaluator_id: str) -> Response:
    await registry.evaluations.delete_evaluator(evaluator_id=evaluator_id)
    return Response(status_code=204)


# ── Evaluate ───────────────────────────────────────────────────────


@router.post("/evaluate")
async def evaluate(request: EvaluateRequest) -> Any:
    return await registry.evaluations.evaluate(
        evaluator_id=request.evaluator_id,
        target=request.target,
        input_data=request.input_data,
        output_data=request.output_data,
        expected_output=request.expected_output,
        metadata=request.metadata or None,
    )


# ── Online evaluation configs ──────────────────────────────────────


@router.post("/online-configs", status_code=201)
async def create_online_evaluation_config(request: CreateOnlineEvalConfigRequest) -> Any:
    try:
        return await registry.evaluations.create_online_evaluation_config(
            name=request.name,
            evaluator_ids=request.evaluator_ids,
            config=request.config or None,
        )
    except NotImplementedError:
        raise HTTPException(
            status_code=501, detail="Online evaluation configs not supported by this provider"
        ) from None


@router.get("/online-configs")
async def list_online_evaluation_configs(
    max_results: int = Query(default=100, ge=1, le=1000),
    next_token: str | None = Query(default=None),
) -> Any:
    try:
        return await registry.evaluations.list_online_evaluation_configs(
            max_results=max_results,
            next_token=next_token,
        )
    except NotImplementedError:
        raise HTTPException(
            status_code=501, detail="Online evaluation configs not supported by this provider"
        ) from None


@router.get("/online-configs/{config_id}")
async def get_online_evaluation_config(config_id: str) -> Any:
    try:
        return await registry.evaluations.get_online_evaluation_config(config_id=config_id)
    except NotImplementedError:
        raise HTTPException(
            status_code=501, detail="Online evaluation configs not supported by this provider"
        ) from None


@router.delete("/online-configs/{config_id}")
async def delete_online_evaluation_config(config_id: str) -> Response:
    try:
        await registry.evaluations.delete_online_evaluation_config(config_id=config_id)
    except NotImplementedError:
        raise HTTPException(
            status_code=501, detail="Online evaluation configs not supported by this provider"
        ) from None
    return Response(status_code=204)
