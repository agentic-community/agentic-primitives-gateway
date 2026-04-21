from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from starlette.responses import Response

from agentic_primitives_gateway.audit.emit import audit_mutation, emit_audit_event
from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType
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
from agentic_primitives_gateway.routes._helpers import handle_provider_errors, require_admin, require_principal

router = APIRouter(
    prefix="/api/v1/identity",
    tags=[Primitive.IDENTITY],
    dependencies=[Depends(require_principal)],
)


# ── Data plane — token operations ────────────────────────────────


@router.post("/token", response_model=TokenResponse)
async def get_token(request: TokenRequest) -> TokenResponse:
    try:
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
    except Exception as exc:
        emit_audit_event(
            action=AuditAction.CREDENTIAL_READ,
            outcome=AuditOutcome.FAILURE,
            resource_type=ResourceType.CREDENTIAL,
            resource_id=request.credential_provider,
            metadata={"kind": "token", "error_type": type(exc).__name__},
        )
        raise
    emit_audit_event(
        action=AuditAction.CREDENTIAL_READ,
        outcome=AuditOutcome.SUCCESS,
        resource_type=ResourceType.CREDENTIAL,
        resource_id=request.credential_provider,
        metadata={"kind": "token", "auth_flow": request.auth_flow},
    )
    return TokenResponse(**result)


@router.post("/api-key", response_model=ApiKeyResponse)
async def get_api_key(request: ApiKeyRequest) -> ApiKeyResponse:
    try:
        result = await registry.identity.get_api_key(
            credential_provider=request.credential_provider,
            workload_token=request.workload_token,
        )
    except Exception as exc:
        emit_audit_event(
            action=AuditAction.CREDENTIAL_READ,
            outcome=AuditOutcome.FAILURE,
            resource_type=ResourceType.CREDENTIAL,
            resource_id=request.credential_provider,
            metadata={"kind": "api_key", "error_type": type(exc).__name__},
        )
        raise
    emit_audit_event(
        action=AuditAction.CREDENTIAL_READ,
        outcome=AuditOutcome.SUCCESS,
        resource_type=ResourceType.CREDENTIAL,
        resource_id=request.credential_provider,
        metadata={"kind": "api_key"},
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
    require_admin()
    async with audit_mutation(
        AuditAction.IDENTITY_CREDENTIAL_PROVIDER_CREATE,
        resource_type=ResourceType.IDENTITY,
        resource_id=request.name,
        metadata={"provider_type": request.provider_type},
    ):
        try:
            result = await registry.identity.create_credential_provider(
                name=request.name,
                provider_type=request.provider_type,
                config=request.config,
            )
        except NotImplementedError:
            return JSONResponse(
                status_code=501, content={"detail": "create_credential_provider not supported by this provider"}
            )
    return JSONResponse(status_code=201, content=CredentialProviderInfo(**result).model_dump(mode="json"))


@router.get("/credential-providers/{name}", response_model=CredentialProviderInfo)
@handle_provider_errors("get_credential_provider not supported by this provider")
async def get_credential_provider(name: str) -> JSONResponse:
    result = await registry.identity.get_credential_provider(name)
    return JSONResponse(content=CredentialProviderInfo(**result).model_dump(mode="json"))


@router.put("/credential-providers/{name}", response_model=CredentialProviderInfo)
async def update_credential_provider(name: str, request: UpdateCredentialProviderRequest) -> JSONResponse:
    require_admin()
    async with audit_mutation(
        AuditAction.IDENTITY_CREDENTIAL_PROVIDER_UPDATE,
        resource_type=ResourceType.IDENTITY,
        resource_id=name,
    ):
        try:
            result = await registry.identity.update_credential_provider(
                name=name,
                config=request.config,
            )
        except NotImplementedError:
            return JSONResponse(
                status_code=501, content={"detail": "update_credential_provider not supported by this provider"}
            )
    return JSONResponse(content=CredentialProviderInfo(**result).model_dump(mode="json"))


@router.delete("/credential-providers/{name}", status_code=204)
async def delete_credential_provider(name: str) -> Response:
    require_admin()
    async with audit_mutation(
        AuditAction.IDENTITY_CREDENTIAL_PROVIDER_DELETE,
        resource_type=ResourceType.IDENTITY,
        resource_id=name,
    ):
        try:
            await registry.identity.delete_credential_provider(name)
        except NotImplementedError:
            return JSONResponse(
                status_code=501, content={"detail": "delete_credential_provider not supported by this provider"}
            )
    return Response(status_code=204)


# ── Control plane — workload identity management ─────────────────


@router.get("/workload-identities", response_model=ListWorkloadIdentitiesResponse)
@handle_provider_errors("list_workload_identities not supported by this provider")
async def list_workload_identities() -> JSONResponse:
    identities = await registry.identity.list_workload_identities()
    return JSONResponse(
        content=ListWorkloadIdentitiesResponse(
            workload_identities=[WorkloadIdentityInfo(**i) for i in identities]
        ).model_dump(mode="json")
    )


@router.post("/workload-identities", response_model=WorkloadIdentityInfo, status_code=201)
async def create_workload_identity(request: CreateWorkloadIdentityRequest) -> JSONResponse:
    require_admin()
    async with audit_mutation(
        AuditAction.IDENTITY_WORKLOAD_CREATE,
        resource_type=ResourceType.IDENTITY,
        resource_id=request.name,
    ):
        try:
            result = await registry.identity.create_workload_identity(
                name=request.name,
                allowed_return_urls=request.allowed_return_urls or None,
            )
        except NotImplementedError:
            return JSONResponse(
                status_code=501, content={"detail": "create_workload_identity not supported by this provider"}
            )
    return JSONResponse(status_code=201, content=WorkloadIdentityInfo(**result).model_dump(mode="json"))


@router.get("/workload-identities/{name}", response_model=WorkloadIdentityInfo)
@handle_provider_errors("get_workload_identity not supported by this provider")
async def get_workload_identity(name: str) -> JSONResponse:
    result = await registry.identity.get_workload_identity(name)
    return JSONResponse(content=WorkloadIdentityInfo(**result).model_dump(mode="json"))


@router.put("/workload-identities/{name}", response_model=WorkloadIdentityInfo)
async def update_workload_identity(name: str, request: UpdateWorkloadIdentityRequest) -> JSONResponse:
    require_admin()
    async with audit_mutation(
        AuditAction.IDENTITY_WORKLOAD_UPDATE,
        resource_type=ResourceType.IDENTITY,
        resource_id=name,
    ):
        try:
            result = await registry.identity.update_workload_identity(
                name=name,
                allowed_return_urls=request.allowed_return_urls or None,
            )
        except NotImplementedError:
            return JSONResponse(
                status_code=501, content={"detail": "update_workload_identity not supported by this provider"}
            )
    return JSONResponse(content=WorkloadIdentityInfo(**result).model_dump(mode="json"))


@router.delete("/workload-identities/{name}", status_code=204)
async def delete_workload_identity(name: str) -> Response:
    require_admin()
    async with audit_mutation(
        AuditAction.IDENTITY_WORKLOAD_DELETE,
        resource_type=ResourceType.IDENTITY,
        resource_id=name,
    ):
        try:
            await registry.identity.delete_workload_identity(name)
        except NotImplementedError:
            return JSONResponse(
                status_code=501, content={"detail": "delete_workload_identity not supported by this provider"}
            )
    return Response(status_code=204)
