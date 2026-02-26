from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response

from agentic_primitives_gateway.models.enums import Primitive
from agentic_primitives_gateway.models.tools import (
    InvokeToolRequest,
    ListServersResponse,
    ListToolsResponse,
    RegisterServerRequest,
    RegisterToolRequest,
    ServerInfo,
    ToolInfo,
    ToolResult,
)
from agentic_primitives_gateway.registry import registry

router = APIRouter(prefix="/api/v1/tools", tags=[Primitive.TOOLS])


# ── Fixed-path routes (must come before {name:path} catch-all) ───────


@router.post("", response_model=ToolInfo, status_code=201)
async def register_tool(request: RegisterToolRequest) -> ToolInfo:
    await registry.tools.register_tool(request.model_dump())
    return ToolInfo(**request.model_dump())


@router.get("", response_model=ListToolsResponse)
async def list_tools() -> ListToolsResponse:
    tools = await registry.tools.list_tools()
    return ListToolsResponse(tools=[ToolInfo(**t) for t in tools])


@router.get("/search", response_model=ListToolsResponse)
async def search_tools(
    query: str = Query(..., description="Search query"),
    max_results: int = Query(default=10, ge=1, le=100),
) -> ListToolsResponse:
    tools = await registry.tools.search_tools(query, max_results)
    return ListToolsResponse(tools=[ToolInfo(**t) for t in tools])


# ── Server management ────────────────────────────────────────────────


@router.get("/servers", response_model=ListServersResponse)
async def list_servers() -> Any:
    try:
        servers = await registry.tools.list_servers()
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="Server listing not supported by this provider") from None
    return ListServersResponse(servers=[ServerInfo(**s) for s in servers])


@router.post("/servers", status_code=201)
async def register_server(request: RegisterServerRequest) -> Any:
    try:
        result = await registry.tools.register_server(request.model_dump())
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="Server registration not supported by this provider") from None
    return result


@router.get("/servers/{server_name}")
async def get_server(server_name: str) -> Any:
    try:
        result = await registry.tools.get_server(server_name)
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="get_server not supported by this provider") from None
    except KeyError:
        raise HTTPException(status_code=404, detail="Server not found") from None
    return result


# ── Tool invoke (has /invoke suffix, no conflict with catch-all) ─────


@router.post("/{name:path}/invoke", response_model=ToolResult)
async def invoke_tool(name: str, request: InvokeToolRequest) -> ToolResult:
    result = await registry.tools.invoke_tool(tool_name=name, params=request.params)
    return ToolResult(tool_name=name, **result)


# ── Single tool retrieval & deletion (catch-all {name:path}) ─────────


@router.get("/{name:path}", response_model=ToolInfo)
async def get_tool(name: str) -> Any:
    try:
        result = await registry.tools.get_tool(name)
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="get_tool not supported by this provider") from None
    except KeyError:
        raise HTTPException(status_code=404, detail="Tool not found") from None
    return ToolInfo(**result)


@router.delete("/{name:path}")
async def delete_tool(name: str) -> Response:
    try:
        await registry.tools.delete_tool(name)
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="delete_tool not supported by this provider") from None
    except KeyError:
        raise HTTPException(status_code=404, detail="Tool not found") from None
    return Response(status_code=204)
