from __future__ import annotations

import asyncio
import base64
import datetime
import functools
import logging
import secrets
from collections.abc import Callable
from contextlib import suppress
from typing import Any, TypeVar
from urllib.parse import urlparse
from weakref import WeakValueDictionary

from bedrock_agentcore._utils.endpoints import get_data_plane_endpoint
from bedrock_agentcore.tools import BrowserClient
from bedrock_agentcore.tools.browser_client import MAX_LIVE_VIEW_PRESIGNED_URL_TIMEOUT
from botocore.auth import SigV4Auth, SigV4QueryAuth
from botocore.awsrequest import AWSRequest

from agentic_primitives_gateway.context import get_boto3_session
from agentic_primitives_gateway.primitives._agentcore import rebind_agentcore_clients
from agentic_primitives_gateway.primitives._sync import SyncRunnerMixin
from agentic_primitives_gateway.primitives.browser.base import BrowserProvider

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


def _frozen_credentials(session: Any) -> Any:
    credentials = session.get_credentials()
    if not credentials:
        raise RuntimeError("No AWS credentials found")
    return credentials.get_frozen_credentials()


def _generate_ws_headers(client: BrowserClient, session: Any) -> tuple[str, dict[str, str]]:
    """``BrowserClient.generate_ws_headers`` signed with ``session`` (SDK uses ambient creds)."""
    if not client.identifier or not client.session_id:
        client.start()

    host = get_data_plane_endpoint(client.region).replace("https://", "")
    path = f"/browser-streams/{client.identifier}/sessions/{client.session_id}/automation"
    frozen = _frozen_credentials(session)
    request = AWSRequest(
        method="GET",
        url=f"https://{host}{path}",
        headers={
            "host": host,
            "x-amz-date": datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ"),
        },
    )
    SigV4Auth(frozen, "bedrock-agentcore", client.region).add_auth(request)
    headers = {
        "Host": host,
        "X-Amz-Date": request.headers["x-amz-date"],
        "Authorization": request.headers["Authorization"],
        "Upgrade": "websocket",
        "Connection": "Upgrade",
        "Sec-WebSocket-Version": "13",
        "Sec-WebSocket-Key": base64.b64encode(secrets.token_bytes(16)).decode(),
        "User-Agent": f"BrowserSandbox-Client/1.0 (Session: {client.session_id})",
    }
    if frozen.token:
        headers["X-Amz-Security-Token"] = frozen.token
    return f"wss://{host}{path}", headers


def _generate_live_view_url(
    client: BrowserClient, session: Any, expires: int = MAX_LIVE_VIEW_PRESIGNED_URL_TIMEOUT
) -> str:
    """``BrowserClient.generate_live_view_url`` signed with ``session`` (SDK uses ambient creds)."""
    if expires > MAX_LIVE_VIEW_PRESIGNED_URL_TIMEOUT:
        raise ValueError(f"Expiry timeout cannot exceed {MAX_LIVE_VIEW_PRESIGNED_URL_TIMEOUT} seconds, got {expires}")
    if not client.identifier or not client.session_id:
        client.start()

    url = urlparse(
        f"{get_data_plane_endpoint(client.region)}/browser-streams/{client.identifier}"
        f"/sessions/{client.session_id}/live-view"
    )
    request = AWSRequest(method="GET", url=url.geturl(), headers={"host": url.hostname})
    SigV4QueryAuth(
        credentials=_frozen_credentials(session),
        service_name="bedrock-agentcore",
        region_name=client.region,
        expires=expires,
    ).add_auth(request)
    if not request.url:
        raise RuntimeError("Failed to generate live view url")
    return request.url


def _bind_browser_session(client: BrowserClient, session: Any, region: str | None = None) -> None:
    """Make ``client``'s API calls and SigV4 signing use ``session``.

    The SDK builds its boto3 clients with the global ``boto3.client`` and signs
    the automation / live-view URLs with a fresh ambient ``boto3.Session()``,
    so the request's credentials would otherwise be ignored entirely.
    """
    rebind_agentcore_clients(
        client, session, control_plane="control_plane_client", data_plane="data_plane_client", region=region
    )
    client.generate_ws_headers = functools.partial(_generate_ws_headers, client, session)  # type: ignore[method-assign]
    client.generate_live_view_url = functools.partial(_generate_live_view_url, client, session)  # type: ignore[method-assign]


