from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

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
from agentic_primitives_gateway.routes._helpers import handle_provider_errors, require_principal

router = APIRouter(
    prefix="/api/v1/tools",
    tags=[Primitive.TOOLS],
    dependencies=[Depends(require_principal)],
)


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
@handle_provider_errors("Server listing not supported by this provider")
async def list_servers() -> Any:
    servers = await registry.tools.list_servers()
    return ListServersResponse(servers=[ServerInfo(**s) for s in servers])


@router.post("/servers", status_code=201)
async def register_server(request: RegisterServerRequest) -> Any:
    try:
        return await registry.tools.register_server(request.model_dump())
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="Server registration not supported by this provider") from None


@router.get("/servers/{server_name}")
@handle_provider_errors("get_server not supported by this provider", not_found="Server not found")
async def get_server(server_name: str) -> Any:
    return await registry.tools.get_server(server_name)


# ── Tool invoke (has /invoke suffix, no conflict with catch-all) ─────


@router.post("/{name:path}/invoke", response_model=ToolResult)
async def invoke_tool(name: str, request: InvokeToolRequest) -> ToolResult:
    try:
        result = await registry.tools.invoke_tool(tool_name=name, params=request.params)
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Tool invocation failed: {e}") from None
    return ToolResult(tool_name=name, **result)


# ── Single tool retrieval & deletion (catch-all {name:path}) ─────────


@router.get("/{name:path}", response_model=ToolInfo)
@handle_provider_errors("get_tool not supported by this provider", not_found="Tool not found")
async def get_tool(name: str) -> Any:
    result = await registry.tools.get_tool(name)
    return ToolInfo(**result)


@router.delete("/{name:path}")
@handle_provider_errors("delete_tool not supported by this provider", not_found="Tool not found")
async def delete_tool(name: str) -> Response:
    await registry.tools.delete_tool(name)
    return Response(status_code=204)
