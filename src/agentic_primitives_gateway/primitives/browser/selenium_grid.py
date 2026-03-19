from __future__ import annotations

import contextlib
import json
import logging
import urllib.request
import uuid
from typing import Any

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By

from agentic_primitives_gateway.context import get_service_credentials_or_defaults
from agentic_primitives_gateway.primitives._sync import SyncRunnerMixin
from agentic_primitives_gateway.primitives.browser.base import BrowserProvider

logger = logging.getLogger(__name__)

# JavaScript injected after every navigation to dismiss common popups
# (cookie banners, GDPR modals, newsletter overlays, etc.).
_DISMISS_POPUPS_JS = """
(function() {
    // Common cookie/consent banner selectors
    var selectors = [
        '[class*="cookie"] button[class*="accept"]',
        '[class*="cookie"] button[class*="agree"]',
        '[class*="consent"] button[class*="accept"]',
        '[class*="consent"] button[class*="agree"]',
        '[id*="cookie"] button[class*="accept"]',
        '[id*="cookie"] button',
        '[class*="gdpr"] button',
        'button[id*="accept"]',
        '.cc-btn.cc-dismiss',
        '#onetrust-accept-btn-handler',
        '.fc-cta-consent',
        '[data-testid="cookie-policy-dialog-accept-button"]',
        '[aria-label="Accept cookies"]',
        '[aria-label="Accept all"]',
        '[aria-label="Close"]',
    ];
    for (var i = 0; i < selectors.length; i++) {
        try {
            var el = document.querySelector(selectors[i]);
            if (el && el.offsetParent !== null) { el.click(); return; }
        } catch(e) {}
    }

    // Remove fixed/sticky overlays blocking content
    var all = document.querySelectorAll('*');
    for (var j = 0; j < all.length; j++) {
        var style = window.getComputedStyle(all[j]);
        if ((style.position === 'fixed' || style.position === 'sticky') &&
            style.zIndex > 999 && all[j].offsetHeight > 100) {
            all[j].remove();
        }
    }
})();
"""


def _clean_selenium_error(e: WebDriverException) -> ValueError:
    """Convert a Selenium exception to a ValueError with a clean message.

    Strips the verbose stacktrace so the LLM gets a concise, actionable
    error it can use to adjust its strategy.
    """
    # Extract just the first line of the message (before Stacktrace:)
    msg = str(e).split("\nStacktrace:")[0].split("\n")[0].strip()
    cls_name = type(e).__name__
    return ValueError(f"{cls_name}: {msg}")


