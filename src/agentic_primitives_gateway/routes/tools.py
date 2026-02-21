from __future__ import annotations

from fastapi import APIRouter, Query

from agentic_primitives_gateway.models.tools import (
    InvokeToolRequest,
    ListToolsResponse,
    RegisterToolRequest,
    ToolInfo,
    ToolResult,
)
from agentic_primitives_gateway.registry import registry

router = APIRouter(prefix="/api/v1/tools", tags=["tools"])


@router.post("", response_model=ToolInfo, status_code=201)
async def register_tool(request: RegisterToolRequest) -> ToolInfo:
    await registry.tools.register_tool(request.model_dump())
    return ToolInfo(**request.model_dump())


@router.get("", response_model=ListToolsResponse)
async def list_tools() -> ListToolsResponse:
    tools = await registry.tools.list_tools()
    return ListToolsResponse(tools=[ToolInfo(**t) for t in tools])


@router.post("/{name:path}/invoke", response_model=ToolResult)
async def invoke_tool(name: str, request: InvokeToolRequest) -> ToolResult:
    result = await registry.tools.invoke_tool(tool_name=name, params=request.params)
    return ToolResult(tool_name=name, **result)


@router.get("/search", response_model=ListToolsResponse)
async def search_tools(
    query: str = Query(..., description="Search query"),
    max_results: int = Query(default=10, ge=1, le=100),
) -> ListToolsResponse:
    tools = await registry.tools.search_tools(query, max_results)
    return ListToolsResponse(tools=[ToolInfo(**t) for t in tools])
