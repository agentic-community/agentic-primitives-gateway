"""Tests for the LLM streaming endpoint."""

from __future__ import annotations

import json
from unittest.mock import patch

from fastapi.testclient import TestClient


def _collect_sse_events(response) -> list[dict]:
    events = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


class TestCompletionStreamEndpoint:
    def test_stream_returns_sse(self, client: TestClient):
        """Streaming endpoint returns text/event-stream content type."""

        async def mock_stream(model_request):
            yield {"type": "content_delta", "delta": "Hello"}
            yield {"type": "message_stop", "stop_reason": "end_turn", "model": "test"}

        with patch("agentic_primitives_gateway.routes.llm.registry") as mock_registry:
            mock_registry.llm.route_request_stream = mock_stream
            resp = client.post(
                "/api/v1/llm/completions/stream",
                json={"model": "test", "messages": [{"role": "user", "content": "Hi"}]},
            )

        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        events = _collect_sse_events(resp)
        assert len(events) == 2
        assert events[0] == {"type": "content_delta", "delta": "Hello"}
        assert events[1]["type"] == "message_stop"

    def test_stream_tool_use_events(self, client: TestClient):
        """Streaming endpoint forwards tool_use events."""

        async def mock_stream(model_request):
            yield {"type": "tool_use_start", "id": "t1", "name": "remember"}
            yield {"type": "tool_use_complete", "id": "t1", "name": "remember", "input": {"key": "x"}}
            yield {"type": "message_stop", "stop_reason": "tool_use", "model": "test"}

        with patch("agentic_primitives_gateway.routes.llm.registry") as mock_registry:
            mock_registry.llm.route_request_stream = mock_stream
            resp = client.post(
                "/api/v1/llm/completions/stream",
                json={"model": "test", "messages": [{"role": "user", "content": "Hi"}]},
            )

        events = _collect_sse_events(resp)
        assert events[0]["type"] == "tool_use_start"
        assert events[0]["name"] == "remember"
        assert events[-1]["type"] == "message_stop"

    def test_stream_empty_response(self, client: TestClient):
        """Streaming endpoint handles empty stream gracefully."""

        async def mock_stream(model_request):
            return
            yield

        with patch("agentic_primitives_gateway.routes.llm.registry") as mock_registry:
            mock_registry.llm.route_request_stream = mock_stream
            resp = client.post(
                "/api/v1/llm/completions/stream",
                json={"model": "test", "messages": [{"role": "user", "content": "Hi"}]},
            )

        assert resp.status_code == 200
        events = _collect_sse_events(resp)
        assert events == []

    def test_stream_without_model_field(self, client: TestClient):
        """Request without model field doesn't 422 (model defaults to empty string)."""

        async def mock_stream(model_request):
            yield {"type": "content_delta", "delta": "ok"}
            yield {"type": "message_stop", "stop_reason": "end_turn"}

        with patch("agentic_primitives_gateway.routes.llm.registry") as mock_registry:
            mock_registry.llm.route_request_stream = mock_stream
            resp = client.post(
                "/api/v1/llm/completions/stream",
                json={"messages": [{"role": "user", "content": "Hi"}]},
            )

        assert resp.status_code == 200
        events = _collect_sse_events(resp)
        assert len(events) == 2

    def test_stream_passes_request_fields(self, client: TestClient):
        """Request fields (model, system, tools, temperature) are passed to the provider."""
        captured = {}

        async def mock_stream(model_request):
            captured.update(model_request)
            yield {"type": "message_stop", "stop_reason": "end_turn"}

        with patch("agentic_primitives_gateway.routes.llm.registry") as mock_registry:
            mock_registry.llm.route_request_stream = mock_stream
            resp = client.post(
                "/api/v1/llm/completions/stream",
                json={
                    "model": "claude-4",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "system": "Be helpful.",
                    "temperature": 0.5,
                    "max_tokens": 100,
                    "tools": [{"name": "recall", "description": "Search"}],
                    "tool_choice": "auto",
                },
            )

        assert resp.status_code == 200
        assert captured["model"] == "claude-4"
        assert captured["system"] == "Be helpful."
        assert captured["temperature"] == 0.5
        assert captured["max_tokens"] == 100
        assert captured["tools"] == [{"name": "recall", "description": "Search"}]
        assert captured["tool_choice"] == "auto"

    def test_stream_metadata_event(self, client: TestClient):
        """Metadata events are forwarded in the SSE stream."""

        async def mock_stream(model_request):
            yield {"type": "content_delta", "delta": "Hi"}
            yield {"type": "message_stop", "stop_reason": "end_turn"}
            yield {"type": "metadata", "usage": {"input_tokens": 10, "output_tokens": 5}}

        with patch("agentic_primitives_gateway.routes.llm.registry") as mock_registry:
            mock_registry.llm.route_request_stream = mock_stream
            resp = client.post(
                "/api/v1/llm/completions/stream",
                json={"model": "test", "messages": [{"role": "user", "content": "Hi"}]},
            )

        events = _collect_sse_events(resp)
        metadata = [e for e in events if e.get("type") == "metadata"]
        assert len(metadata) == 1
        assert metadata[0]["usage"]["input_tokens"] == 10
