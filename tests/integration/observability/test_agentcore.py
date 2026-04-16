"""Integration tests for the AgentCore observability primitive.

Full stack with real AWS calls:
AgenticPlatformClient → ASGI → middleware → route → registry →
AgentCoreObservabilityProvider → real OTel tracer + X-Ray client.

Focused on "fire and verify acceptance" — trace ingestion is async and
subject to X-Ray eventual consistency, so we verify 202 acceptance
rather than immediate read-after-write.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from agentic_primitives_gateway_client import AgenticPlatformClient

pytestmark = pytest.mark.integration


# ── Trace ingestion ──────────────────────────────────────────────────


class TestIngestTrace:
    async def test_ingest_trace(self, client: AgenticPlatformClient) -> None:
        result = await client.ingest_trace({"name": "integ-trace", "trace_id": f"t-{uuid4().hex[:8]}"})

        assert result["status"] == "accepted"

    async def test_ingest_trace_with_spans(self, client: AgenticPlatformClient) -> None:
        result = await client.ingest_trace(
            {
                "name": "integ-agent",
                "trace_id": f"t-{uuid4().hex[:8]}",
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
        """Query recent traces.

        May return empty results in a fresh account — we just verify the
        API call succeeds and returns the expected shape.
        """
        result = await client.query_traces()

        assert "traces" in result
        assert isinstance(result["traces"], list)