def _session_browser_client(session: Any) -> BrowserClient:
    """Build a ``BrowserClient`` bound to ``session`` (see ``_bind_browser_session``)."""
    client = BrowserClient(region=session.region_name)
    _bind_browser_session(client, session)
    return client


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
        # Per-session lock: rebinding mutates the shared SDK client, so the
        # rebind and the AWS call it serves must not interleave across requests.
        # Waiters keep their lock alive across stop; idle locks are reclaimed.
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
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
        client = _session_browser_client(get_boto3_session(default_region=self._region))

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
        # Only a confirmed remote stop may invalidate the persistent CDP
        # connection. The completion task owns cleanup after cancellation.

        async def close_resources(page: Any, browser: Any) -> None:
            for resource in (page, browser):
                if resource:
                    try:  # noqa: SIM105
                        await resource.close()
                    except Exception:
                        pass

        def forget() -> asyncio.Task[None]:
            page = self._pages.pop(session_id, None)
            browser = self._browsers.pop(session_id, None)
            self._sessions.pop(session_id, None)
            return asyncio.create_task(close_resources(page, browser))

        if session_id in self._sessions:
            await self._call(session_id, lambda c: c.stop(), after=forget)
        else:
            await forget()

    def _client(self, session_id: str) -> BrowserClient:
        """Return the session's SDK client rebound to the *current* request's AWS session.

        Browser sessions can outlive the request that started them (and its
        possibly short-lived STS credentials), so every later AWS call and
        URL signature uses the credentials in force now.  Only call while
        holding the session's lock (see ``_call``).
        """
        client = self._sessions.get(session_id)
        if not client:
            raise ValueError(f"Session {session_id} not found")
        _bind_browser_session(client, get_boto3_session(default_region=self._region), region=client.region)
        return client

    async def _call(
        self,
        session_id: str,
        fn: Callable[[BrowserClient], _T],
        *,
        after: Callable[[], asyncio.Task[None]] | None = None,
    ) -> _T:
        """Serialize rebinding through worker completion, including cancelled requests."""
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        await lock.acquire()
        completion: asyncio.Task[_T] | None = None
        try:

            async def finish() -> _T:
                try:
                    result: _T = await self._run_sync(lambda: fn(self._client(session_id)))
                    if after is not None:
                        await after()  # also runs when the request was cancelled
                    return result
                finally:
                    lock.release()

            completion = asyncio.create_task(finish())

            def consume(done: asyncio.Task[_T]) -> None:
                with suppress(asyncio.CancelledError):
                    done.exception()

            completion.add_done_callback(consume)
            return await asyncio.shield(completion)
        except BaseException:
            if completion is None:
                lock.release()  # task creation failed; no SDK worker exists
            raise

    async def _authorize_session(self, session_id: str) -> None:
        """Require AgentCore to authorize the current request and report READY."""
        info = await self._call(session_id, lambda c: c.get_session())
        if not isinstance(info, dict) or info.get("status") != "READY":
            raise ValueError(f"Browser session {session_id} is not active")

    async def _authorized_page(self, session_id: str) -> Any:
        """Revalidate the current caller with AgentCore before each CDP interaction.

        A persistent CDP connection remains usable after AWS keys are revoked.
        Do not cache even a successful read: the same access key ID may be
        presented with a different secret, or revoked between interactions.
        """
        page = self._get_page(session_id)
        await self._authorize_session(session_id)
        return page

    def _get_page(self, session_id: str) -> Any:
        page = self._pages.get(session_id)
        if page is None:
            raise ValueError(
                f"No Playwright page for session {session_id}. Session may not have started or CDP connection failed."
            )
        return page

    async def get_session(self, session_id: str) -> dict[str, Any]:
        await self._authorize_session(session_id)
        page = self._pages.get(session_id)
        return {
            "session_id": session_id,
            "status": "active",
            "url": page.url if page else None,
        }

    async def list_sessions(self, status: str | None = None) -> list[dict[str, Any]]:
        return [{"session_id": sid, "status": "active"} for sid in self._sessions]

    async def get_live_view_url(self, session_id: str, expires: int = MAX_LIVE_VIEW_PRESIGNED_URL_TIMEOUT) -> str:
        return await self._call(session_id, lambda c: c.generate_live_view_url(expires=expires))

    # ── Browser interaction via Playwright ──────────────────────────

    async def navigate(self, session_id: str, url: str) -> dict[str, Any]:
        page = await self._authorized_page(session_id)
        response = await page.goto(url, wait_until="domcontentloaded")
        return {
            "url": page.url,
            "status": response.status if response else None,
            "title": await page.title(),
        }

    async def screenshot(self, session_id: str) -> str:
        page = await self._authorized_page(session_id)
        data = await page.screenshot(type="png")
        return base64.b64encode(data).decode()

    async def get_page_content(self, session_id: str) -> str:
        page = await self._authorized_page(session_id)
        result: str = await page.evaluate("document.body?.innerText || ''")
        return result

    async def click(self, session_id: str, selector: str) -> dict[str, Any]:
        page = await self._authorized_page(session_id)
        await page.click(selector)
        return {"status": "clicked", "selector": selector}

    async def type_text(self, session_id: str, selector: str, text: str) -> dict[str, Any]:
        page = await self._authorized_page(session_id)
        await page.fill(selector, text)
        return {"status": "typed", "selector": selector, "text": text}

    async def evaluate(self, session_id: str, expression: str) -> Any:
        page = await self._authorized_page(session_id)
        # Playwright wraps the expression in a function, so bare `return` is illegal.
        # Strip leading `return` if the LLM included it.
        expr = expression.strip()
        if expr.startswith("return "):
            expr = expr[7:]
        return await page.evaluate(expr)

    async def healthcheck(self) -> bool:
        return True
