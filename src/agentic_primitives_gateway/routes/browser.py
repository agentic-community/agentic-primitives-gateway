from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response

from agentic_primitives_gateway.models.browser import (
    BrowserSessionInfo,
    ClickRequest,
    EvaluateRequest,
    ListBrowserSessionsResponse,
    LiveViewResponse,
    NavigateRequest,
    StartBrowserSessionRequest,
    TypeRequest,
)
from agentic_primitives_gateway.models.enums import Primitive
from agentic_primitives_gateway.registry import registry

router = APIRouter(prefix="/api/v1/browser", tags=[Primitive.BROWSER])


@router.post("/sessions", response_model=BrowserSessionInfo, status_code=201)
async def start_session(
    request: StartBrowserSessionRequest,
) -> BrowserSessionInfo:
    config = dict(request.config)
    if request.viewport:
        config["viewport"] = request.viewport
    result = await registry.browser.start_session(
        session_id=request.session_id,
        config=config,
    )
    return BrowserSessionInfo(**result)


@router.delete("/sessions/{session_id}")
async def stop_session(session_id: str) -> Response:
    await registry.browser.stop_session(session_id)
    return Response(status_code=204)


@router.get("/sessions/{session_id}", response_model=BrowserSessionInfo)
async def get_session(session_id: str) -> BrowserSessionInfo:
    try:
        result = await registry.browser.get_session(session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return BrowserSessionInfo(**result)


@router.get("/sessions", response_model=ListBrowserSessionsResponse)
async def list_sessions(
    status: str | None = None,
) -> ListBrowserSessionsResponse:
    sessions = await registry.browser.list_sessions(status=status)
    return ListBrowserSessionsResponse(sessions=[BrowserSessionInfo(**s) for s in sessions])


@router.get("/sessions/{session_id}/live-view", response_model=LiveViewResponse)
async def get_live_view_url(
    session_id: str,
    expires: int = Query(default=300, ge=1, le=3600),
) -> LiveViewResponse:
    try:
        url = await registry.browser.get_live_view_url(session_id=session_id, expires=expires)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return LiveViewResponse(url=url, expires_in=expires)


# ── Browser interaction endpoints ───────────────────────────────────


@router.post("/sessions/{session_id}/navigate")
async def navigate(session_id: str, request: NavigateRequest) -> dict[str, Any]:
    try:
        return await registry.browser.navigate(session_id, request.url)
    except (ValueError, NotImplementedError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/sessions/{session_id}/screenshot")
async def screenshot(session_id: str) -> dict[str, str]:
    try:
        data = await registry.browser.screenshot(session_id)
        return {"format": "png", "data": data}
    except (ValueError, NotImplementedError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/sessions/{session_id}/content")
async def get_page_content(session_id: str) -> dict[str, str]:
    try:
        content = await registry.browser.get_page_content(session_id)
        return {"content": content}
    except (ValueError, NotImplementedError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/sessions/{session_id}/click")
async def click(session_id: str, request: ClickRequest) -> dict[str, Any]:
    try:
        return await registry.browser.click(session_id, request.selector)
    except (ValueError, NotImplementedError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/sessions/{session_id}/type")
async def type_text(session_id: str, request: TypeRequest) -> dict[str, Any]:
    try:
        return await registry.browser.type_text(session_id, request.selector, request.text)
    except (ValueError, NotImplementedError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/sessions/{session_id}/evaluate")
async def evaluate(session_id: str, request: EvaluateRequest) -> dict[str, Any]:
    try:
        result = await registry.browser.evaluate(session_id, request.expression)
        return {"result": result}
    except (ValueError, NotImplementedError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
