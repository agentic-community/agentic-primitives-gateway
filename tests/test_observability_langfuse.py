from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentic_primitives_gateway.primitives.observability.langfuse import (
    LangfuseObservabilityProvider,
    _trace_to_dict,
)


class TestLangfuseObservabilityProvider:
    """Tests for the Langfuse observability provider."""

    @pytest.fixture
    def provider(self):
        with patch.dict("os.environ", {}, clear=False):
            return LangfuseObservabilityProvider(
                public_key="pk-test",
                secret_key="sk-test",
                base_url="https://langfuse.example.com",
            )

    @patch("agentic_primitives_gateway.primitives.observability.langfuse.get_service_credentials_or_defaults")
    @patch("agentic_primitives_gateway.primitives.observability.langfuse.Langfuse")
    @pytest.mark.asyncio
    async def test_ingest_trace(self, mock_langfuse_cls, mock_get_creds, provider):
        mock_get_creds.return_value = {
            "public_key": "pk",
            "secret_key": "sk",
            "base_url": "https://test.com",
        }
        mock_client = MagicMock()
        mock_langfuse_cls.return_value = mock_client

        # Set up the context manager chain
        mock_root = MagicMock()
        mock_client.start_as_current_observation.return_value.__enter__ = MagicMock(return_value=mock_root)
        mock_client.start_as_current_observation.return_value.__exit__ = MagicMock(return_value=False)

        mock_child = MagicMock()
        mock_root.start_as_current_observation.return_value.__enter__ = MagicMock(return_value=mock_child)
        mock_root.start_as_current_observation.return_value.__exit__ = MagicMock(return_value=False)

        trace = {
            "name": "test-trace",
            "user_id": "user-1",
            "session_id": "sess-1",
            "metadata": {"model": "claude"},
            "tags": ["test"],
            "input": "hello",
            "output": "world",
            "spans": [
                {"name": "span-1", "input": "in", "output": "out", "metadata": {}, "level": "INFO", "model": "gpt-4"},
            ],
        }

        await provider.ingest_trace(trace)
        mock_client.flush.assert_called_once()

    @patch("agentic_primitives_gateway.primitives.observability.langfuse.get_service_credentials_or_defaults")
    @patch("agentic_primitives_gateway.primitives.observability.langfuse.Langfuse")
    @pytest.mark.asyncio
    async def test_ingest_log(self, mock_langfuse_cls, mock_get_creds, provider):
        mock_get_creds.return_value = {
            "public_key": "pk",
            "secret_key": "sk",
            "base_url": "https://test.com",
        }
        mock_client = MagicMock()
        mock_langfuse_cls.return_value = mock_client

        mock_root = MagicMock()
        mock_client.start_as_current_observation.return_value.__enter__ = MagicMock(return_value=mock_root)
        mock_client.start_as_current_observation.return_value.__exit__ = MagicMock(return_value=False)

        log_entry = {
            "level": "error",
            "message": "Something failed",
            "metadata": {"component": "auth"},
        }

        await provider.ingest_log(log_entry)
        mock_client.flush.assert_called_once()

    @patch("agentic_primitives_gateway.primitives.observability.langfuse.get_service_credentials_or_defaults")
    @patch("agentic_primitives_gateway.primitives.observability.langfuse.Langfuse")
    @pytest.mark.asyncio
    async def test_query_traces_by_trace_id(self, mock_langfuse_cls, mock_get_creds, provider):
        mock_get_creds.return_value = {"public_key": "pk", "secret_key": "sk", "base_url": None}
        mock_client = MagicMock()
        mock_langfuse_cls.return_value = mock_client

        mock_trace = MagicMock()
        mock_trace.id = "trace-123"
        mock_trace.name = "test"
        mock_trace.user_id = None
        mock_trace.session_id = None
        mock_trace.input = None
        mock_trace.output = None
        mock_trace.tags = []
        mock_trace.metadata = {}
        mock_trace.latency = 1.5
        mock_trace.total_cost = 0.01
        mock_trace.observations = None

        mock_client.api.trace.get.return_value = mock_trace

        result = await provider.query_traces({"trace_id": "trace-123"})
        assert len(result) == 1
        assert result[0]["trace_id"] == "trace-123"

    @patch("agentic_primitives_gateway.primitives.observability.langfuse.get_service_credentials_or_defaults")
    @patch("agentic_primitives_gateway.primitives.observability.langfuse.Langfuse")
    @pytest.mark.asyncio
    async def test_query_traces_by_filters(self, mock_langfuse_cls, mock_get_creds, provider):
        mock_get_creds.return_value = {"public_key": "pk", "secret_key": "sk", "base_url": None}
        mock_client = MagicMock()
        mock_langfuse_cls.return_value = mock_client

        mock_trace = MagicMock()
        mock_trace.id = "trace-abc"
        mock_trace.name = "my-trace"
        mock_trace.user_id = "user-1"
        mock_trace.session_id = "sess-1"
        mock_trace.input = None
        mock_trace.output = None
        mock_trace.tags = ["tag1"]
        mock_trace.metadata = {}
        mock_trace.latency = None
        mock_trace.total_cost = None
        mock_trace.observations = None

        mock_client.api.trace.list.return_value = MagicMock(data=[mock_trace])

        result = await provider.query_traces(
            {
                "name": "my-trace",
                "user_id": "user-1",
                "session_id": "sess-1",
                "tags": ["tag1"],
                "limit": 50,
            }
        )
        assert len(result) == 1
        mock_client.api.trace.list.assert_called_once_with(
            name="my-trace",
            user_id="user-1",
            session_id="sess-1",
            tags=["tag1"],
            limit=50,
        )

    @patch("agentic_primitives_gateway.primitives.observability.langfuse.get_service_credentials_or_defaults")
    @patch("agentic_primitives_gateway.primitives.observability.langfuse.Langfuse")
    @pytest.mark.asyncio
    async def test_query_traces_trace_id_not_found(self, mock_langfuse_cls, mock_get_creds, provider):
        mock_get_creds.return_value = {"public_key": "pk", "secret_key": "sk", "base_url": None}
        mock_client = MagicMock()
        mock_langfuse_cls.return_value = mock_client
        mock_client.api.trace.get.side_effect = Exception("not found")

        result = await provider.query_traces({"trace_id": "missing"})
        assert result == []

    @patch("agentic_primitives_gateway.primitives.observability.langfuse.get_service_credentials_or_defaults")
    @patch("agentic_primitives_gateway.primitives.observability.langfuse.Langfuse")
    @pytest.mark.asyncio
    async def test_healthcheck_success(self, mock_langfuse_cls, mock_get_creds, provider):
        mock_get_creds.return_value = {"public_key": "pk", "secret_key": "sk", "base_url": None}
        mock_client = MagicMock()
        mock_langfuse_cls.return_value = mock_client
        mock_client.auth_check.return_value = True

        assert await provider.healthcheck() is True

    @patch("agentic_primitives_gateway.primitives.observability.langfuse.get_service_credentials_or_defaults")
    @patch("agentic_primitives_gateway.primitives.observability.langfuse.Langfuse")
    @pytest.mark.asyncio
    async def test_healthcheck_failure(self, mock_langfuse_cls, mock_get_creds, provider):
        mock_get_creds.return_value = {"public_key": "pk", "secret_key": "sk", "base_url": None}
        mock_client = MagicMock()
        mock_langfuse_cls.return_value = mock_client
        mock_client.auth_check.side_effect = Exception("connection failed")

        assert await provider.healthcheck() is False

    def test_init_reads_env_vars(self):
        with patch.dict(
            "os.environ",
            {"LANGFUSE_PUBLIC_KEY": "env-pk", "LANGFUSE_SECRET_KEY": "env-sk", "LANGFUSE_BASE_URL": "https://env.com"},
        ):
            provider = LangfuseObservabilityProvider()
        assert provider._default_public_key == "env-pk"
        assert provider._default_secret_key == "env-sk"
        assert provider._default_base_url == "https://env.com"


