"""System tests for the Langfuse observability primitive.

Full stack: AgenticPlatformClient -> ASGI -> middleware -> route -> registry ->
LangfuseObservabilityProvider -> (mocked) langfuse.Langfuse.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import agentic_primitives_gateway.config as _config_module
from agentic_primitives_gateway.config import Settings
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway_client import AgenticPlatformClient, AgenticPlatformError

# ── Registry override ────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _init_registry():
    """Initialise registry with Langfuse observability provider."""
    test_settings = Settings(
        allow_server_credentials=True,
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
                    "public_key": "pk-test",
                    "secret_key": "sk-test",
                    "base_url": "https://langfuse.test",
                },
            },
            "gateway": {
                "backend": "agentic_primitives_gateway.primitives.gateway.noop.NoopGatewayProvider",
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


# ── Helpers ──────────────────────────────────────────────────────────


def _mock_langfuse_client() -> MagicMock:
    """Create a fully-wired mock Langfuse client.

    Sets up ``start_as_current_observation`` as a nested context manager so that
    ``with client.start_as_current_observation(...) as root:`` works, and
    ``root.start_as_current_observation(...)`` also works for child spans.
    """
    mock_client = MagicMock()

    # Root observation context manager
    mock_root = MagicMock()
    root_cm = MagicMock()
    root_cm.__enter__ = MagicMock(return_value=mock_root)
    root_cm.__exit__ = MagicMock(return_value=False)
    mock_client.start_as_current_observation.return_value = root_cm

    # Child observation context manager (for spans)
    mock_child = MagicMock()
    child_cm = MagicMock()
    child_cm.__enter__ = MagicMock(return_value=mock_child)
    child_cm.__exit__ = MagicMock(return_value=False)
    mock_root.start_as_current_observation.return_value = child_cm

    # API sub-objects
    mock_client.api = MagicMock()
    mock_client.api.trace = MagicMock()
    mock_client.api.score = MagicMock()
    mock_client.api.sessions = MagicMock()

    return mock_client


# ── Trace ingestion ──────────────────────────────────────────────────


class TestIngestTrace:
    async def test_ingest_trace(self, client: AgenticPlatformClient) -> None:
        mock_lf = _mock_langfuse_client()
        with patch(
            "agentic_primitives_gateway.primitives.observability.langfuse.Langfuse",
            return_value=mock_lf,
        ):
            result = await client.ingest_trace({"name": "test-trace", "trace_id": "t1"})

        assert result["status"] == "accepted"
        mock_lf.flush.assert_called_once()

    async def test_ingest_trace_with_spans(self, client: AgenticPlatformClient) -> None:
        mock_lf = _mock_langfuse_client()
        with patch(
            "agentic_primitives_gateway.primitives.observability.langfuse.Langfuse",
            return_value=mock_lf,
        ):
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
        mock_lf.flush.assert_called_once()


class TestIngestLog:
    async def test_ingest_log(self, client: AgenticPlatformClient) -> None:
        mock_lf = _mock_langfuse_client()
        with patch(
            "agentic_primitives_gateway.primitives.observability.langfuse.Langfuse",
            return_value=mock_lf,
        ):
            result = await client.ingest_log({"level": "error", "message": "something failed"})

        assert result["status"] == "accepted"
        mock_lf.flush.assert_called_once()


# ── Trace query / retrieval ──────────────────────────────────────────


class TestQueryTraces:
    async def test_query_traces(self, client: AgenticPlatformClient) -> None:
        mock_lf = _mock_langfuse_client()
        mock_trace = MagicMock()
        mock_trace.id = "t-abc"
        mock_trace.name = "my-trace"
        mock_trace.user_id = None
        mock_trace.session_id = None
        mock_trace.input = None
        mock_trace.output = None
        mock_trace.tags = []
        mock_trace.metadata = {}
        mock_trace.latency = 1.5
        mock_trace.total_cost = None
        mock_trace.observations = None
        mock_lf.api.trace.list.return_value = MagicMock(data=[mock_trace])

        with patch(
            "agentic_primitives_gateway.primitives.observability.langfuse.Langfuse",
            return_value=mock_lf,
        ):
            result = await client.query_traces()

        assert "traces" in result
        assert len(result["traces"]) == 1
        assert result["traces"][0]["trace_id"] == "t-abc"


class TestGetTrace:
    async def test_get_trace(self, client: AgenticPlatformClient) -> None:
        mock_lf = _mock_langfuse_client()
        mock_trace = MagicMock()
        mock_trace.id = "t-abc"
        mock_trace.name = "my-trace"
        mock_trace.user_id = None
        mock_trace.session_id = None
        mock_trace.input = None
        mock_trace.output = None
        mock_trace.tags = []
        mock_trace.metadata = {}
        mock_trace.latency = 2.0
        mock_trace.total_cost = None
        mock_trace.observations = None
        mock_lf.api.trace.get.return_value = mock_trace

        with patch(
            "agentic_primitives_gateway.primitives.observability.langfuse.Langfuse",
            return_value=mock_lf,
        ):
            result = await client.get_trace("t-abc")

        assert result["trace_id"] == "t-abc"

    async def test_get_trace_not_found(self, client: AgenticPlatformClient) -> None:
        mock_lf = _mock_langfuse_client()
        mock_lf.api.trace.get.side_effect = Exception("Not found")

        with (
            patch(
                "agentic_primitives_gateway.primitives.observability.langfuse.Langfuse",
                return_value=mock_lf,
            ),
            pytest.raises(AgenticPlatformError) as exc_info,
        ):
            await client.get_trace("t-missing")
        assert exc_info.value.status_code == 404


# ── Generation logging ───────────────────────────────────────────────


class TestLogGeneration:
    async def test_log_generation(self, client: AgenticPlatformClient) -> None:
        mock_lf = _mock_langfuse_client()
        mock_gen = MagicMock()
        mock_gen.id = "gen-1"
        mock_lf.generation.return_value = mock_gen

        with patch(
            "agentic_primitives_gateway.primitives.observability.langfuse.Langfuse",
            return_value=mock_lf,
        ):
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


# ── Flush ────────────────────────────────────────────────────────────


class TestFlush:
    async def test_flush(self, client: AgenticPlatformClient) -> None:
        mock_lf = _mock_langfuse_client()

        with patch(
            "agentic_primitives_gateway.primitives.observability.langfuse.Langfuse",
            return_value=mock_lf,
        ):
            result = await client.flush_observability()

        assert result["status"] == "accepted"
        mock_lf.flush.assert_called_once()


# ── Trace updates & scoring ──────────────────────────────────────────


class TestUpdateTrace:
    async def test_update_trace(self, client: AgenticPlatformClient) -> None:
        mock_lf = _mock_langfuse_client()

        with patch(
            "agentic_primitives_gateway.primitives.observability.langfuse.Langfuse",
            return_value=mock_lf,
        ):
            result = await client.update_trace("t1", {"name": "updated"})

        assert result["trace_id"] == "t1"
        assert result["status"] == "updated"
        mock_lf.trace.assert_called_once()
        mock_lf.flush.assert_called_once()


class TestScoreTrace:
    async def test_score_trace(self, client: AgenticPlatformClient) -> None:
        mock_lf = _mock_langfuse_client()
        mock_score = MagicMock()
        mock_score.id = "score-1"
        mock_lf.score.return_value = mock_score

        with patch(
            "agentic_primitives_gateway.primitives.observability.langfuse.Langfuse",
            return_value=mock_lf,
        ):
            result = await client.score_trace("t1", {"name": "accuracy", "value": 0.9})

        assert result["trace_id"] == "t1"
        assert result["name"] == "accuracy"
        assert result["value"] == 0.9


class TestListScores:
    async def test_list_scores(self, client: AgenticPlatformClient) -> None:
        mock_lf = _mock_langfuse_client()
        mock_score_obj = MagicMock()
        mock_score_obj.id = "score-1"
        mock_score_obj.trace_id = "t1"
        mock_score_obj.name = "accuracy"
        mock_score_obj.value = 0.95
        mock_score_obj.comment = None
        mock_score_obj.data_type = None
        mock_lf.api.score.list.return_value = MagicMock(data=[mock_score_obj])

        with patch(
            "agentic_primitives_gateway.primitives.observability.langfuse.Langfuse",
            return_value=mock_lf,
        ):
            result = await client.list_scores("t1")

        assert "scores" in result
        assert len(result["scores"]) == 1
        assert result["scores"][0]["name"] == "accuracy"


# ── Session management ───────────────────────────────────────────────


class TestListSessions:
    async def test_list_sessions(self, client: AgenticPlatformClient) -> None:
        mock_lf = _mock_langfuse_client()
        mock_session = MagicMock()
        mock_session.id = "sess-1"
        mock_session.user_id = "u1"
        mock_session.trace_count = 3
        mock_session.created_at = "2025-01-01T00:00:00Z"
        mock_session.metadata = {}
        mock_lf.api.sessions.list.return_value = MagicMock(data=[mock_session])

        with patch(
            "agentic_primitives_gateway.primitives.observability.langfuse.Langfuse",
            return_value=mock_lf,
        ):
            result = await client.list_observability_sessions()

        assert "sessions" in result
        assert len(result["sessions"]) == 1
        assert result["sessions"][0]["session_id"] == "sess-1"


class TestGetSession:
    async def test_get_session(self, client: AgenticPlatformClient) -> None:
        mock_lf = _mock_langfuse_client()
        mock_session = MagicMock()
        mock_session.id = "sess-1"
        mock_session.user_id = "u1"
        mock_session.trace_count = 5
        mock_session.created_at = "2025-01-01T00:00:00Z"
        mock_session.metadata = {"env": "dev"}
        mock_lf.api.sessions.get.return_value = mock_session

        with patch(
            "agentic_primitives_gateway.primitives.observability.langfuse.Langfuse",
            return_value=mock_lf,
        ):
            result = await client.get_observability_session("sess-1")

        assert result["session_id"] == "sess-1"
