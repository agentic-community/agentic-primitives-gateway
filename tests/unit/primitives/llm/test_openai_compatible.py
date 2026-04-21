"""Tests for the OpenAI-compatible LLM provider."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agentic_primitives_gateway.primitives.llm.openai_compatible import (
    OpenAICompatibleProvider,
    _from_openai_response,
    _to_openai_messages,
    _to_openai_tool_choice,
    _to_openai_tools,
)

_PATCH_PREFIX = "agentic_primitives_gateway.primitives.llm.openai_compatible"


# ── Message translation ──────────────────────────────────────────────


class TestToOpenAIMessages:
    def test_simple_messages(self):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        result = _to_openai_messages(messages)
        assert result == messages

    def test_system_message(self):
        messages = [{"role": "system", "content": "Be helpful"}]
        result = _to_openai_messages(messages)
        assert result == [{"role": "system", "content": "Be helpful"}]

    def test_tool_results_batch(self):
        messages = [{"tool_results": [{"tool_use_id": "t1", "content": "result"}]}]
        result = _to_openai_messages(messages)
        assert result == [{"role": "tool", "tool_call_id": "t1", "content": "result"}]

    def test_tool_result_single(self):
        messages = [{"tool_result": {"tool_use_id": "t1", "content": "result"}}]
        result = _to_openai_messages(messages)
        assert result == [{"role": "tool", "tool_call_id": "t1", "content": "result"}]

    def test_assistant_with_tool_calls(self):
        messages = [
            {
                "role": "assistant",
                "content": "Let me check.",
                "tool_calls": [{"id": "t1", "name": "recall", "input": {"q": "x"}}],
            }
        ]
        result = _to_openai_messages(messages)
        assert result[0]["role"] == "assistant"
        assert result[0]["tool_calls"][0]["function"]["name"] == "recall"
        assert json.loads(result[0]["tool_calls"][0]["function"]["arguments"]) == {"q": "x"}


class TestToOpenAITools:
    def test_gateway_format(self):
        tools = [{"name": "recall", "description": "Search", "input_schema": {"type": "object"}}]
        result = _to_openai_tools(tools)
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "recall"

    def test_already_openai_format(self):
        tools = [{"type": "function", "function": {"name": "recall"}}]
        assert _to_openai_tools(tools) == tools


class TestToOpenAIToolChoice:
    def test_auto(self):
        assert _to_openai_tool_choice("auto") == "auto"

    def test_any_maps_to_required(self):
        assert _to_openai_tool_choice("any") == "required"

    def test_specific_tool(self):
        result = _to_openai_tool_choice("recall")
        assert result == {"type": "function", "function": {"name": "recall"}}


# ── Response parsing ─────────────────────────────────────────────────


class TestFromOpenAIResponse:
    def test_text_response(self):
        data = {
            "model": "qwen3",
            "choices": [{"message": {"content": "Hello!", "role": "assistant"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        result = _from_openai_response(data)
        assert result["content"] == "Hello!"
        assert result["model"] == "qwen3"
        assert result["usage"]["input_tokens"] == 10

    def test_tool_call_response(self):
        data = {
            "model": "qwen3",
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {"id": "t1", "type": "function", "function": {"name": "recall", "arguments": '{"q":"x"}'}}
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {},
        }
        result = _from_openai_response(data)
        assert result["stop_reason"] == "tool_use"
        assert result["tool_calls"][0]["name"] == "recall"
        assert result["tool_calls"][0]["input"] == {"q": "x"}

    def test_empty_choices(self):
        result = _from_openai_response({"model": "test", "choices": []})
        assert result["content"] == ""


# ── Provider ─────────────────────────────────────────────────────────


class TestOpenAICompatibleProvider:
    def test_init_defaults(self):
        provider = OpenAICompatibleProvider()
        assert provider._base_url == "https://api.openai.com"

    def test_init_custom(self):
        provider = OpenAICompatibleProvider(base_url="http://gpu-box:1234", default_model="llama3")
        assert provider._base_url == "http://gpu-box:1234"
        assert provider._default_model == "llama3"

    @pytest.mark.asyncio
    async def test_route_request(self):
        provider = OpenAICompatibleProvider(default_model="qwen3")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "model": "qwen3",
            "choices": [{"message": {"content": "Hello!", "role": "assistant"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

        with patch(f"{_PATCH_PREFIX}.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_httpx.Client.return_value = mock_client

            result = await provider.route_request({"messages": [{"role": "user", "content": "Hi"}]})

        assert result["content"] == "Hello!"
        call_args = mock_client.post.call_args
        assert "/v1/chat/completions" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_route_request_empty_model_uses_default(self):
        provider = OpenAICompatibleProvider(default_model="qwen3")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "model": "qwen3",
            "choices": [{"message": {"content": "Hi"}, "finish_reason": "stop"}],
            "usage": {},
        }

        with patch(f"{_PATCH_PREFIX}.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_httpx.Client.return_value = mock_client

            await provider.route_request({"model": "", "messages": [{"role": "user", "content": "Hi"}]})

        request_body = mock_client.post.call_args[1]["json"]
        assert request_body.get("model") == "qwen3"

    @pytest.mark.asyncio
    async def test_route_request_with_system_prompt(self):
        provider = OpenAICompatibleProvider()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "model": "test",
            "choices": [{"message": {"content": "Hi"}, "finish_reason": "stop"}],
            "usage": {},
        }

        with patch(f"{_PATCH_PREFIX}.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_httpx.Client.return_value = mock_client

            await provider.route_request({"system": "Be helpful.", "messages": [{"role": "user", "content": "Hi"}]})

        request_body = mock_client.post.call_args[1]["json"]
        assert request_body["messages"][0] == {"role": "system", "content": "Be helpful."}

    @pytest.mark.asyncio
    async def test_list_models(self):
        provider = OpenAICompatibleProvider()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"id": "qwen3-4b", "object": "model"},
                {"id": "llama3-8b", "object": "model"},
            ]
        }

        with patch(f"{_PATCH_PREFIX}.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_httpx.Client.return_value = mock_client

            models = await provider.list_models()

        assert len(models) == 2
        assert models[0]["name"] == "qwen3-4b"
        assert models[0]["provider"] == "openai_compatible"


class TestStreaming:
    """Cover the SSE streaming path — content deltas, tool-call deltas,
    finish reasons, usage metadata."""

    @pytest.mark.asyncio
    async def test_stream_content_deltas(self):
        provider = OpenAICompatibleProvider(default_model="qwen3")

        sse_lines = [
            'data: {"choices":[{"delta":{"content":"Hel"},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"content":"lo"},"finish_reason":"stop"}],"model":"qwen3"}',
            "data: [DONE]",
        ]

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_lines = MagicMock(return_value=iter(sse_lines))

        with patch(f"{_PATCH_PREFIX}.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)

            stream_ctx = MagicMock()
            stream_ctx.__enter__ = MagicMock(return_value=mock_resp)
            stream_ctx.__exit__ = MagicMock(return_value=False)
            mock_client.stream.return_value = stream_ctx

            mock_httpx.Client.return_value = mock_client

            chunks = []
            async for chunk in provider.route_request_stream({"messages": [{"role": "user", "content": "Hi"}]}):
                chunks.append(chunk)

        types = [c["type"] for c in chunks]
        assert "content_delta" in types
        assert "message_stop" in types

    @pytest.mark.asyncio
    async def test_stream_tool_calls(self):
        provider = OpenAICompatibleProvider()

        sse_lines = [
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"t1","function":{"name":"recall"}}]},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"q\\":\\"x\\"}"}}]},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}],"model":"qwen3"}',
            "data: [DONE]",
        ]

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_lines = MagicMock(return_value=iter(sse_lines))

        with patch(f"{_PATCH_PREFIX}.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)

            stream_ctx = MagicMock()
            stream_ctx.__enter__ = MagicMock(return_value=mock_resp)
            stream_ctx.__exit__ = MagicMock(return_value=False)
            mock_client.stream.return_value = stream_ctx

            mock_httpx.Client.return_value = mock_client

            chunks = []
            async for chunk in provider.route_request_stream({"messages": [{"role": "user", "content": "Hi"}]}):
                chunks.append(chunk)

        types = [c["type"] for c in chunks]
        assert "tool_use_start" in types
        assert "tool_use_delta" in types
        assert "tool_use_complete" in types
        # tool_calls finish reason maps to tool_use
        stops = [c for c in chunks if c["type"] == "message_stop"]
        assert stops and stops[0]["stop_reason"] == "tool_use"

    @pytest.mark.asyncio
    async def test_stream_ignores_malformed_json(self):
        provider = OpenAICompatibleProvider()
        sse_lines = [
            "data: not-json-at-all",
            'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}',
            "data: [DONE]",
        ]

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_lines = MagicMock(return_value=iter(sse_lines))

        with patch(f"{_PATCH_PREFIX}.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)

            stream_ctx = MagicMock()
            stream_ctx.__enter__ = MagicMock(return_value=mock_resp)
            stream_ctx.__exit__ = MagicMock(return_value=False)
            mock_client.stream.return_value = stream_ctx

            mock_httpx.Client.return_value = mock_client

            chunks = []
            async for chunk in provider.route_request_stream({"messages": [{"role": "user", "content": "Hi"}]}):
                chunks.append(chunk)

        # Still get a content_delta for the valid line + a stop
        assert any(c["type"] == "content_delta" for c in chunks)
        assert any(c["type"] == "message_stop" for c in chunks)

    @pytest.mark.asyncio
    async def test_stream_emits_usage_metadata(self):
        provider = OpenAICompatibleProvider()
        sse_lines = [
            'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":1}}',
            "data: [DONE]",
        ]
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_lines = MagicMock(return_value=iter(sse_lines))

        with patch(f"{_PATCH_PREFIX}.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            stream_ctx = MagicMock()
            stream_ctx.__enter__ = MagicMock(return_value=mock_resp)
            stream_ctx.__exit__ = MagicMock(return_value=False)
            mock_client.stream.return_value = stream_ctx
            mock_httpx.Client.return_value = mock_client

            chunks = [c async for c in provider.route_request_stream({"messages": [{"role": "user", "content": "h"}]})]

        metadata = [c for c in chunks if c["type"] == "metadata"]
        assert metadata and metadata[0]["usage"]["input_tokens"] == 3


class TestHealthcheck:
    @pytest.mark.asyncio
    async def test_healthcheck_returns_true_when_models_ok(self):
        provider = OpenAICompatibleProvider()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch(f"{_PATCH_PREFIX}.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp
            mock_httpx.Client.return_value = mock_client

            assert await provider.healthcheck() is True

    @pytest.mark.asyncio
    async def test_healthcheck_returns_false_on_exception(self):
        provider = OpenAICompatibleProvider()
        with patch(f"{_PATCH_PREFIX}.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.side_effect = RuntimeError("down")
            mock_httpx.Client.return_value = mock_client

            assert await provider.healthcheck() is False


class TestEdgeCases:
    def test_tool_choice_dict_passthrough(self):
        choice = {"type": "function", "function": {"name": "specific"}}
        assert _to_openai_tool_choice(choice) == choice

    def test_tool_choice_named_tool_string(self):
        assert _to_openai_tool_choice("my_tool") == {"type": "function", "function": {"name": "my_tool"}}

    def test_from_openai_response_malformed_tool_args(self):
        """When a tool-call's arguments aren't valid JSON, they surface as
        ``{"raw": <string>}`` instead of raising."""
        data = {
            "model": "m",
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [{"id": "t1", "function": {"name": "f", "arguments": "not-json"}}],
                    }
                }
            ],
        }
        result = _from_openai_response(data)
        assert result["tool_calls"][0]["input"] == {"raw": "not-json"}

    def test_init_with_api_key_adds_auth_header(self):
        provider = OpenAICompatibleProvider(api_key="sk-test")
        headers = provider._headers()
        assert headers["Authorization"] == "Bearer sk-test"
