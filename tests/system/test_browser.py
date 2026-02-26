"""System tests for the AgentCore browser primitive.

Full stack: AgenticPlatformClient → ASGI → middleware → route → registry →
AgentCoreBrowserProvider → (mocked) BrowserClient + Playwright.
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway_client import AgenticPlatformClient, AgenticPlatformError

# ── Helpers ───────────────────────────────────────────────────────────


def _mock_page() -> MagicMock:
    """Create a mock Playwright page with common async methods."""
    page = MagicMock()
    page.url = "about:blank"
    page.title = AsyncMock(return_value="")
    page.goto = AsyncMock()
    page.screenshot = AsyncMock(return_value=b"\x89PNG fake data")
    page.evaluate = AsyncMock(return_value="page text content")
    page.click = AsyncMock()
    page.fill = AsyncMock()
    page.close = AsyncMock()
    return page


async def _start_session_with_mocks(
    client: AgenticPlatformClient,
    mock_browser_client: MagicMock,
    session_id: str = "br-sess-1",
) -> MagicMock:
    """Start a browser session with all mocks wired up, return the mock page."""
    mock_browser_client.start.return_value = session_id
    mock_browser_client.generate_ws_headers.return_value = (
        "ws://fake-cdp:9222",
        {"Authorization": "Bearer tok"},
    )

    page = _mock_page()

    # Mock Playwright's connect_over_cdp chain
    mock_context = MagicMock()
    mock_context.new_page = AsyncMock(return_value=page)

    mock_browser = MagicMock()
    mock_browser.contexts = [mock_context]
    mock_browser.close = AsyncMock()

    mock_pw = MagicMock()
    mock_pw.chromium = MagicMock()
    mock_pw.chromium.connect_over_cdp = AsyncMock(return_value=mock_browser)

    provider = registry.get_primitive("browser").get()
    real_provider = provider._provider

    with patch.object(
        real_provider,
        "_ensure_playwright",
        new_callable=AsyncMock,
        return_value=mock_pw,
    ):
        await client.start_browser_session(session_id=session_id)

    return page


# ── Session management ────────────────────────────────────────────────


class TestStartSession:
    async def test_start_session(self, client: AgenticPlatformClient, mock_browser_client: MagicMock) -> None:
        await _start_session_with_mocks(client, mock_browser_client)

        # Verify session was created
        result = await client.get_browser_session("br-sess-1")
        assert result["session_id"] == "br-sess-1"
        assert result["status"] == "active"


class TestStopSession:
    async def test_stop_session(self, client: AgenticPlatformClient, mock_browser_client: MagicMock) -> None:
        await _start_session_with_mocks(client, mock_browser_client)
        mock_browser_client.stop.return_value = None

        await client.stop_browser_session("br-sess-1")


class TestGetSession:
    async def test_get_session(self, client: AgenticPlatformClient, mock_browser_client: MagicMock) -> None:
        await _start_session_with_mocks(client, mock_browser_client)

        result = await client.get_browser_session("br-sess-1")

        assert result["session_id"] == "br-sess-1"

    async def test_get_session_not_found(self, client: AgenticPlatformClient, mock_browser_client: MagicMock) -> None:
        with pytest.raises(AgenticPlatformError) as exc_info:
            await client.get_browser_session("missing")
        assert exc_info.value.status_code == 404


class TestListSessions:
    async def test_list_sessions(self, client: AgenticPlatformClient, mock_browser_client: MagicMock) -> None:
        await _start_session_with_mocks(client, mock_browser_client)

        result = await client.list_browser_sessions()

        assert "sessions" in result
        assert any(s["session_id"] == "br-sess-1" for s in result["sessions"])


class TestLiveViewUrl:
    async def test_get_live_view_url(self, client: AgenticPlatformClient, mock_browser_client: MagicMock) -> None:
        await _start_session_with_mocks(client, mock_browser_client)
        mock_browser_client.generate_live_view_url.return_value = "https://live.example.com/view/br-sess-1"

        result = await client.get_live_view_url("br-sess-1")

        assert result["url"] == "https://live.example.com/view/br-sess-1"


# ── Browser interaction ───────────────────────────────────────────────


class TestNavigate:
    async def test_navigate(self, client: AgenticPlatformClient, mock_browser_client: MagicMock) -> None:
        page = await _start_session_with_mocks(client, mock_browser_client)

        mock_response = MagicMock()
        mock_response.status = 200
        page.goto.return_value = mock_response
        page.url = "https://example.com"
        page.title.return_value = "Example Domain"

        result = await client.browser_navigate("br-sess-1", "https://example.com")

        assert result["url"] == "https://example.com"
        assert result["status"] == 200
        assert result["title"] == "Example Domain"

    async def test_navigate_no_session(self, client: AgenticPlatformClient, mock_browser_client: MagicMock) -> None:
        with pytest.raises(AgenticPlatformError) as exc_info:
            await client.browser_navigate("missing", "https://example.com")
        assert exc_info.value.status_code == 400


class TestScreenshot:
    async def test_screenshot(self, client: AgenticPlatformClient, mock_browser_client: MagicMock) -> None:
        page = await _start_session_with_mocks(client, mock_browser_client)
        png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        page.screenshot.return_value = png_data

        result = await client.browser_screenshot("br-sess-1")

        assert result["format"] == "png"
        assert result["data"] == base64.b64encode(png_data).decode()


class TestGetContent:
    async def test_get_content(self, client: AgenticPlatformClient, mock_browser_client: MagicMock) -> None:
        page = await _start_session_with_mocks(client, mock_browser_client)
        page.evaluate.return_value = "Hello World"

        result = await client.browser_get_content("br-sess-1")

        assert result["content"] == "Hello World"


class TestClick:
    async def test_click(self, client: AgenticPlatformClient, mock_browser_client: MagicMock) -> None:
        page = await _start_session_with_mocks(client, mock_browser_client)

        result = await client.browser_click("br-sess-1", "#submit-btn")

        assert result["status"] == "clicked"
        assert result["selector"] == "#submit-btn"
        page.click.assert_called_once_with("#submit-btn")


class TestTypeText:
    async def test_type_text(self, client: AgenticPlatformClient, mock_browser_client: MagicMock) -> None:
        page = await _start_session_with_mocks(client, mock_browser_client)

        result = await client.browser_type("br-sess-1", "#search", "hello")

        assert result["status"] == "typed"
        assert result["selector"] == "#search"
        page.fill.assert_called_once_with("#search", "hello")


class TestEvaluate:
    async def test_evaluate(self, client: AgenticPlatformClient, mock_browser_client: MagicMock) -> None:
        page = await _start_session_with_mocks(client, mock_browser_client)
        page.evaluate.return_value = 42

        result = await client.browser_evaluate("br-sess-1", "1 + 41")

        assert result["result"] == 42
