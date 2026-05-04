from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from agentic_primitives_gateway.audit.emit import audit_mutation
from agentic_primitives_gateway.audit.models import AuditAction, ResourceType
from agentic_primitives_gateway.models.enums import Primitive
from agentic_primitives_gateway.models.knowledge import (
    IngestRequest,
    IngestResult,
    ListDocumentsResponse,
    QueryRequest,
    QueryResponse,
    RetrieveRequest,
    RetrieveResponse,
)
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway.routes._helpers import (
    handle_provider_errors,
    require_principal,
    require_user_scoped,
)

router = APIRouter(
    prefix="/api/v1/knowledge",
    tags=[Primitive.KNOWLEDGE],
    dependencies=[Depends(require_principal)],
)


def _check_namespace(namespace: str) -> None:
    """Validate the caller owns this namespace."""
    require_user_scoped(namespace, require_principal())


# ── Namespace discovery ──────────────────────────────────────────────
# Must be registered before /{namespace} catch-all routes.


@router.get("/namespaces")
async def list_namespaces() -> Any:
    """List knowledge namespaces visible to the current user.

    Non-admin callers only see namespaces scoped to their own user ID.

    Unscoped (shared-corpus) namespaces are intentionally NOT listed
    for non-admins here.  That's because the REST surface currently
    has no per-principal ACL on unscoped namespaces — any authenticated
    caller could read/write them directly.  Listing them would advertise
    resources non-admins can't safely use.  Access to shared corpora
    for end users goes through the ``search_knowledge`` agent tool,
    which inherits access control from the agent's ``shared_with``.

    This is a known limitation tracked in the shared-namespace ACL
    follow-up issue.
    """
    principal = require_principal()
    namespaces = await registry.knowledge.list_namespaces()
    if principal.is_admin:
        return {"namespaces": namespaces}
    filtered = [ns for ns in namespaces if f":u:{principal.id}" in ns]
    return {"namespaces": filtered}


# ── Document ingest / list / delete ──────────────────────────────────


@router.post(
    "/{namespace}/documents",
    response_model=IngestResult,
    status_code=201,
)
async def ingest_documents(namespace: str, request: IngestRequest) -> IngestResult:
    _check_namespace(namespace)
    async with audit_mutation(
        AuditAction.KNOWLEDGE_INGEST,
        resource_type=ResourceType.KNOWLEDGE,
        resource_id=namespace,
        metadata={"document_count": len(request.documents)},
    ) as audit:
        try:
            result = await registry.knowledge.ingest(
                namespace=namespace,
                documents=request.documents,
            )
        except NotImplementedError:
            raise HTTPException(status_code=501, detail="Ingestion not supported by this provider") from None
        audit.metadata["ingested"] = result.ingested
        return result


@router.get(
    "/{namespace}/documents",
    response_model=ListDocumentsResponse,
)
@handle_provider_errors("Document listing not supported by this provider")
async def list_documents(
    namespace: str,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> ListDocumentsResponse:
    _check_namespace(namespace)
    documents = await registry.knowledge.list_documents(
        namespace=namespace,
        limit=limit,
        offset=offset,
    )
    return ListDocumentsResponse(documents=documents, total=len(documents))


@router.delete("/{namespace}/documents/{document_id}")
async def delete_document(namespace: str, document_id: str) -> Response:
    _check_namespace(namespace)
    async with audit_mutation(
        AuditAction.KNOWLEDGE_DELETE,
        resource_type=ResourceType.KNOWLEDGE,
        resource_id=f"{namespace}/{document_id}",
    ):
        try:
            deleted = await registry.knowledge.delete(namespace=namespace, document_id=document_id)
        except NotImplementedError:
            raise HTTPException(status_code=501, detail="Delete not supported by this provider") from None
        if not deleted:
            raise HTTPException(status_code=404, detail="Document not found")
    return Response(status_code=204)


# ── Retrieve / query ─────────────────────────────────────────────────


@router.post("/{namespace}/retrieve", response_model=RetrieveResponse)
async def retrieve(namespace: str, request: RetrieveRequest) -> RetrieveResponse:
    _check_namespace(namespace)
    chunks = await registry.knowledge.retrieve(
        namespace=namespace,
        query=request.query,
        top_k=request.top_k,
        filters=request.filters,
        include_citations=request.include_citations,
    )
    return RetrieveResponse(chunks=chunks)


@router.post("/{namespace}/query", response_model=QueryResponse)
@handle_provider_errors("Native query (retrieve-and-generate) not supported by this provider")
async def query(namespace: str, request: QueryRequest) -> QueryResponse:
    _check_namespace(namespace)
    return await registry.knowledge.query(
        namespace=namespace,
        question=request.question,
        top_k=request.top_k,
        filters=request.filters,
    )
