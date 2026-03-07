from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response

from agentic_primitives_gateway.models.enums import Primitive
from agentic_primitives_gateway.models.policy import (
    CreatePolicyEngineRequest,
    CreatePolicyRequest,
    ListPoliciesResponse,
    ListPolicyEnginesResponse,
    ListPolicyGenerationAssetsResponse,
    ListPolicyGenerationsResponse,
    PolicyEngineInfo,
    PolicyGenerationAssetInfo,
    PolicyGenerationInfo,
    PolicyInfo,
    StartPolicyGenerationRequest,
    UpdatePolicyRequest,
)
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway.routes._helpers import handle_provider_errors

router = APIRouter(prefix="/api/v1/policy", tags=[Primitive.POLICY])


# ── Policy engines ────────────────────────────────────────────────────


@router.post("/engines", response_model=PolicyEngineInfo, status_code=201)
async def create_policy_engine(request: CreatePolicyEngineRequest) -> PolicyEngineInfo:
    result = await registry.policy.create_policy_engine(
        name=request.name,
        description=request.description,
        config=request.config or None,
    )
    return PolicyEngineInfo(**result)


@router.get("/engines", response_model=ListPolicyEnginesResponse)
async def list_policy_engines(
    max_results: int = Query(default=100, ge=1, le=1000),
    next_token: str | None = None,
) -> ListPolicyEnginesResponse:
    result = await registry.policy.list_policy_engines(
        max_results=max_results,
        next_token=next_token,
    )
    engines = [PolicyEngineInfo(**e) for e in result.get("policy_engines", [])]
    return ListPolicyEnginesResponse(policy_engines=engines, next_token=result.get("next_token"))


@router.get("/engines/{engine_id}", response_model=PolicyEngineInfo)
async def get_policy_engine(engine_id: str) -> PolicyEngineInfo:
    result = await registry.policy.get_policy_engine(engine_id)
    return PolicyEngineInfo(**result)


@router.delete("/engines/{engine_id}")
async def delete_policy_engine(engine_id: str) -> Response:
    await registry.policy.delete_policy_engine(engine_id)
    return Response(status_code=204)


# ── Policies ──────────────────────────────────────────────────────────


@router.post("/engines/{engine_id}/policies", response_model=PolicyInfo, status_code=201)
async def create_policy(engine_id: str, request: CreatePolicyRequest) -> PolicyInfo:
    result = await registry.policy.create_policy(
        engine_id=engine_id,
        policy_body=request.policy_body,
        description=request.description,
    )
    return PolicyInfo(**result)


@router.get("/engines/{engine_id}/policies", response_model=ListPoliciesResponse)
async def list_policies(
    engine_id: str,
    max_results: int = Query(default=100, ge=1, le=1000),
    next_token: str | None = None,
) -> ListPoliciesResponse:
    result = await registry.policy.list_policies(
        engine_id=engine_id,
        max_results=max_results,
        next_token=next_token,
    )
    policies = [PolicyInfo(**p) for p in result.get("policies", [])]
    return ListPoliciesResponse(policies=policies, next_token=result.get("next_token"))


@router.get("/engines/{engine_id}/policies/{policy_id}", response_model=PolicyInfo)
async def get_policy(engine_id: str, policy_id: str) -> PolicyInfo:
    result = await registry.policy.get_policy(engine_id=engine_id, policy_id=policy_id)
    return PolicyInfo(**result)


@router.put("/engines/{engine_id}/policies/{policy_id}", response_model=PolicyInfo)
async def update_policy(engine_id: str, policy_id: str, request: UpdatePolicyRequest) -> PolicyInfo:
    result = await registry.policy.update_policy(
        engine_id=engine_id,
        policy_id=policy_id,
        policy_body=request.policy_body,
        description=request.description,
    )
    return PolicyInfo(**result)


@router.delete("/engines/{engine_id}/policies/{policy_id}")
async def delete_policy(engine_id: str, policy_id: str) -> Response:
    await registry.policy.delete_policy(engine_id=engine_id, policy_id=policy_id)
    return Response(status_code=204)


# ── Policy generation ─────────────────────────────────────────────────


@router.post("/engines/{engine_id}/generations", response_model=PolicyGenerationInfo, status_code=201)
async def start_policy_generation(engine_id: str, request: StartPolicyGenerationRequest) -> Any:
    try:
        result = await registry.policy.start_policy_generation(
            engine_id=engine_id,
            config=request.config or None,
        )
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="Policy generation not supported by this provider") from None
    return PolicyGenerationInfo(**result)


@router.get("/engines/{engine_id}/generations", response_model=ListPolicyGenerationsResponse)
@handle_provider_errors("Policy generation not supported by this provider")
async def list_policy_generations(
    engine_id: str,
    max_results: int = Query(default=100, ge=1, le=1000),
    next_token: str | None = None,
) -> Any:
    result = await registry.policy.list_policy_generations(
        engine_id=engine_id,
        max_results=max_results,
        next_token=next_token,
    )
    gens = [PolicyGenerationInfo(**g) for g in result.get("policy_generations", [])]
    return ListPolicyGenerationsResponse(policy_generations=gens, next_token=result.get("next_token"))


@router.get("/engines/{engine_id}/generations/{generation_id}", response_model=PolicyGenerationInfo)
@handle_provider_errors("Policy generation not supported by this provider")
async def get_policy_generation(engine_id: str, generation_id: str) -> Any:
    result = await registry.policy.get_policy_generation(
        engine_id=engine_id,
        generation_id=generation_id,
    )
    return PolicyGenerationInfo(**result)


@router.get(
    "/engines/{engine_id}/generations/{generation_id}/assets",
    response_model=ListPolicyGenerationAssetsResponse,
)
@handle_provider_errors("Policy generation not supported by this provider")
async def list_policy_generation_assets(
    engine_id: str,
    generation_id: str,
    max_results: int = Query(default=100, ge=1, le=1000),
    next_token: str | None = None,
) -> Any:
    result = await registry.policy.list_policy_generation_assets(
        engine_id=engine_id,
        generation_id=generation_id,
        max_results=max_results,
        next_token=next_token,
    )
    assets = [PolicyGenerationAssetInfo(**a) for a in result.get("policy_generation_assets", [])]
    return ListPolicyGenerationAssetsResponse(policy_generation_assets=assets, next_token=result.get("next_token"))