class SeleniumGridBrowserProvider(BrowserProvider, SyncRunnerMixin):
    """Browser provider backed by a Selenium Grid hub.

    Connects to a remote Selenium Grid via WebDriver protocol. All browser
    interaction is done through the standard Selenium API.

    Provider config example::

        backend: agentic_primitives_gateway.primitives.browser.selenium_grid.SeleniumGridBrowserProvider
        config:
          hub_url: "http://localhost:4444"
          browser: "chrome"
    """

    def __init__(
        self,
        hub_url: str = "http://localhost:4444",
        browser: str = "chrome",
        **kwargs: Any,
    ) -> None:
        self._default_hub_url = hub_url.rstrip("/")
        self._default_browser = browser
        self._sessions: dict[str, webdriver.Remote] = {}
        logger.info(
            "SeleniumGrid browser provider initialized (hub=%s, browser=%s)",
            self._default_hub_url,
            self._default_browser,
        )

    def _resolve_config(self) -> tuple[str, str]:
        """Resolve hub URL and browser from request context, falling back to config defaults."""
        creds = get_service_credentials_or_defaults(
            "selenium",
            {
                "hub_url": self._default_hub_url,
                "browser": self._default_browser,
            },
        )
        hub_url = (creds.get("hub_url") or self._default_hub_url).rstrip("/")
        browser = creds.get("browser") or self._default_browser
        return hub_url, browser

    @staticmethod
    def _build_options(
        browser_name: str,
    ) -> webdriver.ChromeOptions | webdriver.FirefoxOptions | webdriver.EdgeOptions:
        name = browser_name.lower()
        if name == "firefox":
            return webdriver.FirefoxOptions()
        if name == "edge":
            return webdriver.EdgeOptions()
        return webdriver.ChromeOptions()

    async def start_session(
        self,
        session_id: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        sid = session_id or str(uuid.uuid4())
        hub_url, browser_name = self._resolve_config()
        options = self._build_options(browser_name)

        def _create() -> webdriver.Remote:
            return webdriver.Remote(
                command_executor=hub_url,
                options=options,
            )

        try:
            driver = await self._run_sync(_create)
        except Exception as e:
            err_msg = str(e)
            if "Could not start a new session" in err_msg or "timed out" in err_msg.lower():
                raise ValueError(
                    "Selenium Grid has no available browser slots. "
                    "Either wait for existing sessions to finish or scale up the grid."
                ) from e
            raise

        viewport = (config or {}).get("viewport")
        if viewport:
            width = viewport.get("width", 1920)
            height = viewport.get("height", 1080)
            await self._run_sync(driver.set_window_size, width, height)

        self._sessions[sid] = driver
        logger.info("Selenium Grid session started: %s (driver session %s)", sid, driver.session_id)

        return {
            "session_id": sid,
            "status": "active",
            "viewport": viewport,
        }

    async def stop_session(self, session_id: str) -> None:
        driver = self._sessions.pop(session_id, None)
        if driver:
            with contextlib.suppress(Exception):
                await self._run_sync(driver.quit)

    def _get_driver(self, session_id: str) -> webdriver.Remote:
        driver = self._sessions.get(session_id)
        if driver is None:
            raise ValueError(f"Session {session_id} not found")
        return driver

    async def get_session(self, session_id: str) -> dict[str, Any]:
        driver = self._get_driver(session_id)
        url = await self._run_sync(lambda: driver.current_url)
        return {
            "session_id": session_id,
            "status": "active",
            "url": url,
        }

    async def list_sessions(self, status: str | None = None) -> list[dict[str, Any]]:
        return [{"session_id": sid, "status": "active"} for sid in self._sessions]

    async def get_live_view_url(self, session_id: str, expires: int = 300) -> str:
        driver = self._get_driver(session_id)
        hub_url, _ = self._resolve_config()
        return f"{hub_url}/#/sessions/{driver.session_id}"

    # ── Browser interaction via Selenium WebDriver ─────────────────

    async def navigate(self, session_id: str, url: str) -> dict[str, Any]:
        driver = self._get_driver(session_id)
        try:
            await self._run_sync(driver.get, url)

            # Auto-dismiss common popups (cookie banners, modals, etc.)
            with contextlib.suppress(WebDriverException):
                await self._run_sync(driver.execute_script, _DISMISS_POPUPS_JS)

            title: str = await self._run_sync(lambda: driver.title)
            current_url: str = await self._run_sync(lambda: driver.current_url)
        except WebDriverException as e:
            raise _clean_selenium_error(e) from None
        return {
            "url": current_url,
            "status": 200,
            "title": title,
        }

    async def screenshot(self, session_id: str) -> str:
        driver = self._get_driver(session_id)
        try:
            data: str = await self._run_sync(driver.get_screenshot_as_base64)
        except WebDriverException as e:
            raise _clean_selenium_error(e) from None
        return data

    async def get_page_content(self, session_id: str) -> str:
        driver = self._get_driver(session_id)

        def _get_text() -> str:
            body = driver.find_element(By.TAG_NAME, "body")
            result: str = body.text
            return result

        try:
            result: str = await self._run_sync(_get_text)
        except WebDriverException as e:
            raise _clean_selenium_error(e) from None
        return result

    async def click(self, session_id: str, selector: str) -> dict[str, Any]:
        driver = self._get_driver(session_id)

        def _click() -> None:
            el = driver.find_element(By.CSS_SELECTOR, selector)
            # Scroll into view to avoid fixed headers/footers
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
            try:
                el.click()
            except Exception:
                # Fallback: JS click bypasses overlay interception
                driver.execute_script("arguments[0].click();", el)

        try:
            await self._run_sync(_click)
        except WebDriverException as e:
            raise _clean_selenium_error(e) from None
        return {"status": "clicked", "selector": selector}

    async def type_text(self, session_id: str, selector: str, text: str) -> dict[str, Any]:
        driver = self._get_driver(session_id)

        def _type() -> None:
            el = driver.find_element(By.CSS_SELECTOR, selector)
            el.clear()
            el.send_keys(text)

        try:
            await self._run_sync(_type)
        except WebDriverException as e:
            raise _clean_selenium_error(e) from None
        return {"status": "typed", "selector": selector, "text": text}

    async def evaluate(self, session_id: str, expression: str) -> Any:
        driver = self._get_driver(session_id)
        script = f"return {expression}" if not expression.strip().startswith("return") else expression
        try:
            return await self._run_sync(driver.execute_script, script)
        except WebDriverException as e:
            raise _clean_selenium_error(e) from None

    async def healthcheck(self) -> bool:
        hub_url = self._default_hub_url

        def _check() -> bool:
            req = urllib.request.Request(f"{hub_url}/status")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                return bool(data.get("value", {}).get("ready", False))

        try:
            result: bool = await self._run_sync(_check)
            return result
        except Exception:
            return False
