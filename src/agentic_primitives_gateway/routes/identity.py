from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from starlette.responses import Response

from agentic_primitives_gateway.models.enums import Primitive
from agentic_primitives_gateway.models.identity import (
    ApiKeyRequest,
    ApiKeyResponse,
    CompleteAuthRequest,
    CreateCredentialProviderRequest,
    CreateWorkloadIdentityRequest,
    CredentialProviderInfo,
    ListCredentialProvidersResponse,
    ListWorkloadIdentitiesResponse,
    TokenRequest,
    TokenResponse,
    UpdateCredentialProviderRequest,
    UpdateWorkloadIdentityRequest,
    WorkloadIdentityInfo,
    WorkloadTokenRequest,
    WorkloadTokenResponse,
)
from agentic_primitives_gateway.registry import registry

router = APIRouter(prefix="/api/v1/identity", tags=[Primitive.IDENTITY])


# ── Data plane — token operations ────────────────────────────────


@router.post("/token", response_model=TokenResponse)
async def get_token(request: TokenRequest) -> TokenResponse:
    result = await registry.identity.get_token(
        credential_provider=request.credential_provider,
        workload_token=request.workload_token,
        auth_flow=request.auth_flow,
        scopes=request.scopes or None,
        callback_url=request.callback_url,
        force_auth=request.force_auth,
        session_uri=request.session_uri,
        custom_state=request.custom_state,
        custom_parameters=request.custom_parameters,
    )
    return TokenResponse(**result)


@router.post("/api-key", response_model=ApiKeyResponse)
async def get_api_key(request: ApiKeyRequest) -> ApiKeyResponse:
    result = await registry.identity.get_api_key(
        credential_provider=request.credential_provider,
        workload_token=request.workload_token,
    )
    return ApiKeyResponse(**result)


@router.post("/workload-token", response_model=WorkloadTokenResponse)
async def get_workload_token(request: WorkloadTokenRequest) -> WorkloadTokenResponse:
    result = await registry.identity.get_workload_token(
        workload_name=request.workload_name,
        user_token=request.user_token,
        user_id=request.user_id,
    )
    return WorkloadTokenResponse(**result)


@router.post("/auth/complete", status_code=204)
async def complete_auth(request: CompleteAuthRequest) -> Response:
    try:
        await registry.identity.complete_auth(
            session_uri=request.session_uri,
            user_token=request.user_token,
            user_id=request.user_id,
        )
    except NotImplementedError:
        return JSONResponse(status_code=501, content={"detail": "complete_auth not supported by this provider"})
    return Response(status_code=204)


# ── Control plane — credential provider management ───────────────


@router.get("/credential-providers", response_model=ListCredentialProvidersResponse)
async def list_credential_providers() -> ListCredentialProvidersResponse:
    providers = await registry.identity.list_credential_providers()
    return ListCredentialProvidersResponse(credential_providers=[CredentialProviderInfo(**p) for p in providers])


@router.post("/credential-providers", response_model=CredentialProviderInfo, status_code=201)
async def create_credential_provider(request: CreateCredentialProviderRequest) -> JSONResponse:
    try:
        result = await registry.identity.create_credential_provider(
            name=request.name,
            provider_type=request.provider_type,
            config=request.config,
        )
    except NotImplementedError:
        return JSONResponse(
            status_code=501,
            content={"detail": "create_credential_provider not supported by this provider"},
        )
    return JSONResponse(status_code=201, content=CredentialProviderInfo(**result).model_dump(mode="json"))


@router.get("/credential-providers/{name}", response_model=CredentialProviderInfo)
async def get_credential_provider(name: str) -> JSONResponse:
    try:
        result = await registry.identity.get_credential_provider(name)
    except NotImplementedError:
        return JSONResponse(
            status_code=501,
            content={"detail": "get_credential_provider not supported by this provider"},
        )
    return JSONResponse(content=CredentialProviderInfo(**result).model_dump(mode="json"))


@router.put("/credential-providers/{name}", response_model=CredentialProviderInfo)
async def update_credential_provider(name: str, request: UpdateCredentialProviderRequest) -> JSONResponse:
    try:
        result = await registry.identity.update_credential_provider(
            name=name,
            config=request.config,
        )
    except NotImplementedError:
        return JSONResponse(
            status_code=501,
            content={"detail": "update_credential_provider not supported by this provider"},
        )
    return JSONResponse(content=CredentialProviderInfo(**result).model_dump(mode="json"))


@router.delete("/credential-providers/{name}", status_code=204)
async def delete_credential_provider(name: str) -> Response:
    try:
        await registry.identity.delete_credential_provider(name)
    except NotImplementedError:
        return JSONResponse(
            status_code=501,
            content={"detail": "delete_credential_provider not supported by this provider"},
        )
    return Response(status_code=204)


# ── Control plane — workload identity management ─────────────────


@router.get("/workload-identities", response_model=ListWorkloadIdentitiesResponse)
async def list_workload_identities() -> JSONResponse:
    try:
        identities = await registry.identity.list_workload_identities()
    except NotImplementedError:
        return JSONResponse(
            status_code=501,
            content={"detail": "list_workload_identities not supported by this provider"},
        )
    return JSONResponse(
        content=ListWorkloadIdentitiesResponse(
            workload_identities=[WorkloadIdentityInfo(**i) for i in identities]
        ).model_dump(mode="json")
    )


@router.post("/workload-identities", response_model=WorkloadIdentityInfo, status_code=201)
async def create_workload_identity(request: CreateWorkloadIdentityRequest) -> JSONResponse:
    try:
        result = await registry.identity.create_workload_identity(
            name=request.name,
            allowed_return_urls=request.allowed_return_urls or None,
        )
    except NotImplementedError:
        return JSONResponse(
            status_code=501,
            content={"detail": "create_workload_identity not supported by this provider"},
        )
    return JSONResponse(status_code=201, content=WorkloadIdentityInfo(**result).model_dump(mode="json"))


@router.get("/workload-identities/{name}", response_model=WorkloadIdentityInfo)
async def get_workload_identity(name: str) -> JSONResponse:
    try:
        result = await registry.identity.get_workload_identity(name)
    except NotImplementedError:
        return JSONResponse(
            status_code=501,
            content={"detail": "get_workload_identity not supported by this provider"},
        )
    return JSONResponse(content=WorkloadIdentityInfo(**result).model_dump(mode="json"))


@router.put("/workload-identities/{name}", response_model=WorkloadIdentityInfo)
async def update_workload_identity(name: str, request: UpdateWorkloadIdentityRequest) -> JSONResponse:
    try:
        result = await registry.identity.update_workload_identity(
            name=name,
            allowed_return_urls=request.allowed_return_urls or None,
        )
    except NotImplementedError:
        return JSONResponse(
            status_code=501,
            content={"detail": "update_workload_identity not supported by this provider"},
        )
    return JSONResponse(content=WorkloadIdentityInfo(**result).model_dump(mode="json"))


@router.delete("/workload-identities/{name}", status_code=204)
async def delete_workload_identity(name: str) -> Response:
    try:
        await registry.identity.delete_workload_identity(name)
    except NotImplementedError:
        return JSONResponse(
            status_code=501,
            content={"detail": "delete_workload_identity not supported by this provider"},
        )
    return Response(status_code=204)
