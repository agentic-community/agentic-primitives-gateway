from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_primitives_gateway.primitives.browser.agentcore import AgentCoreBrowserProvider


@patch("agentic_primitives_gateway.primitives.browser.agentcore.get_boto3_session")
@patch("agentic_primitives_gateway.primitives.browser.agentcore.BrowserClient")
class TestAgentCoreBrowserProvider:
    """Tests for the AgentCore browser provider."""

    @pytest.mark.asyncio
    async def test_start_session_success(self, mock_browser_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.start.return_value = "session-123"
        mock_client.generate_ws_headers.return_value = ("ws://cdp-url", {"Auth": "token"})
        mock_browser_client_cls.return_value = mock_client

        provider = AgentCoreBrowserProvider(region="us-east-1")

        mock_pw = MagicMock()
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = AsyncMock()
        mock_browser.contexts = [mock_context]
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_pw.chromium.connect_over_cdp = AsyncMock(return_value=mock_browser)

        with patch.object(provider, "_ensure_playwright", new_callable=AsyncMock, return_value=mock_pw):
            result = await provider.start_session(session_id="my-sess")

        assert result["session_id"] == "session-123"
        assert result["status"] == "active"
        assert "session-123" in provider._sessions

    @pytest.mark.asyncio
    async def test_start_session_cdp_failure(self, mock_browser_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.start.return_value = "sess-fail"
        mock_client.generate_ws_headers.side_effect = Exception("CDP failed")
        mock_browser_client_cls.return_value = mock_client

        provider = AgentCoreBrowserProvider()

        with patch.object(provider, "_ensure_playwright", new_callable=AsyncMock):
            result = await provider.start_session()

        # Should still succeed, just without Playwright
        assert result["session_id"] == "sess-fail"
        assert "sess-fail" in provider._sessions
        assert "sess-fail" not in provider._pages

    @pytest.mark.asyncio
    async def test_start_session_dict_result(self, mock_browser_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.start.return_value = {"sessionId": "dict-sess"}
        mock_client.generate_ws_headers.side_effect = Exception("skip CDP")
        mock_browser_client_cls.return_value = mock_client

        provider = AgentCoreBrowserProvider()
        with patch.object(provider, "_ensure_playwright", new_callable=AsyncMock):
            result = await provider.start_session()

        assert result["session_id"] == "dict-sess"

    @pytest.mark.asyncio
    async def test_stop_session(self, mock_browser_client_cls, mock_get_session):
        provider = AgentCoreBrowserProvider()
        mock_client = MagicMock()
        mock_page = AsyncMock()
        mock_browser = AsyncMock()

        provider._sessions["s1"] = mock_client
        provider._pages["s1"] = mock_page
        provider._browsers["s1"] = mock_browser

        await provider.stop_session("s1")

        mock_page.close.assert_awaited_once()
        mock_browser.close.assert_awaited_once()
        assert "s1" not in provider._sessions

    @pytest.mark.asyncio
    async def test_stop_session_cleanup_errors_suppressed(self, mock_browser_client_cls, mock_get_session):
        provider = AgentCoreBrowserProvider()
        mock_client = MagicMock()
        mock_page = AsyncMock()
        mock_page.close.side_effect = Exception("already closed")
        mock_browser = AsyncMock()
        mock_browser.close.side_effect = Exception("already closed")

        provider._sessions["s1"] = mock_client
        provider._pages["s1"] = mock_page
        provider._browsers["s1"] = mock_browser

        await provider.stop_session("s1")
        assert "s1" not in provider._sessions

    @pytest.mark.asyncio
    async def test_get_session_found(self, mock_browser_client_cls, mock_get_session):
        provider = AgentCoreBrowserProvider()
        provider._sessions["s1"] = MagicMock()
        mock_page = MagicMock()
        mock_page.url = "https://example.com"
        provider._pages["s1"] = mock_page

        result = await provider.get_session("s1")
        assert result["session_id"] == "s1"
        assert result["url"] == "https://example.com"

    @pytest.mark.asyncio
    async def test_get_session_not_found(self, mock_browser_client_cls, mock_get_session):
        provider = AgentCoreBrowserProvider()
        with pytest.raises(ValueError, match="not found"):
            await provider.get_session("missing")

    @pytest.mark.asyncio
    async def test_list_sessions(self, mock_browser_client_cls, mock_get_session):
        provider = AgentCoreBrowserProvider()
        provider._sessions["s1"] = MagicMock()
        provider._sessions["s2"] = MagicMock()

        result = await provider.list_sessions()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_get_live_view_url(self, mock_browser_client_cls, mock_get_session):
        provider = AgentCoreBrowserProvider()
        mock_client = MagicMock()
        mock_client.generate_live_view_url.return_value = "https://live.example.com"
        provider._sessions["s1"] = mock_client

        result = await provider.get_live_view_url("s1", expires=600)
        assert result == "https://live.example.com"

    @pytest.mark.asyncio
    async def test_get_live_view_url_not_found(self, mock_browser_client_cls, mock_get_session):
        provider = AgentCoreBrowserProvider()
        with pytest.raises(ValueError, match="not found"):
            await provider.get_live_view_url("missing")

    @pytest.mark.asyncio
    async def test_navigate(self, mock_browser_client_cls, mock_get_session):
        provider = AgentCoreBrowserProvider()
        mock_page = AsyncMock()
        mock_page.url = "https://example.com"
        mock_response = MagicMock()
        mock_response.status = 200
        mock_page.goto.return_value = mock_response
        mock_page.title.return_value = "Example"
        provider._pages["s1"] = mock_page

        result = await provider.navigate("s1", "https://example.com")
        assert result["url"] == "https://example.com"
        assert result["status"] == 200
        assert result["title"] == "Example"

    @pytest.mark.asyncio
    async def test_navigate_no_page_raises(self, mock_browser_client_cls, mock_get_session):
        provider = AgentCoreBrowserProvider()
        with pytest.raises(ValueError, match="No Playwright page"):
            await provider.navigate("missing", "https://example.com")

    @pytest.mark.asyncio
    async def test_screenshot(self, mock_browser_client_cls, mock_get_session):
        provider = AgentCoreBrowserProvider()
        mock_page = AsyncMock()
        mock_page.screenshot.return_value = b"\x89PNG"
        provider._pages["s1"] = mock_page

        result = await provider.screenshot("s1")
        assert isinstance(result, str)  # base64 encoded

    @pytest.mark.asyncio
    async def test_get_page_content(self, mock_browser_client_cls, mock_get_session):
        provider = AgentCoreBrowserProvider()
        mock_page = AsyncMock()
        mock_page.evaluate.return_value = "Page text content"
        provider._pages["s1"] = mock_page

        result = await provider.get_page_content("s1")
        assert result == "Page text content"

    @pytest.mark.asyncio
    async def test_click(self, mock_browser_client_cls, mock_get_session):
        provider = AgentCoreBrowserProvider()
        mock_page = AsyncMock()
        provider._pages["s1"] = mock_page

        result = await provider.click("s1", "#button")
        assert result["status"] == "clicked"
        mock_page.click.assert_awaited_once_with("#button")

    @pytest.mark.asyncio
    async def test_type_text(self, mock_browser_client_cls, mock_get_session):
        provider = AgentCoreBrowserProvider()
        mock_page = AsyncMock()
        provider._pages["s1"] = mock_page

        result = await provider.type_text("s1", "#input", "hello")
        assert result["status"] == "typed"
        mock_page.fill.assert_awaited_once_with("#input", "hello")

    @pytest.mark.asyncio
    async def test_evaluate(self, mock_browser_client_cls, mock_get_session):
        provider = AgentCoreBrowserProvider()
        mock_page = AsyncMock()
        mock_page.evaluate.return_value = {"key": "value"}
        provider._pages["s1"] = mock_page

        result = await provider.evaluate("s1", "JSON.parse('{}')")
        assert result == {"key": "value"}

    @pytest.mark.asyncio
    async def test_healthcheck(self, mock_browser_client_cls, mock_get_session):
        provider = AgentCoreBrowserProvider()
        assert await provider.healthcheck() is True

    @pytest.mark.asyncio
    async def test_start_session_no_contexts_creates_new(self, mock_browser_client_cls, mock_get_session):
        """When browser.contexts is empty, should create new context."""
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.start.return_value = "s-new"
        mock_client.generate_ws_headers.return_value = ("ws://url", {})
        mock_browser_client_cls.return_value = mock_client

        provider = AgentCoreBrowserProvider()

        mock_pw = MagicMock()
        mock_browser = MagicMock()
        mock_browser.contexts = []  # No existing contexts
        mock_new_context = AsyncMock()
        mock_page = AsyncMock()
        mock_new_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.new_context = AsyncMock(return_value=mock_new_context)
        mock_pw.chromium.connect_over_cdp = AsyncMock(return_value=mock_browser)

        with patch.object(provider, "_ensure_playwright", new_callable=AsyncMock, return_value=mock_pw):
            result = await provider.start_session()

        assert result["session_id"] == "s-new"
        mock_browser.new_context.assert_awaited_once()
