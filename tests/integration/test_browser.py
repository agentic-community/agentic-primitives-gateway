"""Integration tests for the AgentCore browser primitive.

Full stack with real AWS calls:
AgenticPlatformClient → ASGI → middleware → route → registry →
AgentCoreBrowserProvider → real BrowserClient + Playwright.

Requires: AWS credentials + playwright installed.
"""

from __future__ import annotations

import base64

import pytest

from agentic_primitives_gateway_client import AgenticPlatformClient

pytestmark = [pytest.mark.integration, pytest.mark.browser]


# ── Navigation & content ─────────────────────────────────────────────


class TestNavigateAndContent:
    async def test_navigate_and_content(self, client: AgenticPlatformClient, browser_session: str) -> None:
        result = await client.browser_navigate(browser_session, "https://example.com")

        assert result["status"] == 200
        assert "example.com" in result["url"]
        assert result["title"]  # Should have a title

        content = await client.browser_get_content(browser_session)

        assert "content" in content
        assert "Example Domain" in content["content"]


class TestScreenshot:
    async def test_screenshot(self, client: AgenticPlatformClient, browser_session: str) -> None:
        await client.browser_navigate(browser_session, "https://example.com")

        result = await client.browser_screenshot(browser_session)

        assert result["format"] == "png"
        assert result["data"]
        # Verify it's valid base64 that decodes to PNG-like bytes
        raw = base64.b64decode(result["data"])
        assert raw[:4] == b"\x89PNG"


class TestClickAndType:
    async def test_click_and_type(self, client: AgenticPlatformClient, browser_session: str) -> None:
        # Navigate to a page with a form — use a simple test page
        await client.browser_navigate(browser_session, "https://www.w3schools.com/html/html_forms.asp")

        # Type text into an input (best-effort — page structure may change)
        try:
            result = await client.browser_type(browser_session, "input[type='text']", "integration test")
            assert result["status"] == "typed"
        except Exception:
            pytest.skip("Form page structure changed — skipping click/type test")


class TestEvaluateJs:
    async def test_evaluate_js(self, client: AgenticPlatformClient, browser_session: str) -> None:
        await client.browser_navigate(browser_session, "https://example.com")

        result = await client.browser_evaluate(browser_session, "document.title")

        assert "result" in result
        assert "Example Domain" in str(result["result"])


# ── Session management ───────────────────────────────────────────────


class TestSessionLifecycle:
    async def test_session_lifecycle(self, client: AgenticPlatformClient) -> None:
        """Start, get, list, stop — no fixture."""
        started = await client.start_browser_session()
        sid = started["session_id"]

        try:
            # Get
            session = await client.get_browser_session(sid)
            assert session["session_id"] == sid
            assert session["status"] == "active"

            # List
            listed = await client.list_browser_sessions()
            assert "sessions" in listed
            ids = [s["session_id"] for s in listed["sessions"]]
            assert sid in ids
        finally:
            await client.stop_browser_session(sid)


class TestLiveViewUrl:
    async def test_live_view_url(self, client: AgenticPlatformClient, browser_session: str) -> None:
        result = await client.get_live_view_url(browser_session)

        assert "url" in result
        url = result["url"]
        assert url.startswith("http://") or url.startswith("https://")
