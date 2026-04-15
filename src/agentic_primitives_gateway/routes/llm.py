import json

from fastapi import APIRouter, Depends
from starlette.responses import StreamingResponse

from agentic_primitives_gateway.models.enums import Primitive
from agentic_primitives_gateway.models.llm import (
    CompletionRequest,
    CompletionResponse,
    ListModelsResponse,
    ModelInfo,
)
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway.routes._helpers import require_principal

router = APIRouter(
    prefix="/api/v1/llm",
    tags=[Primitive.LLM],
    dependencies=[Depends(require_principal)],
)


@router.post("/completions", response_model=CompletionResponse)
async def route_completion(request: CompletionRequest) -> CompletionResponse:
    result = await registry.llm.route_request(request.model_dump())
    return CompletionResponse(**result)


@router.post("/completions/stream")
async def route_completion_stream(request: CompletionRequest) -> StreamingResponse:
    async def generate():
        async for event in registry.llm.route_request_stream(request.model_dump()):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/models", response_model=ListModelsResponse)
async def list_models() -> ListModelsResponse:
    models = await registry.llm.list_models()
    return ListModelsResponse(models=[ModelInfo(**m) for m in models])
