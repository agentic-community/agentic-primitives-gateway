from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.registry import registry


class TestBrowserInteractionRoutes:
    """Tests for browser interaction endpoints (navigate, screenshot, content, click, type, evaluate)
    and error paths for get_session and live_view."""

    def setup_method(self):
        from agentic_primitives_gateway.config import Settings

        test_settings = Settings(
            providers={
                "memory": {"backend": "agentic_primitives_gateway.primitives.memory.in_memory.InMemoryProvider"},
                "observability": {
                    "backend": "agentic_primitives_gateway.primitives.observability.noop.NoopObservabilityProvider"
                },
                "gateway": {"backend": "agentic_primitives_gateway.primitives.gateway.noop.NoopGatewayProvider"},
                "tools": {"backend": "agentic_primitives_gateway.primitives.tools.noop.NoopToolsProvider"},
                "identity": {"backend": "agentic_primitives_gateway.primitives.identity.noop.NoopIdentityProvider"},
                "code_interpreter": {
                    "backend": "agentic_primitives_gateway.primitives.code_interpreter.noop.NoopCodeInterpreterProvider"
                },
                "browser": {"backend": "agentic_primitives_gateway.primitives.browser.noop.NoopBrowserProvider"},
            }
        )
        registry.initialize(test_settings)
        self.client = TestClient(app, raise_server_exceptions=False)

    def _patch_browser(self, method_name, **kwargs):
        return patch.object(
            registry.get_primitive("browser").get()._provider,
            method_name,
            new_callable=AsyncMock,
            **kwargs,
        )

    # ── navigate ──────────────────────────────────────────────────────

    def test_navigate_success(self):
        with self._patch_browser(
            "navigate", return_value={"url": "https://example.com", "status": 200, "title": "Example"}
        ):
            resp = self.client.post("/api/v1/browser/sessions/s1/navigate", json={"url": "https://example.com"})
            assert resp.status_code == 200
            assert resp.json()["url"] == "https://example.com"

    def test_navigate_value_error_returns_400(self):
        with self._patch_browser("navigate", side_effect=ValueError("no page")):
            resp = self.client.post("/api/v1/browser/sessions/s1/navigate", json={"url": "https://example.com"})
            assert resp.status_code == 400
            assert "no page" in resp.json()["detail"]

    def test_navigate_not_implemented_returns_400(self):
        with self._patch_browser("navigate", side_effect=NotImplementedError("not supported")):
            resp = self.client.post("/api/v1/browser/sessions/s1/navigate", json={"url": "https://example.com"})
            assert resp.status_code == 400

    # ── screenshot ────────────────────────────────────────────────────

    def test_screenshot_success(self):
        with self._patch_browser("screenshot", return_value="base64data"):
            resp = self.client.get("/api/v1/browser/sessions/s1/screenshot")
            assert resp.status_code == 200
            assert resp.json() == {"format": "png", "data": "base64data"}

    def test_screenshot_error_returns_400(self):
        with self._patch_browser("screenshot", side_effect=ValueError("no session")):
            resp = self.client.get("/api/v1/browser/sessions/s1/screenshot")
            assert resp.status_code == 400

    # ── content ───────────────────────────────────────────────────────

    def test_get_page_content_success(self):
        with self._patch_browser("get_page_content", return_value="Hello World"):
            resp = self.client.get("/api/v1/browser/sessions/s1/content")
            assert resp.status_code == 200
            assert resp.json()["content"] == "Hello World"

    def test_get_page_content_error_returns_400(self):
        with self._patch_browser("get_page_content", side_effect=NotImplementedError("nope")):
            resp = self.client.get("/api/v1/browser/sessions/s1/content")
            assert resp.status_code == 400

    # ── click ─────────────────────────────────────────────────────────

    def test_click_success(self):
        with self._patch_browser("click", return_value={"status": "clicked", "selector": "#btn"}):
            resp = self.client.post("/api/v1/browser/sessions/s1/click", json={"selector": "#btn"})
            assert resp.status_code == 200
            assert resp.json()["status"] == "clicked"

    def test_click_error_returns_400(self):
        with self._patch_browser("click", side_effect=ValueError("not found")):
            resp = self.client.post("/api/v1/browser/sessions/s1/click", json={"selector": "#btn"})
            assert resp.status_code == 400

    # ── type_text ─────────────────────────────────────────────────────

    def test_type_text_success(self):
        with self._patch_browser("type_text", return_value={"status": "typed", "selector": "#input", "text": "hello"}):
            resp = self.client.post("/api/v1/browser/sessions/s1/type", json={"selector": "#input", "text": "hello"})
            assert resp.status_code == 200
            assert resp.json()["text"] == "hello"

    def test_type_text_error_returns_400(self):
        with self._patch_browser("type_text", side_effect=ValueError("selector not found")):
            resp = self.client.post("/api/v1/browser/sessions/s1/type", json={"selector": "#input", "text": "hello"})
            assert resp.status_code == 400

    # ── evaluate ──────────────────────────────────────────────────────

    def test_evaluate_success(self):
        with self._patch_browser("evaluate", return_value=42):
            resp = self.client.post("/api/v1/browser/sessions/s1/evaluate", json={"expression": "1+1"})
            assert resp.status_code == 200
            assert resp.json()["result"] == 42

    def test_evaluate_error_returns_400(self):
        with self._patch_browser("evaluate", side_effect=NotImplementedError("no js")):
            resp = self.client.post("/api/v1/browser/sessions/s1/evaluate", json={"expression": "1+1"})
            assert resp.status_code == 400

    # ── get_session ───────────────────────────────────────────────────

    def test_get_session_not_found_returns_404(self):
        with self._patch_browser("get_session", side_effect=ValueError("Session not-exist not found")):
            resp = self.client.get("/api/v1/browser/sessions/not-exist")
            assert resp.status_code == 404
            assert "not found" in resp.json()["detail"]

    def test_get_session_success(self):
        with self._patch_browser("get_session", return_value={"session_id": "s1", "status": "active"}):
            resp = self.client.get("/api/v1/browser/sessions/s1")
            assert resp.status_code == 200
            assert resp.json()["session_id"] == "s1"

    # ── live_view ─────────────────────────────────────────────────────

    def test_get_live_view_not_found_returns_404(self):
        with self._patch_browser("get_live_view_url", side_effect=ValueError("Session x not found")):
            resp = self.client.get("/api/v1/browser/sessions/x/live-view")
            assert resp.status_code == 404

    def test_get_live_view_success(self):
        with self._patch_browser("get_live_view_url", return_value="https://live.example.com/view"):
            resp = self.client.get("/api/v1/browser/sessions/s1/live-view", params={"expires": 60})
            assert resp.status_code == 200
            assert resp.json()["url"] == "https://live.example.com/view"
            assert resp.json()["expires_in"] == 60
