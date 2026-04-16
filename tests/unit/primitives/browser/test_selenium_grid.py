from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agentic_primitives_gateway.primitives.browser.selenium_grid import SeleniumGridBrowserProvider

# Patch context so _resolve_config returns server defaults without request context
_PATCH_CREDS = patch(
    "agentic_primitives_gateway.primitives.browser.selenium_grid.get_service_credentials_or_defaults",
    side_effect=lambda _service, defaults: defaults,
)


class TestSeleniumGridBrowserProvider:
    """Tests for the Selenium Grid browser provider."""

    # ── Session management ────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_start_session_success(self):
        mock_driver = MagicMock()
        mock_driver.session_id = "wd-session-abc"

        with (
            patch("agentic_primitives_gateway.primitives.browser.selenium_grid.webdriver") as mock_wd,
            _PATCH_CREDS,
        ):
            mock_wd.Remote.return_value = mock_driver
            mock_wd.ChromeOptions = MagicMock
            provider = SeleniumGridBrowserProvider(hub_url="http://grid:4444")

            result = await provider.start_session(session_id="my-sess")

        assert result["session_id"] == "my-sess"
        assert result["status"] == "active"
        assert "my-sess" in provider._sessions

    @pytest.mark.asyncio
    async def test_start_session_generates_id(self):
        mock_driver = MagicMock()

        with (
            patch("agentic_primitives_gateway.primitives.browser.selenium_grid.webdriver") as mock_wd,
            _PATCH_CREDS,
        ):
            mock_wd.Remote.return_value = mock_driver
            mock_wd.ChromeOptions = MagicMock
            provider = SeleniumGridBrowserProvider()

            result = await provider.start_session()

        assert result["session_id"]  # UUID generated
        assert result["status"] == "active"
        assert len(provider._sessions) == 1

    @pytest.mark.asyncio
    async def test_start_session_with_viewport(self):
        mock_driver = MagicMock()

        with (
            patch("agentic_primitives_gateway.primitives.browser.selenium_grid.webdriver") as mock_wd,
            _PATCH_CREDS,
        ):
            mock_wd.Remote.return_value = mock_driver
            mock_wd.ChromeOptions = MagicMock
            provider = SeleniumGridBrowserProvider()

            result = await provider.start_session(
                session_id="vp-sess",
                config={"viewport": {"width": 800, "height": 600}},
            )

        assert result["viewport"] == {"width": 800, "height": 600}
        mock_driver.set_window_size.assert_called_once_with(800, 600)

    @pytest.mark.asyncio
    async def test_start_session_uses_client_credentials(self):
        """Client-provided hub_url via X-Cred-Selenium-Hub-Url should override config."""
        mock_driver = MagicMock()
        mock_driver.session_id = "wd-123"

        client_creds = {"hub_url": "http://custom-grid:9999", "browser": "firefox"}

        with (
            patch("agentic_primitives_gateway.primitives.browser.selenium_grid.webdriver") as mock_wd,
            patch(
                "agentic_primitives_gateway.primitives.browser.selenium_grid.get_service_credentials_or_defaults",
                return_value=client_creds,
            ),
        ):
            mock_wd.Remote.return_value = mock_driver
            mock_wd.FirefoxOptions = MagicMock
            provider = SeleniumGridBrowserProvider(hub_url="http://default:4444", browser="chrome")

            result = await provider.start_session(session_id="s1")

        # Should have connected to the client-provided URL
        mock_wd.Remote.assert_called_once()
        call_kwargs = mock_wd.Remote.call_args
        assert call_kwargs[1]["command_executor"] == "http://custom-grid:9999"
        assert result["session_id"] == "s1"

    @pytest.mark.asyncio
    async def test_stop_session(self):
        provider = SeleniumGridBrowserProvider.__new__(SeleniumGridBrowserProvider)
        provider._sessions = {}
        mock_driver = MagicMock()
        provider._sessions["s1"] = mock_driver

        await provider.stop_session("s1")

        mock_driver.quit.assert_called_once()
        assert "s1" not in provider._sessions

    @pytest.mark.asyncio
    async def test_stop_session_not_found(self):
        provider = SeleniumGridBrowserProvider.__new__(SeleniumGridBrowserProvider)
        provider._sessions = {}

        # Should not raise
        await provider.stop_session("missing")

    @pytest.mark.asyncio
    async def test_stop_session_quit_error_suppressed(self):
        provider = SeleniumGridBrowserProvider.__new__(SeleniumGridBrowserProvider)
        provider._sessions = {}
        mock_driver = MagicMock()
        mock_driver.quit.side_effect = Exception("already closed")
        provider._sessions["s1"] = mock_driver

        await provider.stop_session("s1")
        assert "s1" not in provider._sessions

    @pytest.mark.asyncio
    async def test_get_session_found(self):
        provider = SeleniumGridBrowserProvider.__new__(SeleniumGridBrowserProvider)
        provider._sessions = {}
        mock_driver = MagicMock()
        mock_driver.current_url = "https://example.com"
        provider._sessions["s1"] = mock_driver

        result = await provider.get_session("s1")
        assert result["session_id"] == "s1"
        assert result["status"] == "active"
        assert result["url"] == "https://example.com"

    @pytest.mark.asyncio
    async def test_get_session_not_found(self):
        provider = SeleniumGridBrowserProvider.__new__(SeleniumGridBrowserProvider)
        provider._sessions = {}

        with pytest.raises(ValueError, match="not found"):
            await provider.get_session("missing")

    @pytest.mark.asyncio
    async def test_list_sessions(self):
        provider = SeleniumGridBrowserProvider.__new__(SeleniumGridBrowserProvider)
        provider._sessions = {"s1": MagicMock(), "s2": MagicMock()}

        result = await provider.list_sessions()
        assert len(result) == 2
        ids = {s["session_id"] for s in result}
        assert ids == {"s1", "s2"}

    @pytest.mark.asyncio
    async def test_list_sessions_empty(self):
        provider = SeleniumGridBrowserProvider.__new__(SeleniumGridBrowserProvider)
        provider._sessions = {}

        result = await provider.list_sessions()
        assert result == []

    @pytest.mark.asyncio
    async def test_get_live_view_url(self):
        provider = SeleniumGridBrowserProvider.__new__(SeleniumGridBrowserProvider)
        provider._default_hub_url = "http://grid:4444"
        provider._default_browser = "chrome"
        provider._sessions = {}
        mock_driver = MagicMock()
        mock_driver.session_id = "wd-abc123"
        provider._sessions["s1"] = mock_driver

        with _PATCH_CREDS:
            result = await provider.get_live_view_url("s1")
        assert result == "http://grid:4444/#/sessions/wd-abc123"

    @pytest.mark.asyncio
    async def test_get_live_view_url_not_found(self):
        provider = SeleniumGridBrowserProvider.__new__(SeleniumGridBrowserProvider)
        provider._sessions = {}

        with pytest.raises(ValueError, match="not found"):
            await provider.get_live_view_url("missing")

    # ── Browser interaction ───────────────────────────────────────

    @pytest.mark.asyncio
    async def test_navigate(self):
        provider = SeleniumGridBrowserProvider.__new__(SeleniumGridBrowserProvider)
        provider._sessions = {}
        mock_driver = MagicMock()
        mock_driver.title = "Example"
        mock_driver.current_url = "https://example.com"
        provider._sessions["s1"] = mock_driver

        result = await provider.navigate("s1", "https://example.com")

        mock_driver.get.assert_called_once_with("https://example.com")
        assert result["url"] == "https://example.com"
        assert result["title"] == "Example"
        assert result["status"] == 200

    @pytest.mark.asyncio
    async def test_navigate_not_found(self):
        provider = SeleniumGridBrowserProvider.__new__(SeleniumGridBrowserProvider)
        provider._sessions = {}

        with pytest.raises(ValueError, match="not found"):
            await provider.navigate("missing", "https://example.com")

    @pytest.mark.asyncio
    async def test_screenshot(self):
        provider = SeleniumGridBrowserProvider.__new__(SeleniumGridBrowserProvider)
        provider._sessions = {}
        mock_driver = MagicMock()
        mock_driver.get_screenshot_as_base64.return_value = "iVBORw0KGgo="
        provider._sessions["s1"] = mock_driver

        result = await provider.screenshot("s1")
        assert result == "iVBORw0KGgo="
        mock_driver.get_screenshot_as_base64.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_page_content(self):
        provider = SeleniumGridBrowserProvider.__new__(SeleniumGridBrowserProvider)
        provider._sessions = {}
        mock_driver = MagicMock()
        mock_body = MagicMock()
        mock_body.text = "Hello world"
        mock_driver.find_element.return_value = mock_body
        provider._sessions["s1"] = mock_driver

        result = await provider.get_page_content("s1")
        assert result == "Hello world"

    @pytest.mark.asyncio
    async def test_click(self):
        provider = SeleniumGridBrowserProvider.__new__(SeleniumGridBrowserProvider)
        provider._sessions = {}
        mock_driver = MagicMock()
        mock_element = MagicMock()
        mock_driver.find_element.return_value = mock_element
        provider._sessions["s1"] = mock_driver

        result = await provider.click("s1", "#btn")

        assert result["status"] == "clicked"
        assert result["selector"] == "#btn"
        mock_element.click.assert_called_once()

    @pytest.mark.asyncio
    async def test_type_text(self):
        provider = SeleniumGridBrowserProvider.__new__(SeleniumGridBrowserProvider)
        provider._sessions = {}
        mock_driver = MagicMock()
        mock_element = MagicMock()
        mock_driver.find_element.return_value = mock_element
        provider._sessions["s1"] = mock_driver

        result = await provider.type_text("s1", "#input", "hello")

        assert result["status"] == "typed"
        assert result["text"] == "hello"
        mock_element.clear.assert_called_once()
        mock_element.send_keys.assert_called_once_with("hello")

    @pytest.mark.asyncio
    async def test_evaluate(self):
        provider = SeleniumGridBrowserProvider.__new__(SeleniumGridBrowserProvider)
        provider._sessions = {}
        mock_driver = MagicMock()
        mock_driver.execute_script.return_value = 42
        provider._sessions["s1"] = mock_driver

        result = await provider.evaluate("s1", "1 + 1")
        assert result == 42
        mock_driver.execute_script.assert_called_once_with("return 1 + 1")

    @pytest.mark.asyncio
    async def test_evaluate_with_return(self):
        provider = SeleniumGridBrowserProvider.__new__(SeleniumGridBrowserProvider)
        provider._sessions = {}
        mock_driver = MagicMock()
        mock_driver.execute_script.return_value = "ok"
        provider._sessions["s1"] = mock_driver

        result = await provider.evaluate("s1", "return document.title")
        assert result == "ok"
        # Should NOT double-prefix with "return"
        mock_driver.execute_script.assert_called_once_with("return document.title")

    # ── Healthcheck ───────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_healthcheck_success(self):
        provider = SeleniumGridBrowserProvider.__new__(SeleniumGridBrowserProvider)
        provider._default_hub_url = "http://grid:4444"

        response_data = json.dumps({"value": {"ready": True}}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_data
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = await provider.healthcheck()

        assert result is True

    @pytest.mark.asyncio
    async def test_healthcheck_not_ready(self):
        provider = SeleniumGridBrowserProvider.__new__(SeleniumGridBrowserProvider)
        provider._default_hub_url = "http://grid:4444"

        response_data = json.dumps({"value": {"ready": False}}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_data
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = await provider.healthcheck()

        assert result is False

    @pytest.mark.asyncio
    async def test_healthcheck_failure(self):
        provider = SeleniumGridBrowserProvider.__new__(SeleniumGridBrowserProvider)
        provider._default_hub_url = "http://grid:4444"

        with patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
            result = await provider.healthcheck()

        assert result is False

    # ── Browser options ───────────────────────────────────────────

    def test_build_options_chrome(self):
        with patch("agentic_primitives_gateway.primitives.browser.selenium_grid.webdriver") as mock_wd:
            mock_wd.ChromeOptions.return_value = "chrome-opts"
            opts = SeleniumGridBrowserProvider._build_options("chrome")
        assert opts == "chrome-opts"

    def test_build_options_firefox(self):
        with patch("agentic_primitives_gateway.primitives.browser.selenium_grid.webdriver") as mock_wd:
            mock_wd.FirefoxOptions.return_value = "firefox-opts"
            opts = SeleniumGridBrowserProvider._build_options("firefox")
        assert opts == "firefox-opts"

    def test_build_options_edge(self):
        with patch("agentic_primitives_gateway.primitives.browser.selenium_grid.webdriver") as mock_wd:
            mock_wd.EdgeOptions.return_value = "edge-opts"
            opts = SeleniumGridBrowserProvider._build_options("edge")
        assert opts == "edge-opts"
