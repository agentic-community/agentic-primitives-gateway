from __future__ import annotations

from fastapi import APIRouter

from agentic_primitives_gateway.models.enums import Primitive
from agentic_primitives_gateway.models.identity import (
    ApiKeyRequest,
    ApiKeyResponse,
    IdentityProviderInfo,
    ListProvidersResponse,
    TokenRequest,
    TokenResponse,
)
from agentic_primitives_gateway.registry import registry

router = APIRouter(prefix="/api/v1/identity", tags=[Primitive.IDENTITY])


@router.post("/token", response_model=TokenResponse)
async def get_token(request: TokenRequest) -> TokenResponse:
    result = await registry.identity.get_token(
        provider_name=request.provider_name,
        scopes=request.scopes or None,
        context=request.context or None,
    )
    return TokenResponse(**result)


@router.post("/api-key", response_model=ApiKeyResponse)
async def get_api_key(request: ApiKeyRequest) -> ApiKeyResponse:
    result = await registry.identity.get_api_key(
        provider_name=request.provider_name,
        context=request.context or None,
    )
    return ApiKeyResponse(**result)


@router.get("/providers", response_model=ListProvidersResponse)
async def list_providers() -> ListProvidersResponse:
    providers = await registry.identity.list_providers()
    return ListProvidersResponse(providers=[IdentityProviderInfo(**p) for p in providers])