class TestTraceToDict:
    """Tests for the _trace_to_dict helper."""

    def test_basic_conversion(self):
        trace = MagicMock()
        trace.id = "t-1"
        trace.name = "trace"
        trace.user_id = "u-1"
        trace.session_id = "s-1"
        trace.input = "in"
        trace.output = "out"
        trace.tags = ["tag"]
        trace.metadata = {"k": "v"}
        trace.latency = 1.0
        trace.total_cost = 0.5
        trace.observations = None

        result = _trace_to_dict(trace)
        assert result["trace_id"] == "t-1"
        assert result["name"] == "trace"
        assert result["tags"] == ["tag"]
        assert "spans" not in result

    def test_with_observation_objects(self):
        trace = MagicMock()
        trace.id = "t-2"
        trace.name = "trace"
        trace.user_id = None
        trace.session_id = None
        trace.input = None
        trace.output = None
        trace.tags = []
        trace.metadata = {}
        trace.latency = None
        trace.total_cost = None

        obs = MagicMock()
        obs.name = "span-1"
        obs.input = "in"
        obs.output = "out"
        obs.metadata = {}
        obs.level = "INFO"
        obs.model = "gpt-4"
        trace.observations = [obs]

        result = _trace_to_dict(trace)
        assert "spans" in result
        assert len(result["spans"]) == 1
        assert result["spans"][0]["name"] == "span-1"
        assert result["spans"][0]["model"] == "gpt-4"

    def test_with_string_observations_no_spans(self):
        """When observations are string IDs, spans should not be included."""
        trace = MagicMock()
        trace.id = "t-3"
        trace.name = "trace"
        trace.user_id = None
        trace.session_id = None
        trace.input = None
        trace.output = None
        trace.tags = []
        trace.metadata = {}
        trace.latency = None
        trace.total_cost = None
        trace.observations = ["obs-id-1", "obs-id-2"]

        result = _trace_to_dict(trace)
        assert "spans" not in result
