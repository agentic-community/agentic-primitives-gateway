"""Integration tests for the Langfuse observability primitive.

Full stack with real Langfuse calls:
AgenticPlatformClient → ASGI → middleware → route → registry →
LangfuseObservabilityProvider → real Langfuse API.

Requires:
  - A running Langfuse instance
  - LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY env vars
  - Optionally LANGFUSE_BASE_URL (default: http://localhost:3000)
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import httpx
import pytest

import agentic_primitives_gateway.config as _config_module
from agentic_primitives_gateway.config import Settings
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway_client import AgenticPlatformClient

pytestmark = pytest.mark.integration


# ── Skip logic ────────────────────────────────────────────────────────

if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
    pytest.skip(
        "LANGFUSE_PUBLIC_KEY not set — skipping Langfuse integration tests",
        allow_module_level=True,
    )

FAKE_AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
FAKE_AWS_SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
FAKE_AWS_REGION = "us-east-1"


# ── Registry initialization ──────────────────────────────────────────


@pytest.fixture(autouse=True)
def _init_registry():
    """Initialise registry with Langfuse observability provider (noop for everything else).

    Langfuse credentials are read from env vars and baked into the provider config
    so the provider doesn't need per-request credential headers.
    """
    public_key = os.environ["LANGFUSE_PUBLIC_KEY"]
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
    base_url = os.environ.get("LANGFUSE_BASE_URL", "http://localhost:3000")

    test_settings = Settings(
        allow_server_credentials="always",
        providers={
            "memory": {
                "backend": "agentic_primitives_gateway.primitives.memory.noop.NoopMemoryProvider",
                "config": {},
            },
            "observability": {
                "backend": (
                    "agentic_primitives_gateway.primitives.observability.langfuse.LangfuseObservabilityProvider"
                ),
                "config": {
                    "public_key": public_key,
                    "secret_key": secret_key,
                    "base_url": base_url,
                },
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
                "backend": "agentic_primitives_gateway.primitives.browser.noop.NoopBrowserProvider",
                "config": {},
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

    Langfuse doesn't need AWS credentials — they're baked into the provider
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


# ── Helpers ───────────────────────────────────────────────────────────


def _unique_trace_id() -> str:
    """Return a 32-char hex trace ID compatible with Langfuse v3."""
    return uuid4().hex


# ── Trace ingestion ──────────────────────────────────────────────────


class TestIngestTrace:
    async def test_ingest_trace(self, client: AgenticPlatformClient) -> None:
        result = await client.ingest_trace({"name": "integ-trace", "trace_id": _unique_trace_id()})

        assert result["status"] == "accepted"

    async def test_ingest_trace_with_spans(self, client: AgenticPlatformClient) -> None:
        result = await client.ingest_trace(
            {
                "name": "integ-agent",
                "trace_id": _unique_trace_id(),
                "user_id": "integ-user",
                "session_id": f"s-{uuid4().hex[:8]}",
                "input": "What is 2+2?",
                "output": "4",
                "tags": ["integration-test"],
                "metadata": {"env": "integ"},
                "spans": [
                    {
                        "name": "llm-call",
                        "model": "claude-3",
                        "input": "prompt text",
                        "output": "completion text",
                    }
                ],
            }
        )

        assert result["status"] == "accepted"


# ── Log ingestion ────────────────────────────────────────────────────


class TestIngestLog:
    async def test_ingest_log(self, client: AgenticPlatformClient) -> None:
        result = await client.ingest_log({"level": "info", "message": "integration test log entry"})

        assert result["status"] == "accepted"


# ── Flush ────────────────────────────────────────────────────────────


class TestFlush:
    async def test_flush(self, client: AgenticPlatformClient) -> None:
        result = await client.flush_observability()

        assert result["status"] == "accepted"


# ── Trace query ──────────────────────────────────────────────────────


class TestQueryTraces:
    async def test_query_traces(self, client: AgenticPlatformClient) -> None:
        """Query recent traces — verify API succeeds and returns expected shape."""
        result = await client.query_traces()

        assert "traces" in result
        assert isinstance(result["traces"], list)


# ── Get trace ────────────────────────────────────────────────────────


class TestGetTrace:
    async def test_ingest_then_get_trace(self, client: AgenticPlatformClient) -> None:
        """Ingest a trace, flush, then retrieve it by ID.

        Langfuse has eventual consistency — we retry a few times with a short
        delay to allow propagation.
        """
        trace_id = _unique_trace_id()

        await client.ingest_trace({"name": "integ-get-trace", "trace_id": trace_id})
        await client.flush_observability()

        # Retry with small delays for eventual consistency
        trace = None
        for _ in range(5):
            await asyncio.sleep(1)
            try:
                trace = await client.get_trace(trace_id)
                break
            except Exception:
                continue

        assert trace is not None, f"Could not retrieve trace {trace_id} after retries"
        assert trace["trace_id"] == trace_id


# ── Generation logging ───────────────────────────────────────────────


class TestLogGeneration:
    async def test_log_generation(self, client: AgenticPlatformClient) -> None:
        trace_id = _unique_trace_id()

        # Ensure the trace exists first
        await client.ingest_trace({"name": "integ-gen-trace", "trace_id": trace_id})
        await client.flush_observability()

        result = await client.log_generation(
            trace_id,
            {
                "name": "claude-gen",
                "model": "claude-3",
                "input": "What is 2+2?",
                "output": "4",
            },
        )

        assert result["trace_id"] == trace_id
        assert result["name"] == "claude-gen"
        assert result["model"] == "claude-3"


# ── Update trace ─────────────────────────────────────────────────────


class TestUpdateTrace:
    async def test_update_trace(self, client: AgenticPlatformClient) -> None:
        trace_id = _unique_trace_id()

        await client.ingest_trace({"name": "integ-update-trace", "trace_id": trace_id})
        await client.flush_observability()

        result = await client.update_trace(trace_id, {"name": "updated-trace", "tags": ["updated"]})

        assert result["trace_id"] == trace_id
        assert result["status"] == "updated"


# ── Score trace ──────────────────────────────────────────────────────


class TestScoreTrace:
    async def test_score_trace(self, client: AgenticPlatformClient) -> None:
        trace_id = _unique_trace_id()

        await client.ingest_trace({"name": "integ-score-trace", "trace_id": trace_id})
        await client.flush_observability()

        result = await client.score_trace(trace_id, {"name": "accuracy", "value": 0.95})

        assert result["trace_id"] == trace_id
        assert result["name"] == "accuracy"
        assert result["value"] == 0.95

    async def test_list_scores(self, client: AgenticPlatformClient) -> None:
        trace_id = _unique_trace_id()

        await client.ingest_trace({"name": "integ-list-scores", "trace_id": trace_id})
        await client.flush_observability()

        await client.score_trace(trace_id, {"name": "relevance", "value": 0.8})
        await client.flush_observability()

        # Small delay for eventual consistency
        await asyncio.sleep(1)

        result = await client.list_scores(trace_id)

        assert "scores" in result
        assert isinstance(result["scores"], list)
