from __future__ import annotations

import base64
import logging
from typing import Any

from bedrock_agentcore.tools import BrowserClient

from agentic_primitives_gateway.context import get_boto3_session
from agentic_primitives_gateway.primitives._sync import SyncRunnerMixin
from agentic_primitives_gateway.primitives.browser.base import BrowserProvider

logger = logging.getLogger(__name__)


class AgentCoreBrowserProvider(BrowserProvider, SyncRunnerMixin):
    """Browser provider backed by AWS Bedrock AgentCore Browser service.

    Uses Playwright to connect to the AgentCore browser via CDP for
    full browser interaction (navigate, screenshot, page content, click, type).

    Requires: pip install playwright && playwright install chromium

    Provider config example::

        backend: agentic_primitives_gateway.primitives.browser.agentcore.AgentCoreBrowserProvider
        config:
          region: "us-east-1"
    """

    def __init__(self, region: str = "us-east-1", **kwargs: Any) -> None:
        self._region = region
        self._sessions: dict[str, BrowserClient] = {}
        self._pages: dict[str, Any] = {}  # session_id -> Playwright Page
        self._browsers: dict[str, Any] = {}  # session_id -> Playwright Browser
        self._playwright: Any = None
        logger.info("AgentCore browser provider initialized (region=%s)", region)

    async def _ensure_playwright(self) -> Any:
        if self._playwright is None:
            from playwright.async_api import async_playwright

            pw = await async_playwright().start()
            self._playwright = pw
        return self._playwright

    async def start_session(
        self,
        session_id: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        boto_session = get_boto3_session(default_region=self._region)
        client = BrowserClient(region=boto_session.region_name)

        viewport = (config or {}).get("viewport")

        kwargs: dict[str, Any] = {}
        if session_id:
            kwargs["name"] = session_id
        if viewport:
            kwargs["viewport"] = {
                "width": viewport.get("width", 1920),
                "height": viewport.get("height", 1080),
            }

        result = await self._run_sync(client.start, **kwargs)
        sid = result if isinstance(result, str) else result.get("sessionId", session_id or "unknown")
        self._sessions[sid] = client

        # Connect Playwright via CDP
        try:
            pw = await self._ensure_playwright()
            ws_url, headers = await self._run_sync(client.generate_ws_headers)
            browser = await pw.chromium.connect_over_cdp(endpoint_url=ws_url, headers=headers)
            self._browsers[sid] = browser
            # Prefer creating a new context with SSL bypass — remote browsers may lack CA certs
            try:
                context = await browser.new_context(ignore_https_errors=True)
            except Exception:
                # Fall back to existing context (e.g. in unit tests with mocks)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()
            self._pages[sid] = page
            logger.info("Playwright connected to browser session %s", sid)
        except Exception as e:
            logger.warning("Playwright CDP connection failed: %s (browser interaction won't work)", e)

        return {
            "session_id": sid,
            "status": "active",
            "viewport": viewport,
        }

    async def stop_session(self, session_id: str) -> None:
        # Close Playwright resources
        page = self._pages.pop(session_id, None)
        if page:
            try:  # noqa: SIM105
                await page.close()
            except Exception:
                pass

        browser = self._browsers.pop(session_id, None)
        if browser:
            try:  # noqa: SIM105
                await browser.close()
            except Exception:
                pass

        client = self._sessions.pop(session_id, None)
        if client:
            await self._run_sync(client.stop)

    def _get_page(self, session_id: str) -> Any:
        page = self._pages.get(session_id)
        if page is None:
            raise ValueError(
                f"No Playwright page for session {session_id}. Session may not have started or CDP connection failed."
            )
        return page

    async def get_session(self, session_id: str) -> dict[str, Any]:
        client = self._sessions.get(session_id)
        if not client:
            raise ValueError(f"Session {session_id} not found")
        page = self._pages.get(session_id)
        return {
            "session_id": session_id,
            "status": "active",
            "url": page.url if page else None,
        }

    async def list_sessions(self, status: str | None = None) -> list[dict[str, Any]]:
        return [{"session_id": sid, "status": "active"} for sid in self._sessions]

    async def get_live_view_url(self, session_id: str, expires: int = 300) -> str:
        client = self._sessions.get(session_id)
        if not client:
            raise ValueError(f"Session {session_id} not found")
        result: str = await self._run_sync(client.generate_live_view_url, expires=expires)
        return result

    # ── Browser interaction via Playwright ──────────────────────────

    async def navigate(self, session_id: str, url: str) -> dict[str, Any]:
        page = self._get_page(session_id)
        response = await page.goto(url, wait_until="domcontentloaded")
        return {
            "url": page.url,
            "status": response.status if response else None,
            "title": await page.title(),
        }

    async def screenshot(self, session_id: str) -> str:
        page = self._get_page(session_id)
        data = await page.screenshot(type="png")
        return base64.b64encode(data).decode()

    async def get_page_content(self, session_id: str) -> str:
        page = self._get_page(session_id)
        result: str = await page.evaluate("document.body?.innerText || ''")
        return result

    async def click(self, session_id: str, selector: str) -> dict[str, Any]:
        page = self._get_page(session_id)
        await page.click(selector)
        return {"status": "clicked", "selector": selector}

    async def type_text(self, session_id: str, selector: str, text: str) -> dict[str, Any]:
        page = self._get_page(session_id)
        await page.fill(selector, text)
        return {"status": "typed", "selector": selector, "text": text}

    async def evaluate(self, session_id: str, expression: str) -> Any:
        page = self._get_page(session_id)
        # Playwright wraps the expression in a function, so bare `return` is illegal.
        # Strip leading `return` if the LLM included it.
        expr = expression.strip()
        if expr.startswith("return "):
            expr = expr[7:]
        return await page.evaluate(expr)

    async def healthcheck(self) -> bool:
        return True
