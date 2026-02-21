from __future__ import annotations

import asyncio
from typing import Any

from agentic_primitives_gateway_client.client import AgenticPlatformClient


class Browser:
    """Helper for the browser primitive — session lifecycle + interaction."""

    def __init__(
        self,
        client: AgenticPlatformClient,
        viewport: dict[str, int] | None = None,
    ) -> None:
        self._client = client
        self._viewport = viewport or {"width": 1920, "height": 1080}
        self._session_id: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def _require_session(self) -> str:
        if self._session_id is None:
            raise RuntimeError("No browser session. Call start() first.")
        return self._session_id

    # ── Session lifecycle ───────────────────────────────────────────

    async def start(self) -> str:
        """Start a browser session and return info with live view URL."""
        session = await self._client.start_browser_session(
            viewport=self._viewport,
        )
        self._session_id = session["session_id"]
        try:
            live = await self._client.get_live_view_url(self._session_id)  # type: ignore[arg-type]
            return f"Browser session started: {self._session_id}\nLive view: {live.get('url', 'N/A')}"
        except Exception:
            return f"Browser session started: {self._session_id}"

    async def close(self) -> str:
        """Stop the current browser session."""
        if self._session_id is None:
            return "No browser session is running."
        try:  # noqa: SIM105
            await self._client.stop_browser_session(self._session_id)
        except Exception:
            pass
        sid = self._session_id
        self._session_id = None
        return f"Browser session {sid} stopped."

    # ── Browser interaction ─────────────────────────────────────────

    async def navigate(self, url: str) -> str:
        """Navigate the browser to a URL."""
        sid = self._require_session()
        await self._client.browser_navigate(sid, url)
        return f"Navigated to {url}"

    async def screenshot(self) -> str:
        """Take a screenshot. Returns base64-encoded PNG data."""
        sid = self._require_session()
        result = await self._client.browser_screenshot(sid)
        data = result.get("data", "")
        return f"Screenshot captured ({len(data)} bytes base64)"

    async def get_page_content(self) -> str:
        """Get the text content of the current page."""
        sid = self._require_session()
        result = await self._client.browser_get_content(sid)
        return str(result.get("content", ""))

    async def click(self, selector: str) -> str:
        """Click an element on the page.

        Args:
            selector: CSS selector of the element to click.
        """
        sid = self._require_session()
        result = await self._client.browser_click(sid, selector)
        return f"Clicked: {selector} ({result.get('status', 'unknown')})"

    async def type_text(self, selector: str, text: str) -> str:
        """Type text into an input element.

        Args:
            selector: CSS selector of the input element.
            text: Text to type.
        """
        sid = self._require_session()
        result = await self._client.browser_type(sid, selector, text)
        return f"Typed into {selector} ({result.get('status', 'unknown')})"

    async def evaluate(self, expression: str) -> str:
        """Evaluate JavaScript in the browser and return the result.

        Args:
            expression: JavaScript expression to evaluate.
        """
        sid = self._require_session()
        result = await self._client.browser_evaluate(sid, expression)
        return str(result.get("result", ""))

    # ── Sync wrappers ───────────────────────────────────────────────

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _sync(self, coro: Any) -> Any:
        return self._get_loop().run_until_complete(coro)

    def start_sync(self) -> str:
        return str(self._sync(self.start()))

    def close_sync(self) -> str:
        return str(self._sync(self.close()))

    def navigate_sync(self, url: str) -> str:
        return str(self._sync(self.navigate(url)))

    def screenshot_sync(self) -> str:
        return str(self._sync(self.screenshot()))

    def get_page_content_sync(self) -> str:
        return str(self._sync(self.get_page_content()))

    def click_sync(self, selector: str) -> str:
        return str(self._sync(self.click(selector)))

    def type_text_sync(self, selector: str, text: str) -> str:
        return str(self._sync(self.type_text(selector, text)))

    def evaluate_sync(self, expression: str) -> str:
        return str(self._sync(self.evaluate(expression)))
