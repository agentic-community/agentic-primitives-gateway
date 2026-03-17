from fastapi import APIRouter, Depends

from agentic_primitives_gateway.models.enums import Primitive
from agentic_primitives_gateway.models.gateway import (
    CompletionRequest,
    CompletionResponse,
    ListModelsResponse,
    ModelInfo,
)
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway.routes._helpers import require_principal

router = APIRouter(
    prefix="/api/v1/gateway",
    tags=[Primitive.GATEWAY],
    dependencies=[Depends(require_principal)],
)


@router.post("/completions", response_model=CompletionResponse)
async def route_completion(request: CompletionRequest) -> CompletionResponse:
    result = await registry.gateway.route_request(request.model_dump())
    return CompletionResponse(**result)


@router.get("/models", response_model=ListModelsResponse)
async def list_models() -> ListModelsResponse:
    models = await registry.gateway.list_models()
    return ListModelsResponse(models=[ModelInfo(**m) for m in models])
