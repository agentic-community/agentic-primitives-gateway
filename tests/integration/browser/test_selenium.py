"""Integration tests for the Selenium Grid browser primitive.

Full stack with real Selenium Grid calls:
AgenticPlatformClient → ASGI → middleware → route → registry →
SeleniumGridBrowserProvider → real Selenium Grid hub.

Requires:
  - A running Selenium Grid hub
  - Optionally SELENIUM_HUB_URL env var (default: http://localhost:4444)
"""

from __future__ import annotations

import base64
import os

import httpx
import pytest

import agentic_primitives_gateway.config as _config_module
from agentic_primitives_gateway.config import Settings
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway_client import AgenticPlatformClient

pytestmark = pytest.mark.integration


# ── Skip logic ────────────────────────────────────────────────────────

if not os.environ.get("SELENIUM_HUB_URL"):
    pytest.skip(
        "SELENIUM_HUB_URL not set — skipping Selenium Grid integration tests",
        allow_module_level=True,
    )

FAKE_AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
FAKE_AWS_SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
FAKE_AWS_REGION = "us-east-1"


# ── Registry initialization ──────────────────────────────────────────


@pytest.fixture(autouse=True)
def _init_registry():
    """Initialise registry with Selenium Grid browser provider (noop for everything else).

    Selenium Hub URL is read from env vars and baked into the provider config
    so the provider doesn't need per-request credential headers.
    """
    hub_url = os.environ.get("SELENIUM_HUB_URL", "http://localhost:4444")

    test_settings = Settings(
        allow_server_credentials="always",
        providers={
            "memory": {
                "backend": "agentic_primitives_gateway.primitives.memory.noop.NoopMemoryProvider",
                "config": {},
            },
            "observability": {
                "backend": "agentic_primitives_gateway.primitives.observability.noop.NoopObservabilityProvider",
                "config": {},
            },
            "llm": {
                "backend": "agentic_primitives_gateway.primitives.llm.noop.NoopLLMProvider",
                "config": {},
            },
            "tools": {
                "backend": "agentic_primitives_gateway.primitives.tools.noop.NoopToolsProvider",
                "config": {},
            },
            "identity": {
                "backend": "agentic_primitives_gateway.primitives.identity.noop.NoopIdentityProvider",
                "config": {},
            },
            "code_interpreter": {
                "backend": "agentic_primitives_gateway.primitives.code_interpreter.noop.NoopCodeInterpreterProvider",
                "config": {},
            },
            "browser": {
                "backend": ("agentic_primitives_gateway.primitives.browser.selenium_grid.SeleniumGridBrowserProvider"),
                "config": {
                    "hub_url": hub_url,
                    "browser": "chrome",
                },
            },
        },
    )
    orig_settings = _config_module.settings
    _config_module.settings = test_settings
    registry.initialize(test_settings)

    yield

    _config_module.settings = orig_settings


# ── Client fixture ───────────────────────────────────────────────────


@pytest.fixture
async def client():
    """AgenticPlatformClient wired to ASGI app with fake AWS creds.

    Selenium doesn't need AWS credentials — they're baked into the provider
    config. We use fake AWS creds to satisfy the middleware.
    """
    transport = httpx.ASGITransport(app=app)
    async with AgenticPlatformClient(
        base_url="http://testserver",
        aws_access_key_id=FAKE_AWS_ACCESS_KEY,
        aws_secret_access_key=FAKE_AWS_SECRET_KEY,
        aws_region=FAKE_AWS_REGION,
        max_retries=2,
        transport=transport,
    ) as c:
        yield c


# ── Session fixture ──────────────────────────────────────────────────


@pytest.fixture
async def browser_session(client: AgenticPlatformClient):
    """Start a browser session, yield ID, stop on teardown."""
    result = await client.start_browser_session()
    sid = result["session_id"]
    yield sid
    try:  # noqa: SIM105
        await client.stop_browser_session(sid)
    except Exception:
        pass


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


# ── Screenshot ────────────────────────────────────────────────────────


class TestScreenshot:
    async def test_screenshot(self, client: AgenticPlatformClient, browser_session: str) -> None:
        await client.browser_navigate(browser_session, "https://example.com")

        result = await client.browser_screenshot(browser_session)

        assert result["format"] == "png"
        assert result["data"]
        # Verify it's valid base64 that decodes to PNG-like bytes
        raw = base64.b64decode(result["data"])
        assert raw[:4] == b"\x89PNG"


# ── Click & type ──────────────────────────────────────────────────────


class TestClickAndType:
    async def test_click(self, client: AgenticPlatformClient, browser_session: str) -> None:
        """Click an element on example.com — the 'More information...' link."""
        await client.browser_navigate(browser_session, "https://example.com")

        result = await client.browser_click(browser_session, "a")

        assert result["status"] == "clicked"

    async def test_type_text(self, client: AgenticPlatformClient, browser_session: str) -> None:
        """Navigate to a page with an input and type into it.

        Uses a data URI to avoid reliance on external sites with forms.
        """
        data_uri = 'data:text/html,<html><body><input id="name" type="text" /></body></html>'
        await client.browser_navigate(browser_session, data_uri)

        result = await client.browser_type(browser_session, "input#name", "integration test")

        assert result["status"] == "typed"


# ── Evaluate JavaScript ──────────────────────────────────────────────


class TestEvaluateJs:
    async def test_evaluate_js(self, client: AgenticPlatformClient, browser_session: str) -> None:
        await client.browser_navigate(browser_session, "https://example.com")

        result = await client.browser_evaluate(browser_session, "document.title")

        assert "result" in result
        assert "Example Domain" in str(result["result"])

    async def test_evaluate_js_arithmetic(self, client: AgenticPlatformClient, browser_session: str) -> None:
        await client.browser_navigate(browser_session, "https://example.com")

        result = await client.browser_evaluate(browser_session, "2 + 2")

        assert "result" in result
        assert result["result"] == 4


# ── Live view URL ────────────────────────────────────────────────────


class TestLiveViewUrl:
    async def test_live_view_url(self, client: AgenticPlatformClient, browser_session: str) -> None:
        result = await client.get_live_view_url(browser_session)

        assert "url" in result
        url = result["url"]
        assert url.startswith("http://") or url.startswith("https://")
