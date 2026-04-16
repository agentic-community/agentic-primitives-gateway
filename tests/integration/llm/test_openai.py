"""Integration tests for the OpenAI-compatible LLM provider.

Full stack with real LLM calls:
AgenticPlatformClient -> ASGI -> middleware -> route -> registry ->
OpenAICompatibleProvider -> local OpenAI-compatible server.

Requires:
  - An OpenAI-compatible server running (LM Studio, Ollama, vLLM, etc.)
  - OPENAI_COMPATIBLE_URL env var (default: http://localhost:1234)
  - Optionally OPENAI_COMPATIBLE_MODEL (auto-detected from /v1/models if not set)
  - Optionally OPENAI_COMPATIBLE_API_KEY
"""

from __future__ import annotations

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

_BASE_URL = os.environ.get("OPENAI_COMPATIBLE_URL", "http://localhost:1234")


def _server_reachable() -> bool:
    try:
        resp = httpx.get(f"{_BASE_URL}/v1/models", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


if not _server_reachable():
    pytest.skip(
        f"OpenAI-compatible server not reachable at {_BASE_URL} — skipping",
        allow_module_level=True,
    )

FAKE_AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
FAKE_AWS_SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
FAKE_AWS_REGION = "us-east-1"


# ── Auto-detect model ────────────────────────────────────────────────

_MODEL = os.environ.get("OPENAI_COMPATIBLE_MODEL", "")
if not _MODEL:
    try:
        resp = httpx.get(f"{_BASE_URL}/v1/models", timeout=5)
        models = resp.json().get("data", [])
        if models:
            _MODEL = models[0].get("id", "")
    except Exception:
        pass


# ── Registry initialization ──────────────────────────────────────────


@pytest.fixture(autouse=True)
def _init_registry():
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
                "backend": "agentic_primitives_gateway.primitives.llm.openai_compatible.OpenAICompatibleProvider",
                "config": {
                    "base_url": _BASE_URL,
                    "default_model": _MODEL,
                    "api_key": os.environ.get("OPENAI_COMPATIBLE_API_KEY", ""),
                },
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
                "backend": "agentic_primitives_gateway.primitives.browser.noop.NoopBrowserProvider",
                "config": {},
            },
        },
    )
    orig = _config_module.settings
    _config_module.settings = test_settings
    registry.initialize(test_settings)
    yield
    _config_module.settings = orig


# ── Client ────────────────────────────────────────────────────────────


@pytest.fixture
async def client():
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


# ── Completions ───────────────────────────────────────────────────────


class TestCompletions:
    @pytest.mark.asyncio
    async def test_simple_completion(self, client):
        result = await client.completions({"messages": [{"role": "user", "content": "Say hello"}]})

        assert "content" in result
        assert result["content"]  # non-empty
        assert result.get("stop_reason")

    @pytest.mark.asyncio
    async def test_completion_with_system(self, client):
        result = await client.completions(
            {
                "system": "You are a pirate. Respond in pirate speak.",
                "messages": [{"role": "user", "content": "Say hello"}],
            }
        )

        assert result["content"]

    @pytest.mark.asyncio
    async def test_completion_multi_turn(self, client):
        result = await client.completions(
            {
                "messages": [
                    {"role": "user", "content": "My name is Alice."},
                    {"role": "assistant", "content": "Nice to meet you, Alice!"},
                    {"role": "user", "content": "What is my name?"},
                ],
            }
        )

        assert "alice" in result["content"].lower()


# ── Streaming ─────────────────────────────────────────────────────────


class TestStreaming:
    @pytest.mark.asyncio
    async def test_stream_completion(self, client):
        events = []
        async for event in client.completions_stream({"messages": [{"role": "user", "content": "Count to 3"}]}):
            events.append(event)

        assert len(events) > 0
        types = {e.get("type") for e in events}
        assert "content_delta" in types
        assert "message_stop" in types

        # Reassemble text
        text = "".join(e.get("delta", "") for e in events if e.get("type") == "content_delta")
        assert text  # non-empty


# ── List models ───────────────────────────────────────────────────────


class TestListModels:
    @pytest.mark.asyncio
    async def test_list_models(self, client):
        result = await client.list_models()

        assert "models" in result
        assert len(result["models"]) >= 1
        assert result["models"][0].get("name")
