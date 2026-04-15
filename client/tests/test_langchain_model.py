"""Tests for the LLMGateway LangChain model adapter."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agentic_primitives_gateway_client.models.llmgateway.langchain import (
    LLMGateway,
    _event_to_chunk,
    _parse_response,
    _to_gateway_messages,
)

# ── Message translation ──────────────────────────────────────────────


class TestToGatewayMessages:
    def test_simple_messages(self):
        messages = [HumanMessage(content="Hi"), AIMessage(content="Hello")]
        result, sys = _to_gateway_messages(messages)
        assert sys is None
        assert result == [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ]

    def test_system_message_extracted(self):
        messages = [SystemMessage(content="Be helpful"), HumanMessage(content="Hi")]
        result, sys = _to_gateway_messages(messages)
        assert sys == "Be helpful"
        assert len(result) == 1
        assert result[0] == {"role": "user", "content": "Hi"}

    def test_ai_message_with_tool_calls(self):
        messages = [
            AIMessage(
                content="Let me check.",
                tool_calls=[{"id": "t1", "name": "recall", "args": {"q": "x"}}],
            )
        ]
        result, _ = _to_gateway_messages(messages)
        assert result[0]["tool_calls"] == [{"id": "t1", "name": "recall", "input": {"q": "x"}}]

    def test_tool_message(self):
        messages = [ToolMessage(content="found it", tool_call_id="t1")]
        result, _ = _to_gateway_messages(messages)
        assert result[0] == {"tool_results": [{"tool_use_id": "t1", "content": "found it"}]}


# ── Response parsing ─────────────────────────────────────────────────


class TestParseResponse:
    def test_text_response(self):
        result = _parse_response(
            {
                "content": "Hello!",
                "stop_reason": "end_turn",
                "model": "test",
                "usage": {"input_tokens": 5, "output_tokens": 3},
            }
        )
        assert len(result.generations) == 1
        assert result.generations[0].message.content == "Hello!"
        assert result.llm_output["model"] == "test"

    def test_tool_call_response(self):
        result = _parse_response(
            {
                "content": "",
                "tool_calls": [{"id": "t1", "name": "recall", "input": {"q": "test"}}],
                "stop_reason": "tool_use",
            }
        )
        msg = result.generations[0].message
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0]["name"] == "recall"
        assert msg.tool_calls[0]["args"] == {"q": "test"}


# ── Event to chunk ───────────────────────────────────────────────────


class TestEventToChunk:
    def test_content_delta(self):
        chunk = _event_to_chunk({"type": "content_delta", "delta": "Hello"})
        assert chunk is not None
        assert chunk.message.content == "Hello"

    def test_tool_use_start(self):
        chunk = _event_to_chunk({"type": "tool_use_start", "id": "t1", "name": "recall"})
        assert chunk is not None
        assert chunk.message.tool_call_chunks[0]["name"] == "recall"
        assert chunk.message.tool_call_chunks[0]["id"] == "t1"

    def test_tool_use_complete(self):
        chunk = _event_to_chunk({"type": "tool_use_complete", "id": "t1", "name": "recall", "input": {"q": "test"}})
        assert chunk is not None
        assert json.loads(chunk.message.tool_call_chunks[0]["args"]) == {"q": "test"}

    def test_message_stop(self):
        chunk = _event_to_chunk({"type": "message_stop", "stop_reason": "end_turn"})
        assert chunk is not None
        assert chunk.generation_info["stop_reason"] == "end_turn"

    def test_tool_use_delta(self):
        chunk = _event_to_chunk({"type": "tool_use_delta", "delta": '{"query":'})
        assert chunk is not None
        assert chunk.message.tool_call_chunks[0]["args"] == '{"query":'

    def test_unknown_event_returns_none(self):
        assert _event_to_chunk({"type": "unknown"}) is None
        assert _event_to_chunk({"type": "metadata", "usage": {}}) is None


# ── _langchain_tool_to_gateway ───────────────────────────────────────


class TestLangchainToolToGateway:
    def test_openai_style_tool(self):
        from agentic_primitives_gateway_client.models.llmgateway.langchain import _langchain_tool_to_gateway

        tool = {
            "type": "function",
            "function": {
                "name": "recall",
                "description": "Search memory",
                "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
            },
        }
        result = _langchain_tool_to_gateway(tool)
        assert result["name"] == "recall"
        assert result["description"] == "Search memory"
        assert result["input_schema"]["type"] == "object"

    def test_flat_tool(self):
        from agentic_primitives_gateway_client.models.llmgateway.langchain import _langchain_tool_to_gateway

        tool = {"name": "recall", "description": "Search", "input_schema": {"type": "object"}}
        result = _langchain_tool_to_gateway(tool)
        assert result["name"] == "recall"
        assert result["input_schema"] == {"type": "object"}


# ── LLMGateway integration ──────────────────────────────────────────


class _MockClient:
    """Mock client for testing LLMGateway."""

    def __init__(self, response: dict | None = None, events: list[dict] | None = None):
        self._response = response or {}
        self._events = events or []
        self.last_request: dict = {}

    async def completions(self, request):
        self.last_request = request
        return self._response

    async def completions_stream(self, request):
        self.last_request = request
        for e in self._events:
            yield e


class TestLLMGateway:
    def test_generate(self):
        mock = _MockClient(
            response={
                "content": "Hello!",
                "model": "test",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 5, "output_tokens": 3},
            }
        )
        model = LLMGateway(client=mock, model_name="test-model")
        result = model.invoke([HumanMessage(content="Hi")])

        assert isinstance(result, AIMessage)
        assert result.content == "Hello!"
        assert mock.last_request["model"] == "test-model"
        assert mock.last_request["messages"] == [{"role": "user", "content": "Hi"}]

    def test_generate_with_system_prompt(self):
        mock = _MockClient(response={"content": "Yes!", "stop_reason": "end_turn"})
        model = LLMGateway(client=mock)
        model.invoke([SystemMessage(content="Be brief"), HumanMessage(content="Hi")])

        assert mock.last_request["system"] == "Be brief"
        assert len(mock.last_request["messages"]) == 1

    def test_stream(self):
        events = [
            {"type": "content_delta", "delta": "Hello"},
            {"type": "content_delta", "delta": " world"},
            {"type": "message_stop", "stop_reason": "end_turn"},
        ]
        mock = _MockClient(events=events)
        model = LLMGateway(client=mock)

        chunks = list(model.stream([HumanMessage(content="Hi")]))
        texts = [c.content for c in chunks if c.content]
        assert texts == ["Hello", " world"]

    def test_stream_with_tool_calls(self):
        events = [
            {"type": "tool_use_start", "id": "t1", "name": "recall"},
            {"type": "tool_use_complete", "id": "t1", "name": "recall", "input": {"q": "x"}},
            {"type": "message_stop", "stop_reason": "tool_use"},
        ]
        mock = _MockClient(events=events)
        model = LLMGateway(client=mock)

        chunks = list(model.stream([HumanMessage(content="recall x")]))
        tool_chunks = [c for c in chunks if c.tool_call_chunks]
        assert len(tool_chunks) == 2
        assert tool_chunks[0].tool_call_chunks[0]["name"] == "recall"

    def test_generate_with_tool_calls_response(self):
        mock = _MockClient(
            response={
                "content": "",
                "tool_calls": [{"id": "t1", "name": "recall", "input": {"q": "test"}}],
                "stop_reason": "tool_use",
            }
        )
        model = LLMGateway(client=mock)
        result = model.invoke([HumanMessage(content="recall test")])

        assert isinstance(result, AIMessage)
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["name"] == "recall"
        assert result.tool_calls[0]["args"] == {"q": "test"}

    def test_config_passed_to_request(self):
        mock = _MockClient(response={"content": "ok", "stop_reason": "end_turn"})
        model = LLMGateway(client=mock, model_name="claude-4", temperature=0.3, max_tokens=256)
        model.invoke([HumanMessage(content="Hi")])

        assert mock.last_request["model"] == "claude-4"
        assert mock.last_request["temperature"] == 0.3
        assert mock.last_request["max_tokens"] == 256

    def test_get_model_returns_llm_gateway(self):
        from agentic_primitives_gateway_client.client import AgenticPlatformClient

        client = AgenticPlatformClient("http://localhost:9999")
        model = client.get_model(format="langchain", model="claude-4")
        assert isinstance(model, LLMGateway)
        assert model.model_name == "claude-4"

    def test_get_model_unknown_format_raises(self):
        from agentic_primitives_gateway_client.client import AgenticPlatformClient

        client = AgenticPlatformClient("http://localhost:9999")
        with pytest.raises(ValueError, match="Unknown model format"):
            client.get_model(format="openai")
