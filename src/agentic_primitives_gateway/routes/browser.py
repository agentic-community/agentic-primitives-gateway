from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

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
from agentic_primitives_gateway.routes._helpers import browser_session_owners, require_principal

router = APIRouter(
    prefix="/api/v1/browser",
    tags=[Primitive.BROWSER],
    dependencies=[Depends(require_principal)],
)


@router.post("/sessions", response_model=BrowserSessionInfo, status_code=201)
async def start_session(
    request: StartBrowserSessionRequest,
) -> BrowserSessionInfo:
    principal = require_principal()
    config = dict(request.config)
    if request.viewport:
        config["viewport"] = request.viewport
    result = await registry.browser.start_session(
        session_id=request.session_id,
        config=config,
    )
    info = BrowserSessionInfo(**result)
    await browser_session_owners.set_owner(info.session_id, principal.id)
    return info


@router.delete("/sessions/{session_id}")
async def stop_session(session_id: str) -> Response:
    await browser_session_owners.require_owner(session_id, require_principal())
    await registry.browser.stop_session(session_id)
    await browser_session_owners.delete(session_id)
    return Response(status_code=204)


@router.get("/sessions/{session_id}", response_model=BrowserSessionInfo)
async def get_session(session_id: str) -> BrowserSessionInfo:
    await browser_session_owners.require_owner(session_id, require_principal())
    try:
        result = await registry.browser.get_session(session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return BrowserSessionInfo(**result)


@router.get("/sessions", response_model=ListBrowserSessionsResponse)
async def list_sessions(
    status: str | None = None,
) -> ListBrowserSessionsResponse:
    principal = require_principal()
    sessions = await registry.browser.list_sessions(status=status)
    all_infos = [BrowserSessionInfo(**s) for s in sessions]
    if principal.is_admin:
        return ListBrowserSessionsResponse(sessions=all_infos)
    owned = await browser_session_owners.owned_session_ids(principal.id)
    return ListBrowserSessionsResponse(sessions=[s for s in all_infos if s.session_id in owned])


@router.get("/sessions/{session_id}/live-view", response_model=LiveViewResponse)
async def get_live_view_url(
    session_id: str,
    expires: int = Query(default=300, ge=1, le=3600),
) -> LiveViewResponse:
    await browser_session_owners.require_owner(session_id, require_principal())
    try:
        url = await registry.browser.get_live_view_url(session_id=session_id, expires=expires)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return LiveViewResponse(url=url, expires_in=expires)


# ── Browser interaction endpoints ───────────────────────────────────


@router.post("/sessions/{session_id}/navigate")
async def navigate(session_id: str, request: NavigateRequest) -> dict[str, Any]:
    await browser_session_owners.require_owner(session_id, require_principal())
    try:
        return await registry.browser.navigate(session_id, request.url)
    except (ValueError, NotImplementedError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/sessions/{session_id}/screenshot")
async def screenshot(session_id: str) -> dict[str, str]:
    await browser_session_owners.require_owner(session_id, require_principal())
    try:
        data = await registry.browser.screenshot(session_id)
        return {"format": "png", "data": data}
    except (ValueError, NotImplementedError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/sessions/{session_id}/content")
async def get_page_content(session_id: str) -> dict[str, str]:
    await browser_session_owners.require_owner(session_id, require_principal())
    try:
        content = await registry.browser.get_page_content(session_id)
        return {"content": content}
    except (ValueError, NotImplementedError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/sessions/{session_id}/click")
async def click(session_id: str, request: ClickRequest) -> dict[str, Any]:
    await browser_session_owners.require_owner(session_id, require_principal())
    try:
        return await registry.browser.click(session_id, request.selector)
    except (ValueError, NotImplementedError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/sessions/{session_id}/type")
async def type_text(session_id: str, request: TypeRequest) -> dict[str, Any]:
    await browser_session_owners.require_owner(session_id, require_principal())
    try:
        return await registry.browser.type_text(session_id, request.selector, request.text)
    except (ValueError, NotImplementedError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/sessions/{session_id}/evaluate")
async def evaluate(session_id: str, request: EvaluateRequest) -> dict[str, Any]:
    await browser_session_owners.require_owner(session_id, require_principal())
    try:
        result = await registry.browser.evaluate(session_id, request.expression)
        return {"result": result}
    except (ValueError, NotImplementedError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        # Catch Playwright errors, Selenium errors, etc.
        if "Error" in type(e).__name__ or "Exception" in type(e).__name__:
            raise HTTPException(status_code=400, detail=str(e)) from e
        raise
