"""Tests for the LLMGateway (Strands-compatible model adapter)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from agentic_primitives_gateway_client.models.llmgateway.strands import (
    LLMGateway,
    _to_gateway_messages,
    _to_gateway_tool_choice,
)

# ── Message translation ──────────────────────────────────────────────


class TestToGatewayMessages:
    def test_simple_text_messages(self):
        messages = [
            {"role": "user", "content": [{"text": "Hello"}]},
            {"role": "assistant", "content": [{"text": "Hi there"}]},
        ]
        result = _to_gateway_messages(messages)
        assert result == [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]

    def test_tool_use_message(self):
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"text": "Let me search."},
                    {
                        "toolUse": {
                            "toolUseId": "t1",
                            "name": "remember",
                            "input": {"key": "foo"},
                        }
                    },
                ],
            }
        ]
        result = _to_gateway_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "Let me search."
        assert result[0]["tool_calls"] == [{"id": "t1", "name": "remember", "input": {"key": "foo"}}]

    def test_tool_result_message(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "t1",
                            "content": [{"text": "result data"}],
                            "status": "success",
                        }
                    }
                ],
            }
        ]
        result = _to_gateway_messages(messages)
        assert len(result) == 1
        assert result[0] == {"tool_results": [{"tool_use_id": "t1", "content": "result data"}]}

    def test_tool_result_with_json_content(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "t1",
                            "content": [{"json": {"key": "value"}}],
                            "status": "success",
                        }
                    }
                ],
            }
        ]
        result = _to_gateway_messages(messages)
        assert json.loads(result[0]["tool_results"][0]["content"]) == {"key": "value"}


class TestToGatewayToolChoice:
    def test_auto(self):
        assert _to_gateway_tool_choice({"auto": {}}) == "auto"

    def test_any(self):
        assert _to_gateway_tool_choice({"any": {}}) == "any"

    def test_specific_tool(self):
        assert _to_gateway_tool_choice({"tool": {"name": "recall"}}) == "recall"

    def test_unknown_defaults_auto(self):
        assert _to_gateway_tool_choice({}) == "auto"


# ── LLMGateway config ──────────────────────────────────────────────


def _make_model(events: list[dict] | None = None, **kwargs):
    """Create an LLMGateway with _sync_stream patched to feed events."""
    mock_client = MagicMock()
    mock_client._client.base_url = "http://test:8000"
    mock_client._client.timeout = 30.0
    mock_client._headers = {}

    model = LLMGateway(client=mock_client, **kwargs)

    if events is not None:

        def fake_sync_stream(request, callback):
            for e in events:
                callback(e)
            callback(None)

        model._sync_stream = fake_sync_stream

    return model, mock_client


class TestLLMGatewayConfig:
    def test_default_config_empty(self):
        model, _ = _make_model()
        assert model.get_config() == {}

    def test_config_with_model(self):
        model, _ = _make_model(model="claude-4")
        assert model.get_config()["model"] == "claude-4"

    def test_update_config(self):
        model, _ = _make_model()
        model.update_config(model="claude-4", max_tokens=1024)
        cfg = model.get_config()
        assert cfg["model"] == "claude-4"
        assert cfg["max_tokens"] == 1024


# ── LLMGateway.stream() ────────────────────────────────────────────


class TestLLMGatewayStream:
    @pytest.mark.asyncio
    async def test_text_stream(self):
        events = [
            {"type": "content_delta", "delta": "Hello"},
            {"type": "content_delta", "delta": " world"},
            {"type": "message_stop", "stop_reason": "end_turn"},
        ]
        model, _ = _make_model(events)

        collected = []
        async for event in model.stream([{"role": "user", "content": [{"text": "Hi"}]}]):
            collected.append(event)

        # messageStart, contentBlockStart, 2x contentBlockDelta, contentBlockStop, messageStop
        assert collected[0] == {"messageStart": {"role": "assistant"}}
        assert "contentBlockStart" in collected[1]
        assert collected[2]["contentBlockDelta"]["delta"]["text"] == "Hello"
        assert collected[3]["contentBlockDelta"]["delta"]["text"] == " world"
        assert "contentBlockStop" in collected[4]
        assert collected[5]["messageStop"]["stopReason"] == "end_turn"

    @pytest.mark.asyncio
    async def test_tool_use_stream(self):
        events = [
            {"type": "tool_use_start", "id": "t1", "name": "remember"},
            {"type": "tool_use_complete", "id": "t1", "name": "remember", "input": {"key": "x"}},
            {"type": "message_stop", "stop_reason": "tool_use"},
        ]
        model, _ = _make_model(events)

        collected = []
        async for event in model.stream(
            [{"role": "user", "content": [{"text": "remember x"}]}],
            tool_specs=[{"name": "remember", "description": "Store", "inputSchema": {"json": {}}}],
        ):
            collected.append(event)

        assert collected[0] == {"messageStart": {"role": "assistant"}}
        start = collected[1]["contentBlockStart"]["start"]
        assert start["toolUse"]["name"] == "remember"
        assert start["toolUse"]["toolUseId"] == "t1"
        assert "contentBlockDelta" in collected[2]
        assert "contentBlockStop" in collected[3]
        assert collected[4]["messageStop"]["stopReason"] == "tool_use"

    @pytest.mark.asyncio
    async def test_mixed_text_and_tool(self):
        events = [
            {"type": "content_delta", "delta": "I'll help."},
            {"type": "tool_use_start", "id": "t1", "name": "recall"},
            {"type": "tool_use_complete", "id": "t1", "name": "recall", "input": {"query": "q"}},
            {"type": "message_stop", "stop_reason": "tool_use"},
        ]
        model, _ = _make_model(events)

        collected = []
        async for event in model.stream(
            [{"role": "user", "content": [{"text": "recall q"}]}],
            tool_specs=[{"name": "recall", "description": "Search", "inputSchema": {"json": {}}}],
        ):
            collected.append(event)

        types = [next(iter(e.keys())) for e in collected]
        assert types == [
            "messageStart",
            "contentBlockStart",  # text block
            "contentBlockDelta",  # "I'll help."
            "contentBlockStop",  # close text
            "contentBlockStart",  # tool block
            "contentBlockDelta",  # tool input
            "contentBlockStop",  # close tool
            "messageStop",
        ]

    @pytest.mark.asyncio
    async def test_metadata_event(self):
        events = [
            {"type": "content_delta", "delta": "Hi"},
            {"type": "message_stop", "stop_reason": "end_turn"},
            {"type": "metadata", "usage": {"input_tokens": 10, "output_tokens": 5}},
        ]
        model, _ = _make_model(events)

        collected = []
        async for event in model.stream([{"role": "user", "content": [{"text": "Hi"}]}]):
            collected.append(event)

        metadata = [e for e in collected if "metadata" in e]
        assert len(metadata) == 1
        assert metadata[0]["metadata"]["usage"]["inputTokens"] == 10
        assert metadata[0]["metadata"]["usage"]["outputTokens"] == 5

    @pytest.mark.asyncio
    async def test_tool_use_delta_event(self):
        """tool_use_delta events are forwarded as contentBlockDelta."""
        events = [
            {"type": "tool_use_start", "id": "t1", "name": "recall"},
            {"type": "tool_use_delta", "id": "t1", "delta": '{"query":'},
            {"type": "tool_use_delta", "id": "t1", "delta": '"test"}'},
            {"type": "tool_use_complete", "id": "t1", "name": "recall", "input": {"query": "test"}},
            {"type": "message_stop", "stop_reason": "tool_use"},
        ]
        model, _ = _make_model(events)

        collected = []
        async for event in model.stream(
            [{"role": "user", "content": [{"text": "recall"}]}],
            tool_specs=[{"name": "recall", "description": "Search", "inputSchema": {"json": {}}}],
        ):
            collected.append(event)

        deltas = [e for e in collected if "contentBlockDelta" in e]
        # tool_use_delta x2 + tool_use_complete delta = 3 deltas
        assert len(deltas) == 3
        assert deltas[0]["contentBlockDelta"]["delta"]["toolUse"]["input"] == '{"query":'
        assert deltas[1]["contentBlockDelta"]["delta"]["toolUse"]["input"] == '"test"}'

    @pytest.mark.asyncio
    async def test_tool_use_start_with_inline_input(self):
        """tool_use_start with input emits start + delta in one go."""
        events = [
            {"type": "tool_use_start", "id": "t1", "name": "recall", "input": {"q": "x"}},
            {"type": "message_stop", "stop_reason": "tool_use"},
        ]
        model, _ = _make_model(events)

        collected = []
        async for event in model.stream(
            [{"role": "user", "content": [{"text": "recall"}]}],
            tool_specs=[{"name": "recall", "description": "Search", "inputSchema": {"json": {}}}],
        ):
            collected.append(event)

        # messageStart, contentBlockStart, contentBlockDelta (inline input), messageStop
        starts = [e for e in collected if "contentBlockStart" in e]
        assert len(starts) == 1
        assert starts[0]["contentBlockStart"]["start"]["toolUse"]["name"] == "recall"
        deltas = [e for e in collected if "contentBlockDelta" in e]
        assert len(deltas) == 1
        assert json.loads(deltas[0]["contentBlockDelta"]["delta"]["toolUse"]["input"]) == {"q": "x"}

    @pytest.mark.asyncio
    async def test_build_request_with_tools_and_tool_choice(self):
        """_build_request includes tools and tool_choice."""
        captured = {}

        def capturing_sync_stream(request, callback):
            captured.update(request)
            callback(None)

        model, _ = _make_model()
        model._sync_stream = capturing_sync_stream

        async for _ in model.stream(
            [{"role": "user", "content": [{"text": "Hi"}]}],
            tool_specs=[{"name": "recall", "description": "Search", "inputSchema": {"json": {"type": "object"}}}],
            tool_choice={"auto": {}},
        ):
            pass

        assert len(captured["tools"]) == 1
        assert captured["tools"][0]["name"] == "recall"
        assert captured["tools"][0]["input_schema"] == {"type": "object"}
        assert captured["tool_choice"] == "auto"

    @pytest.mark.asyncio
    async def test_system_prompt_passed_in_request(self):
        """System prompt is included in the gateway request."""
        captured = {}

        def capturing_sync_stream(request, callback):
            captured.update(request)
            callback(None)

        model, _ = _make_model()
        model._sync_stream = capturing_sync_stream

        async for _ in model.stream(
            [{"role": "user", "content": [{"text": "Hi"}]}],
            system_prompt="Be helpful.",
        ):
            pass

        assert captured["system"] == "Be helpful."

    @pytest.mark.asyncio
    async def test_system_prompt_content_takes_precedence(self):
        captured = {}

        def capturing_sync_stream(request, callback):
            captured.update(request)
            callback(None)

        model, _ = _make_model()
        model._sync_stream = capturing_sync_stream

        async for _ in model.stream(
            [{"role": "user", "content": [{"text": "Hi"}]}],
            system_prompt="fallback",
            system_prompt_content=[{"text": "preferred prompt"}],
        ):
            pass

        assert captured["system"] == "preferred prompt"

    @pytest.mark.asyncio
    async def test_config_model_included_in_request(self):
        captured = {}

        def capturing_sync_stream(request, callback):
            captured.update(request)
            callback(None)

        model, _ = _make_model(model="claude-4", temperature=0.5)
        model._sync_stream = capturing_sync_stream

        async for _ in model.stream([{"role": "user", "content": [{"text": "Hi"}]}]):
            pass

        assert captured["model"] == "claude-4"
        assert captured["temperature"] == 0.5


# ── get_model() on client ────────────────────────────────────────────


class TestGetModel:
    def test_get_model_returns_llm_gateway(self):
        from agentic_primitives_gateway_client.client import AgenticPlatformClient

        client = AgenticPlatformClient("http://localhost:9999")
        model = client.get_model(format="strands")
        assert isinstance(model, LLMGateway)

    def test_get_model_passes_config(self):
        from agentic_primitives_gateway_client.client import AgenticPlatformClient

        client = AgenticPlatformClient("http://localhost:9999")
        model = client.get_model(format="strands", model="claude-4", max_tokens=512)
        cfg = model.get_config()
        assert cfg["model"] == "claude-4"
        assert cfg["max_tokens"] == 512

    def test_get_model_unknown_format_raises(self):
        from agentic_primitives_gateway_client.client import AgenticPlatformClient

        client = AgenticPlatformClient("http://localhost:9999")
        with pytest.raises(ValueError, match="Unknown model format"):
            client.get_model(format="openai")
