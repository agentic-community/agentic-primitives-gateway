from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentic_primitives_gateway.primitives.observability.agentcore import (
    AgentCoreObservabilityProvider,
)


def _create_provider(**kwargs):
    """Create a provider instance with init side effects mocked out."""
    with (
        patch.object(AgentCoreObservabilityProvider, "_ensure_log_group"),
        patch.object(AgentCoreObservabilityProvider, "_setup_tracer", return_value=MagicMock()),
    ):
        return AgentCoreObservabilityProvider(
            region=kwargs.get("region", "us-east-1"),
            service_name=kwargs.get("service_name", "test-svc"),
            agent_id=kwargs.get("agent_id", "test-agent"),
        )


class TestAgentCoreObservabilityProvider:
    """Tests for the AgentCore observability provider (ADOT/X-Ray)."""

    @pytest.mark.asyncio
    async def test_ingest_trace_creates_spans(self):
        provider = _create_provider()
        mock_root_span = MagicMock()
        provider._tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_root_span)
        provider._tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

        mock_root_span.__enter__ = MagicMock(return_value=mock_root_span)
        mock_root_span.__exit__ = MagicMock(return_value=False)

        trace_data = {
            "name": "test-trace",
            "trace_id": "t-1",
            "user_id": "u-1",
            "session_id": "s-1",
            "input": "hello",
            "output": "world",
            "metadata": {"model": "claude"},
            "tags": ["test"],
            "spans": [
                {"name": "child-span", "input": "in", "output": "out", "model": "gpt-4", "metadata": {"k": "v"}},
            ],
        }

        with (
            patch("opentelemetry.context.get_current", return_value=MagicMock()),
            patch("opentelemetry.baggage.set_baggage", return_value=MagicMock()),
            patch("opentelemetry.context.attach"),
            patch("opentelemetry.context.detach"),
        ):
            await provider.ingest_trace(trace_data)

    @pytest.mark.asyncio
    async def test_ingest_trace_without_session_id(self):
        provider = _create_provider()
        mock_span = MagicMock()
        provider._tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
        provider._tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

        trace_data = {"name": "simple", "trace_id": "t-2"}

        with (
            patch("opentelemetry.context.get_current", return_value=MagicMock()),
            patch("opentelemetry.context.attach"),
            patch("opentelemetry.context.detach"),
        ):
            await provider.ingest_trace(trace_data)

    @pytest.mark.asyncio
    async def test_ingest_log(self):
        provider = _create_provider()
        mock_span = MagicMock()
        provider._tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
        provider._tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

        log_entry = {
            "level": "error",
            "message": "Something broke",
            "metadata": {"component": "auth"},
        }

        await provider.ingest_log(log_entry)
        mock_span.set_attribute.assert_any_call("log.level", "error")
        mock_span.set_attribute.assert_any_call("log.message", "Something broke")
        mock_span.add_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_query_traces(self):
        provider = _create_provider()
        mock_session = MagicMock()
        mock_session.region_name = "us-east-1"
        mock_xray = MagicMock()
        mock_xray.get_trace_summaries.return_value = {
            "TraceSummaries": [
                {
                    "Id": "1-abc",
                    "EntryPoint": {"Name": "my-service"},
                    "Duration": 1.5,
                    "ResponseTime": 0.8,
                    "HasFault": False,
                    "HasError": False,
                },
            ]
        }
        mock_session.client.return_value = mock_xray

        with patch(
            "agentic_primitives_gateway.primitives.observability.agentcore.get_boto3_session", return_value=mock_session
        ):
            result = await provider.query_traces({"limit": 10})

        assert len(result) == 1
        assert result[0]["trace_id"] == "1-abc"
        assert result[0]["name"] == "my-service"

    @pytest.mark.asyncio
    async def test_query_traces_xray_error_returns_empty(self):
        provider = _create_provider()
        mock_session = MagicMock()
        mock_session.region_name = "us-east-1"
        mock_xray = MagicMock()
        mock_xray.get_trace_summaries.side_effect = Exception("X-Ray unavailable")
        mock_session.client.return_value = mock_xray

        with patch(
            "agentic_primitives_gateway.primitives.observability.agentcore.get_boto3_session", return_value=mock_session
        ):
            result = await provider.query_traces()

        assert result == []

    @pytest.mark.asyncio
    async def test_healthcheck(self):
        provider = _create_provider()
        assert await provider.healthcheck() is True

    @pytest.mark.asyncio
    async def test_healthcheck_no_tracer(self):
        provider = _create_provider()
        provider._tracer = None
        assert await provider.healthcheck() is False


class TestAgentCoreObsInit:
    """Test initialization branches."""

    def test_log_group_already_exists(self):
        class ResourceAlreadyExistsException(Exception):
            pass

        mock_logs = MagicMock()
        mock_logs.create_log_group.side_effect = ResourceAlreadyExistsException("exists")

        with (
            patch("agentic_primitives_gateway.primitives.observability.agentcore.boto3") as mock_boto3,
            patch.object(AgentCoreObservabilityProvider, "_setup_tracer", return_value=MagicMock()),
        ):
            mock_boto3.client.return_value = mock_logs
            prov = AgentCoreObservabilityProvider(region="us-east-1")
            assert prov._tracer is not None

    def test_log_group_other_error(self):
        mock_logs = MagicMock()
        mock_logs.create_log_group.side_effect = RuntimeError("permission denied")

        with (
            patch("agentic_primitives_gateway.primitives.observability.agentcore.boto3") as mock_boto3,
            patch.object(AgentCoreObservabilityProvider, "_setup_tracer", return_value=MagicMock()),
        ):
            mock_boto3.client.return_value = mock_logs
            # Should not raise — just logs a warning
            prov = AgentCoreObservabilityProvider(region="us-east-1")
            assert prov._tracer is not None
