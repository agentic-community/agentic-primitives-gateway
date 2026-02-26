"""System tests for the AgentCore observability primitive.

Full stack: AgenticPlatformClient → ASGI → middleware → route → registry →
AgentCoreObservabilityProvider → (mocked) OTel tracer + X-Ray client.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway_client import AgenticPlatformClient, AgenticPlatformError

# ── Helpers ───────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _setup_obs_tracer():
    """Make the mock tracer's context manager work properly.

    The tracer was set to a MagicMock during registry init. We need
    ``start_as_current_span`` to return a usable context manager.
    """
    provider = registry.get_primitive("observability").get()
    tracer = provider._provider._tracer

    mock_span = MagicMock()
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=mock_span)
    cm.__exit__ = MagicMock(return_value=False)
    tracer.start_as_current_span.return_value = cm


@pytest.fixture(autouse=True)
def _mock_otel_context():
    """Patch OTel context/baggage modules used by ``ingest_trace``."""
    with (
        patch("opentelemetry.context.get_current", return_value=MagicMock()),
        patch("opentelemetry.baggage.set_baggage", return_value=MagicMock()),
        patch("opentelemetry.context.attach"),
        patch("opentelemetry.context.detach"),
    ):
        yield


# ── Trace ingestion ──────────────────────────────────────────────────


class TestIngestTrace:
    async def test_ingest_trace(self, client: AgenticPlatformClient) -> None:
        result = await client.ingest_trace({"name": "test-trace", "trace_id": "t1"})

        assert result["status"] == "accepted"

    async def test_ingest_trace_with_spans(self, client: AgenticPlatformClient) -> None:
        result = await client.ingest_trace(
            {
                "name": "my-agent",
                "trace_id": "t2",
                "user_id": "u1",
                "session_id": "s1",
                "input": "hello",
                "output": "world",
                "tags": ["test"],
                "metadata": {"env": "dev"},
                "spans": [
                    {
                        "name": "llm-call",
                        "model": "claude-3",
                        "input": "prompt",
                        "output": "completion",
                    }
                ],
            }
        )

        assert result["status"] == "accepted"


class TestIngestLog:
    async def test_ingest_log(self, client: AgenticPlatformClient) -> None:
        result = await client.ingest_log({"level": "error", "message": "something failed"})

        assert result["status"] == "accepted"


# ── Trace query / retrieval ───────────────────────────────────────────


class TestQueryTraces:
    async def test_query_traces(self, client: AgenticPlatformClient, mock_xray_client: MagicMock) -> None:
        mock_xray_client.get_trace_summaries.return_value = {
            "TraceSummaries": [
                {
                    "Id": "1-abc-def",
                    "EntryPoint": {"Name": "my-trace"},
                    "Duration": 1.5,
                    "ResponseTime": 0.3,
                    "HasFault": False,
                    "HasError": False,
                },
            ],
        }

        result = await client.query_traces()

        assert "traces" in result
        assert len(result["traces"]) == 1
        assert result["traces"][0]["trace_id"] == "1-abc-def"


class TestGetTrace:
    async def test_get_trace(self, client: AgenticPlatformClient, mock_xray_client: MagicMock) -> None:
        mock_xray_client.batch_get_traces.return_value = {
            "Traces": [
                {
                    "Id": "1-abc-def",
                    "Duration": 2.0,
                    "Segments": [
                        {"Document": {"name": "root-span"}},
                    ],
                },
            ],
        }

        result = await client.get_trace("1-abc-def")

        assert result["trace_id"] == "1-abc-def"

    async def test_get_trace_not_found(self, client: AgenticPlatformClient, mock_xray_client: MagicMock) -> None:
        mock_xray_client.batch_get_traces.return_value = {"Traces": []}

        with pytest.raises(AgenticPlatformError) as exc_info:
            await client.get_trace("1-missing")
        assert exc_info.value.status_code == 404


# ── Generation logging ────────────────────────────────────────────────


class TestLogGeneration:
    async def test_log_generation(self, client: AgenticPlatformClient) -> None:
        result = await client.log_generation(
            "t1",
            {
                "name": "claude-gen",
                "model": "claude-3",
                "input": "What is 2+2?",
                "output": "4",
            },
        )

        assert result["trace_id"] == "t1"
        assert result["name"] == "claude-gen"
        assert result["model"] == "claude-3"


# ── Flush ─────────────────────────────────────────────────────────────


class TestFlush:
    async def test_flush(self, client: AgenticPlatformClient) -> None:
        provider = registry.get_primitive("observability").get()
        provider._provider._tracer_provider = MagicMock()

        result = await client.flush_observability()

        assert result["status"] == "accepted"


# ── Unsupported operations ────────────────────────────────────────────


class TestUnsupported:
    async def test_update_trace(self, client: AgenticPlatformClient) -> None:
        with pytest.raises(AgenticPlatformError) as exc_info:
            await client.update_trace("t1", {"name": "updated"})
        assert exc_info.value.status_code == 501

    async def test_score_trace(self, client: AgenticPlatformClient) -> None:
        with pytest.raises(AgenticPlatformError) as exc_info:
            await client.score_trace("t1", {"name": "accuracy", "value": 0.9})
        assert exc_info.value.status_code == 501
