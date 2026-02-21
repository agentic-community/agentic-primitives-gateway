from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response

from agentic_primitives_gateway.models.enums import Primitive
from agentic_primitives_gateway.models.memory import (
    ListMemoryResponse,
    MemoryRecord,
    SearchMemoryRequest,
    SearchMemoryResponse,
    StoreMemoryRequest,
)
from agentic_primitives_gateway.registry import registry

router = APIRouter(prefix="/api/v1/memory", tags=[Primitive.MEMORY])


@router.post("/{namespace}", response_model=MemoryRecord, status_code=201)
async def store_memory(namespace: str, request: StoreMemoryRequest) -> MemoryRecord:
    return await registry.memory.store(
        namespace=namespace,
        key=request.key,
        content=request.content,
        metadata=request.metadata,
    )


@router.get("/{namespace}/{key}", response_model=MemoryRecord)
async def retrieve_memory(namespace: str, key: str) -> MemoryRecord:
    record = await registry.memory.retrieve(namespace=namespace, key=key)
    if record is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return record


@router.get("/{namespace}", response_model=ListMemoryResponse)
async def list_memories(
    namespace: str,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> ListMemoryResponse:
    records = await registry.memory.list_memories(namespace=namespace, limit=limit, offset=offset)
    return ListMemoryResponse(records=records, total=len(records))


@router.post("/{namespace}/search", response_model=SearchMemoryResponse)
async def search_memories(namespace: str, request: SearchMemoryRequest) -> SearchMemoryResponse:
    results = await registry.memory.search(
        namespace=namespace,
        query=request.query,
        top_k=request.top_k,
        filters=request.filters,
    )
    return SearchMemoryResponse(results=results)


@router.delete("/{namespace}/{key}")
async def delete_memory(namespace: str, key: str) -> Response:
    deleted = await registry.memory.delete(namespace=namespace, key=key)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    return Response(status_code=204)
