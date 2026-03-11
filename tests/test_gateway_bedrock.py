from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agentic_primitives_gateway.primitives.gateway.bedrock import (
    BedrockConverseProvider,
    _from_bedrock_response,
    _to_bedrock_messages,
    _to_bedrock_tool_choice,
    _to_bedrock_tools,
)

_PATCH_PREFIX = "agentic_primitives_gateway.primitives.gateway.bedrock"


# ── _to_bedrock_messages ─────────────────────────────────────────────


class TestToBedrockMessages:
    """Tests for _to_bedrock_messages conversion."""

    def test_simple_user_text_message(self):
        model_request = {
            "messages": [{"role": "user", "content": "Hello"}],
        }
        system_prompts, messages = _to_bedrock_messages(model_request)

        assert system_prompts == []
        assert messages == [{"role": "user", "content": [{"text": "Hello"}]}]

    def test_simple_assistant_text_message(self):
        model_request = {
            "messages": [{"role": "assistant", "content": "Hi there"}],
        }
        system_prompts, messages = _to_bedrock_messages(model_request)

        assert system_prompts == []
        assert messages == [{"role": "assistant", "content": [{"text": "Hi there"}]}]

    def test_multiple_user_assistant_messages(self):
        model_request = {
            "messages": [
                {"role": "user", "content": "What is 2+2?"},
                {"role": "assistant", "content": "4"},
                {"role": "user", "content": "Thanks!"},
            ],
        }
        system_prompts, messages = _to_bedrock_messages(model_request)

        assert system_prompts == []
        assert len(messages) == 3
        assert messages[0] == {"role": "user", "content": [{"text": "What is 2+2?"}]}
        assert messages[1] == {"role": "assistant", "content": [{"text": "4"}]}
        assert messages[2] == {"role": "user", "content": [{"text": "Thanks!"}]}

    def test_system_message_extracted_to_system_prompts(self):
        model_request = {
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello"},
            ],
        }
        system_prompts, messages = _to_bedrock_messages(model_request)

        assert system_prompts == [{"text": "You are a helpful assistant."}]
        assert len(messages) == 1
        assert messages[0] == {"role": "user", "content": [{"text": "Hello"}]}

    def test_system_field_in_model_request(self):
        model_request = {
            "system": "You are a coding assistant.",
            "messages": [{"role": "user", "content": "Write code"}],
        }
        system_prompts, messages = _to_bedrock_messages(model_request)

        assert system_prompts == [{"text": "You are a coding assistant."}]
        assert len(messages) == 1

    def test_system_field_plus_system_message_combined(self):
        model_request = {
            "system": "System from field.",
            "messages": [
                {"role": "system", "content": "System from message."},
                {"role": "user", "content": "Hello"},
            ],
        }
        system_prompts, messages = _to_bedrock_messages(model_request)

        assert len(system_prompts) == 2
        assert system_prompts[0] == {"text": "System from field."}
        assert system_prompts[1] == {"text": "System from message."}
        assert len(messages) == 1

    def test_multiple_system_messages(self):
        model_request = {
            "messages": [
                {"role": "system", "content": "Rule 1"},
                {"role": "system", "content": "Rule 2"},
                {"role": "user", "content": "Hello"},
            ],
        }
        system_prompts, messages = _to_bedrock_messages(model_request)

        assert len(system_prompts) == 2
        assert system_prompts[0] == {"text": "Rule 1"}
        assert system_prompts[1] == {"text": "Rule 2"}
        assert len(messages) == 1

    def test_assistant_message_with_tool_calls(self):
        model_request = {
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "tc-1",
                            "name": "get_weather",
                            "input": {"city": "Seattle"},
                        }
                    ],
                }
            ],
        }
        system_prompts, messages = _to_bedrock_messages(model_request)

        assert system_prompts == []
        assert len(messages) == 1
        msg = messages[0]
        assert msg["role"] == "assistant"
        assert len(msg["content"]) == 1
        tool_use = msg["content"][0]["toolUse"]
        assert tool_use["toolUseId"] == "tc-1"
        assert tool_use["name"] == "get_weather"
        assert tool_use["input"] == {"city": "Seattle"}

    def test_assistant_message_with_text_and_tool_calls(self):
        model_request = {
            "messages": [
                {
                    "role": "assistant",
                    "content": "Let me check the weather.",
                    "tool_calls": [
                        {
                            "id": "tc-1",
                            "name": "get_weather",
                            "input": {"city": "NYC"},
                        }
                    ],
                }
            ],
        }
        _system_prompts, messages = _to_bedrock_messages(model_request)

        msg = messages[0]
        assert msg["role"] == "assistant"
        assert len(msg["content"]) == 2
        assert msg["content"][0] == {"text": "Let me check the weather."}
        assert msg["content"][1]["toolUse"]["name"] == "get_weather"

    def test_assistant_tool_call_with_multiple_tools(self):
        model_request = {
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "tc-1", "name": "tool_a", "input": {"x": 1}},
                        {"id": "tc-2", "name": "tool_b", "input": {"y": 2}},
                    ],
                }
            ],
        }
        _, messages = _to_bedrock_messages(model_request)

        assert len(messages[0]["content"]) == 2
        assert messages[0]["content"][0]["toolUse"]["name"] == "tool_a"
        assert messages[0]["content"][1]["toolUse"]["name"] == "tool_b"

    def test_assistant_tool_call_with_string_input_valid_json(self):
        model_request = {
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "tc-1",
                            "name": "search",
                            "input": '{"query": "hello"}',
                        }
                    ],
                }
            ],
        }
        _, messages = _to_bedrock_messages(model_request)

        tool_use = messages[0]["content"][0]["toolUse"]
        assert tool_use["input"] == {"query": "hello"}

    def test_assistant_tool_call_with_string_input_invalid_json(self):
        model_request = {
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "tc-1",
                            "name": "search",
                            "input": "not-valid-json",
                        }
                    ],
                }
            ],
        }
        _, messages = _to_bedrock_messages(model_request)

        tool_use = messages[0]["content"][0]["toolUse"]
        assert tool_use["input"] == {"raw": "not-valid-json"}

    def test_batched_tool_results(self):
        model_request = {
            "messages": [
                {
                    "tool_results": [
                        {"tool_use_id": "tc-1", "content": "Sunny, 72F"},
                        {"tool_use_id": "tc-2", "content": "Rainy, 55F"},
                    ]
                }
            ],
        }
        system_prompts, messages = _to_bedrock_messages(model_request)

        assert system_prompts == []
        assert len(messages) == 1
        msg = messages[0]
        assert msg["role"] == "user"
        assert len(msg["content"]) == 2

        tr1 = msg["content"][0]["toolResult"]
        assert tr1["toolUseId"] == "tc-1"
        assert tr1["content"] == [{"text": "Sunny, 72F"}]

        tr2 = msg["content"][1]["toolResult"]
        assert tr2["toolUseId"] == "tc-2"
        assert tr2["content"] == [{"text": "Rainy, 55F"}]

    def test_batched_tool_results_with_dict_content(self):
        model_request = {
            "messages": [
                {
                    "tool_results": [
                        {
                            "tool_use_id": "tc-1",
                            "content": {"temperature": 72, "unit": "F"},
                        }
                    ]
                }
            ],
        }
        _, messages = _to_bedrock_messages(model_request)

        tr = messages[0]["content"][0]["toolResult"]
        content_text = tr["content"][0]["text"]
        parsed = json.loads(content_text)
        assert parsed == {"temperature": 72, "unit": "F"}

    def test_single_tool_result_legacy(self):
        model_request = {
            "messages": [
                {
                    "tool_result": {
                        "tool_use_id": "tc-1",
                        "content": "Result data",
                    }
                }
            ],
        }
        system_prompts, messages = _to_bedrock_messages(model_request)

        assert system_prompts == []
        assert len(messages) == 1
        msg = messages[0]
        assert msg["role"] == "user"
        assert len(msg["content"]) == 1
        tr = msg["content"][0]["toolResult"]
        assert tr["toolUseId"] == "tc-1"
        assert tr["content"] == [{"text": "Result data"}]

    def test_single_tool_result_with_dict_content(self):
        model_request = {
            "messages": [
                {
                    "tool_result": {
                        "tool_use_id": "tc-1",
                        "content": {"key": "value"},
                    }
                }
            ],
        }
        _, messages = _to_bedrock_messages(model_request)

        tr = messages[0]["content"][0]["toolResult"]
        content_text = tr["content"][0]["text"]
        parsed = json.loads(content_text)
        assert parsed == {"key": "value"}

    def test_content_in_list_format_passed_through(self):
        existing_blocks = [{"text": "hello"}, {"image": {"source": "data"}}]
        model_request = {
            "messages": [{"role": "user", "content": existing_blocks}],
        }
        _, messages = _to_bedrock_messages(model_request)

        assert messages[0]["content"] is existing_blocks

    def test_content_non_string_non_list_converted_to_str(self):
        model_request = {
            "messages": [{"role": "user", "content": 42}],
        }
        _, messages = _to_bedrock_messages(model_request)

        assert messages[0] == {"role": "user", "content": [{"text": "42"}]}

    def test_empty_messages(self):
        model_request = {"messages": []}
        system_prompts, messages = _to_bedrock_messages(model_request)

        assert system_prompts == []
        assert messages == []

    def test_no_messages_key(self):
        model_request = {}
        system_prompts, messages = _to_bedrock_messages(model_request)

        assert system_prompts == []
        assert messages == []

    def test_default_role_is_user(self):
        model_request = {
            "messages": [{"content": "no role specified"}],
        }
        _, messages = _to_bedrock_messages(model_request)

        assert messages[0]["role"] == "user"

    def test_empty_content_defaults_to_empty_string(self):
        model_request = {
            "messages": [{"role": "user"}],
        }
        _, messages = _to_bedrock_messages(model_request)

        assert messages[0] == {"role": "user", "content": [{"text": ""}]}

    def test_empty_system_message_content(self):
        model_request = {
            "messages": [
                {"role": "system"},
                {"role": "user", "content": "hi"},
            ],
        }
        system_prompts, messages = _to_bedrock_messages(model_request)

        assert system_prompts == [{"text": ""}]
        assert len(messages) == 1

    def test_assistant_with_empty_tool_calls_treated_as_text(self):
        """When tool_calls is an empty list, it's falsy and the message is treated as regular text."""
        model_request = {
            "messages": [
                {"role": "assistant", "content": "Done", "tool_calls": []},
            ],
        }
        _, messages = _to_bedrock_messages(model_request)

        assert messages[0] == {"role": "assistant", "content": [{"text": "Done"}]}

    def test_full_conversation_with_tool_use_cycle(self):
        """End-to-end: user asks, assistant calls tool, tool result, assistant answers."""
        model_request = {
            "system": "You are helpful.",
            "messages": [
                {"role": "user", "content": "What is the weather in Seattle?"},
                {
                    "role": "assistant",
                    "content": "Let me check.",
                    "tool_calls": [
                        {"id": "tc-1", "name": "get_weather", "input": {"city": "Seattle"}},
                    ],
                },
                {
                    "tool_results": [
                        {"tool_use_id": "tc-1", "content": "Sunny, 72F"},
                    ],
                },
                {"role": "assistant", "content": "It is sunny and 72F in Seattle."},
            ],
        }
        system_prompts, messages = _to_bedrock_messages(model_request)

        assert len(system_prompts) == 1
        assert system_prompts[0] == {"text": "You are helpful."}
        assert len(messages) == 4
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"
        assert "toolUse" in messages[1]["content"][1]
        assert messages[2]["role"] == "user"
        assert "toolResult" in messages[2]["content"][0]
        assert messages[3]["role"] == "assistant"


# ── _to_bedrock_tools ────────────────────────────────────────────────


class TestToBedrockTools:
    """Tests for _to_bedrock_tools conversion."""

    def test_flat_format_with_input_schema(self):
        tools = [
            {
                "name": "get_weather",
                "description": "Get current weather",
                "input_schema": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            }
        ]
        result = _to_bedrock_tools(tools)

        assert len(result) == 1
        spec = result[0]["toolSpec"]
        assert spec["name"] == "get_weather"
        assert spec["description"] == "Get current weather"
        assert spec["inputSchema"]["json"]["type"] == "object"
        assert "city" in spec["inputSchema"]["json"]["properties"]

    def test_flat_format_with_parameters_fallback(self):
        tools = [
            {
                "name": "search",
                "description": "Search the web",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            }
        ]
        result = _to_bedrock_tools(tools)

        spec = result[0]["toolSpec"]
        assert spec["name"] == "search"
        assert spec["inputSchema"]["json"]["properties"]["query"]["type"] == "string"

    def test_input_schema_takes_precedence_over_parameters(self):
        tools = [
            {
                "name": "tool",
                "description": "desc",
                "input_schema": {"type": "object", "properties": {"a": {"type": "string"}}},
                "parameters": {"type": "object", "properties": {"b": {"type": "string"}}},
            }
        ]
        result = _to_bedrock_tools(tools)

        # input_schema should win since dict.get checks input_schema first
        assert "a" in result[0]["toolSpec"]["inputSchema"]["json"]["properties"]

    def test_already_bedrock_format_passed_through(self):
        tools = [
            {
                "toolSpec": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                        }
                    },
                }
            }
        ]
        result = _to_bedrock_tools(tools)

        assert result == tools

    def test_missing_description_defaults_to_empty_string(self):
        tools = [{"name": "tool", "input_schema": {"type": "object"}}]
        result = _to_bedrock_tools(tools)

        assert result[0]["toolSpec"]["description"] == ""

    def test_missing_schema_defaults_to_empty_dict(self):
        tools = [{"name": "tool", "description": "A tool"}]
        result = _to_bedrock_tools(tools)

        assert result[0]["toolSpec"]["inputSchema"]["json"] == {}

    def test_multiple_tools_mixed_formats(self):
        tools = [
            {"name": "flat_tool", "description": "Flat", "input_schema": {"type": "object"}},
            {"toolSpec": {"name": "bedrock_tool", "description": "Already Bedrock", "inputSchema": {"json": {}}}},
        ]
        result = _to_bedrock_tools(tools)

        assert len(result) == 2
        assert result[0]["toolSpec"]["name"] == "flat_tool"
        assert result[1]["toolSpec"]["name"] == "bedrock_tool"

    def test_empty_tools_list(self):
        result = _to_bedrock_tools([])
        assert result == []


# ── _to_bedrock_tool_choice ──────────────────────────────────────────


class TestToBedrockToolChoice:
    """Tests for _to_bedrock_tool_choice conversion."""

    def test_auto_string(self):
        result = _to_bedrock_tool_choice("auto")
        assert result == {"auto": {}}

    def test_any_string(self):
        result = _to_bedrock_tool_choice("any")
        assert result == {"any": {}}

    def test_specific_tool_name(self):
        result = _to_bedrock_tool_choice("get_weather")
        assert result == {"tool": {"name": "get_weather"}}

    def test_dict_passthrough(self):
        custom = {"tool": {"name": "my_tool"}}
        result = _to_bedrock_tool_choice(custom)
        assert result is custom

    def test_dict_auto_passthrough(self):
        custom = {"auto": {}}
        result = _to_bedrock_tool_choice(custom)
        assert result is custom

    def test_arbitrary_dict_passthrough(self):
        custom = {"custom_key": {"nested": True}}
        result = _to_bedrock_tool_choice(custom)
        assert result is custom


# ── _from_bedrock_response ───────────────────────────────────────────


class TestFromBedrockResponse:
    """Tests for _from_bedrock_response conversion."""

    def test_text_only_response(self):
        response = {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": "Hello, how can I help?"}],
                }
            },
            "stopReason": "end_turn",
            "usage": {"inputTokens": 10, "outputTokens": 20},
        }
        result = _from_bedrock_response(response, "claude-3-sonnet")

        assert result["model"] == "claude-3-sonnet"
        assert result["content"] == "Hello, how can I help?"
        assert result["stop_reason"] == "end_turn"
        assert result["usage"]["input_tokens"] == 10
        assert result["usage"]["output_tokens"] == 20
        assert "tool_calls" not in result

    def test_tool_use_response(self):
        response = {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "tc-abc",
                                "name": "get_weather",
                                "input": {"city": "Seattle"},
                            }
                        }
                    ],
                }
            },
            "stopReason": "tool_use",
            "usage": {"inputTokens": 50, "outputTokens": 30},
        }
        result = _from_bedrock_response(response, "claude-3-sonnet")

        assert result["content"] == ""
        assert result["stop_reason"] == "tool_use"
        assert len(result["tool_calls"]) == 1
        tc = result["tool_calls"][0]
        assert tc["id"] == "tc-abc"
        assert tc["name"] == "get_weather"
        assert tc["input"] == {"city": "Seattle"}

    def test_mixed_text_and_tool_use(self):
        response = {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"text": "Let me check that for you."},
                        {
                            "toolUse": {
                                "toolUseId": "tc-1",
                                "name": "search",
                                "input": {"query": "weather"},
                            }
                        },
                    ],
                }
            },
            "stopReason": "tool_use",
            "usage": {"inputTokens": 15, "outputTokens": 25},
        }
        result = _from_bedrock_response(response, "us.anthropic.claude-sonnet-4-20250514-v1:0")

        assert result["content"] == "Let me check that for you."
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["name"] == "search"

    def test_multiple_text_blocks_joined_with_newline(self):
        response = {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"text": "First paragraph."},
                        {"text": "Second paragraph."},
                    ],
                }
            },
            "stopReason": "end_turn",
            "usage": {"inputTokens": 5, "outputTokens": 10},
        }
        result = _from_bedrock_response(response, "model-x")

        assert result["content"] == "First paragraph.\nSecond paragraph."

    def test_multiple_tool_use_blocks(self):
        response = {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "tc-1",
                                "name": "tool_a",
                                "input": {"x": 1},
                            }
                        },
                        {
                            "toolUse": {
                                "toolUseId": "tc-2",
                                "name": "tool_b",
                                "input": {"y": 2},
                            }
                        },
                    ],
                }
            },
            "stopReason": "tool_use",
            "usage": {"inputTokens": 20, "outputTokens": 40},
        }
        result = _from_bedrock_response(response, "model-x")

        assert len(result["tool_calls"]) == 2
        assert result["tool_calls"][0]["id"] == "tc-1"
        assert result["tool_calls"][1]["id"] == "tc-2"

    def test_empty_content(self):
        response = {
            "output": {"message": {"role": "assistant", "content": []}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 5, "outputTokens": 0},
        }
        result = _from_bedrock_response(response, "model-x")

        assert result["content"] == ""
        assert "tool_calls" not in result

    def test_missing_content_key(self):
        response = {
            "output": {"message": {"role": "assistant"}},
            "stopReason": "end_turn",
            "usage": {},
        }
        result = _from_bedrock_response(response, "model-x")

        assert result["content"] == ""
        assert "tool_calls" not in result

    def test_missing_output_key(self):
        response = {
            "stopReason": "end_turn",
            "usage": {},
        }
        result = _from_bedrock_response(response, "model-x")

        assert result["content"] == ""
        assert "tool_calls" not in result

    def test_usage_defaults_to_zero(self):
        response = {
            "output": {"message": {"content": [{"text": "hi"}]}},
            "stopReason": "end_turn",
            "usage": {},
        }
        result = _from_bedrock_response(response, "model-x")

        assert result["usage"]["input_tokens"] == 0
        assert result["usage"]["output_tokens"] == 0

    def test_missing_usage_key(self):
        response = {
            "output": {"message": {"content": [{"text": "hi"}]}},
            "stopReason": "end_turn",
        }
        result = _from_bedrock_response(response, "model-x")

        assert result["usage"]["input_tokens"] == 0
        assert result["usage"]["output_tokens"] == 0

    def test_default_stop_reason(self):
        response = {
            "output": {"message": {"content": [{"text": "hi"}]}},
            "usage": {},
        }
        result = _from_bedrock_response(response, "model-x")

        assert result["stop_reason"] == "end_turn"

    def test_tool_use_missing_input_defaults_to_empty_dict(self):
        response = {
            "output": {
                "message": {
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "tc-1",
                                "name": "no_args_tool",
                            }
                        }
                    ],
                }
            },
            "stopReason": "tool_use",
            "usage": {"inputTokens": 10, "outputTokens": 5},
        }
        result = _from_bedrock_response(response, "model-x")

        assert result["tool_calls"][0]["input"] == {}

    def test_model_id_passed_through(self):
        response = {
            "output": {"message": {"content": []}},
            "stopReason": "end_turn",
            "usage": {},
        }
        result = _from_bedrock_response(response, "us.anthropic.claude-sonnet-4-20250514-v1:0")

        assert result["model"] == "us.anthropic.claude-sonnet-4-20250514-v1:0"


# ── BedrockConverseProvider ──────────────────────────────────────────


@patch(f"{_PATCH_PREFIX}.get_boto3_session")
class TestBedrockConverseProvider:
    """Tests for the BedrockConverseProvider class."""

    def test_init_defaults(self, mock_get_session):
        provider = BedrockConverseProvider()
        assert provider._region == "us-east-1"
        assert provider._default_model == "us.anthropic.claude-sonnet-4-20250514-v1:0"

    def test_init_custom_params(self, mock_get_session):
        provider = BedrockConverseProvider(region="eu-west-1", default_model="my-model")
        assert provider._region == "eu-west-1"
        assert provider._default_model == "my-model"

    @pytest.mark.asyncio
    async def test_route_request_simple_text(self, mock_get_session):
        mock_client = MagicMock()
        mock_client.converse.return_value = {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": "Hello!"}],
                }
            },
            "stopReason": "end_turn",
            "usage": {"inputTokens": 5, "outputTokens": 3},
        }
        mock_session = MagicMock()
        mock_session.client.return_value = mock_client
        mock_get_session.return_value = mock_session

        provider = BedrockConverseProvider()
        result = await provider.route_request({"messages": [{"role": "user", "content": "Hi"}]})

        assert result["content"] == "Hello!"
        assert result["stop_reason"] == "end_turn"
        assert result["usage"]["input_tokens"] == 5
        assert result["usage"]["output_tokens"] == 3

        mock_session.client.assert_called_once_with("bedrock-runtime")
        call_kwargs = mock_client.converse.call_args[1]
        assert call_kwargs["modelId"] == "us.anthropic.claude-sonnet-4-20250514-v1:0"
        assert len(call_kwargs["messages"]) == 1

    @pytest.mark.asyncio
    async def test_route_request_with_custom_model(self, mock_get_session):
        mock_client = MagicMock()
        mock_client.converse.return_value = {
            "output": {"message": {"content": [{"text": "ok"}]}},
            "stopReason": "end_turn",
            "usage": {},
        }
        mock_session = MagicMock()
        mock_session.client.return_value = mock_client
        mock_get_session.return_value = mock_session

        provider = BedrockConverseProvider()
        await provider.route_request({"model": "us.meta.llama3-70b", "messages": [{"role": "user", "content": "Hi"}]})

        call_kwargs = mock_client.converse.call_args[1]
        assert call_kwargs["modelId"] == "us.meta.llama3-70b"

    @pytest.mark.asyncio
    async def test_route_request_with_system_prompt(self, mock_get_session):
        mock_client = MagicMock()
        mock_client.converse.return_value = {
            "output": {"message": {"content": [{"text": "ok"}]}},
            "stopReason": "end_turn",
            "usage": {},
        }
        mock_session = MagicMock()
        mock_session.client.return_value = mock_client
        mock_get_session.return_value = mock_session

        provider = BedrockConverseProvider()
        await provider.route_request(
            {
                "system": "Be concise.",
                "messages": [{"role": "user", "content": "Hi"}],
            }
        )

        call_kwargs = mock_client.converse.call_args[1]
        assert call_kwargs["system"] == [{"text": "Be concise."}]

    @pytest.mark.asyncio
    async def test_route_request_no_system_omits_key(self, mock_get_session):
        mock_client = MagicMock()
        mock_client.converse.return_value = {
            "output": {"message": {"content": [{"text": "ok"}]}},
            "stopReason": "end_turn",
            "usage": {},
        }
        mock_session = MagicMock()
        mock_session.client.return_value = mock_client
        mock_get_session.return_value = mock_session

        provider = BedrockConverseProvider()
        await provider.route_request(
            {
                "messages": [{"role": "user", "content": "Hi"}],
            }
        )

        call_kwargs = mock_client.converse.call_args[1]
        assert "system" not in call_kwargs

    @pytest.mark.asyncio
    async def test_route_request_with_inference_config(self, mock_get_session):
        mock_client = MagicMock()
        mock_client.converse.return_value = {
            "output": {"message": {"content": [{"text": "ok"}]}},
            "stopReason": "end_turn",
            "usage": {},
        }
        mock_session = MagicMock()
        mock_session.client.return_value = mock_client
        mock_get_session.return_value = mock_session

        provider = BedrockConverseProvider()
        await provider.route_request(
            {
                "messages": [{"role": "user", "content": "Hi"}],
                "temperature": 0.7,
                "max_tokens": 1024,
            }
        )

        call_kwargs = mock_client.converse.call_args[1]
        assert call_kwargs["inferenceConfig"]["temperature"] == 0.7
        assert call_kwargs["inferenceConfig"]["maxTokens"] == 1024

    @pytest.mark.asyncio
    async def test_route_request_no_inference_config_when_absent(self, mock_get_session):
        mock_client = MagicMock()
        mock_client.converse.return_value = {
            "output": {"message": {"content": [{"text": "ok"}]}},
            "stopReason": "end_turn",
            "usage": {},
        }
        mock_session = MagicMock()
        mock_session.client.return_value = mock_client
        mock_get_session.return_value = mock_session

        provider = BedrockConverseProvider()
        await provider.route_request(
            {
                "messages": [{"role": "user", "content": "Hi"}],
            }
        )

        call_kwargs = mock_client.converse.call_args[1]
        assert "inferenceConfig" not in call_kwargs

    @pytest.mark.asyncio
    async def test_route_request_with_tools(self, mock_get_session):
        mock_client = MagicMock()
        mock_client.converse.return_value = {
            "output": {
                "message": {
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "tc-1",
                                "name": "get_weather",
                                "input": {"city": "Seattle"},
                            }
                        }
                    ],
                }
            },
            "stopReason": "tool_use",
            "usage": {"inputTokens": 50, "outputTokens": 30},
        }
        mock_session = MagicMock()
        mock_session.client.return_value = mock_client
        mock_get_session.return_value = mock_session

        provider = BedrockConverseProvider()
        result = await provider.route_request(
            {
                "messages": [{"role": "user", "content": "What is the weather?"}],
                "tools": [
                    {
                        "name": "get_weather",
                        "description": "Get weather",
                        "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
                    }
                ],
            }
        )

        assert result["stop_reason"] == "tool_use"
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["name"] == "get_weather"

        call_kwargs = mock_client.converse.call_args[1]
        assert "toolConfig" in call_kwargs
        assert len(call_kwargs["toolConfig"]["tools"]) == 1

    @pytest.mark.asyncio
    async def test_route_request_with_tool_choice(self, mock_get_session):
        mock_client = MagicMock()
        mock_client.converse.return_value = {
            "output": {"message": {"content": [{"text": "ok"}]}},
            "stopReason": "end_turn",
            "usage": {},
        }
        mock_session = MagicMock()
        mock_session.client.return_value = mock_client
        mock_get_session.return_value = mock_session

        provider = BedrockConverseProvider()
        await provider.route_request(
            {
                "messages": [{"role": "user", "content": "Hi"}],
                "tools": [{"name": "t", "description": "d", "input_schema": {}}],
                "tool_choice": "auto",
            }
        )

        call_kwargs = mock_client.converse.call_args[1]
        assert call_kwargs["toolConfig"]["toolChoice"] == {"auto": {}}

    @pytest.mark.asyncio
    async def test_route_request_tool_choice_ignored_without_tools(self, mock_get_session):
        mock_client = MagicMock()
        mock_client.converse.return_value = {
            "output": {"message": {"content": [{"text": "ok"}]}},
            "stopReason": "end_turn",
            "usage": {},
        }
        mock_session = MagicMock()
        mock_session.client.return_value = mock_client
        mock_get_session.return_value = mock_session

        provider = BedrockConverseProvider()
        await provider.route_request(
            {
                "messages": [{"role": "user", "content": "Hi"}],
                "tool_choice": "auto",
            }
        )

        call_kwargs = mock_client.converse.call_args[1]
        assert "toolConfig" not in call_kwargs

    @pytest.mark.asyncio
    async def test_route_request_uses_get_boto3_session(self, mock_get_session):
        mock_client = MagicMock()
        mock_client.converse.return_value = {
            "output": {"message": {"content": [{"text": "ok"}]}},
            "stopReason": "end_turn",
            "usage": {},
        }
        mock_session = MagicMock()
        mock_session.client.return_value = mock_client
        mock_get_session.return_value = mock_session

        provider = BedrockConverseProvider(region="us-west-2")
        await provider.route_request({"messages": [{"role": "user", "content": "Hi"}]})

        mock_get_session.assert_called_once_with(default_region="us-west-2")

    @pytest.mark.asyncio
    async def test_list_models(self, mock_get_session):
        provider = BedrockConverseProvider(default_model="my-model")
        models = await provider.list_models()

        assert len(models) == 1
        assert models[0]["name"] == "my-model"
        assert models[0]["provider"] == "bedrock"
        assert "tool_use" in models[0]["capabilities"]

    @pytest.mark.asyncio
    async def test_healthcheck_success(self, mock_get_session):
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session

        provider = BedrockConverseProvider()
        assert await provider.healthcheck() is True

    @pytest.mark.asyncio
    async def test_healthcheck_failure(self, mock_get_session):
        mock_get_session.side_effect = Exception("No credentials")

        provider = BedrockConverseProvider()
        assert await provider.healthcheck() is False


# ── route_request_stream ─────────────────────────────────────────────────


@patch(f"{_PATCH_PREFIX}.get_boto3_session")
class TestRouteRequestStream:
    """Tests for the streaming Bedrock Converse API path."""

    @pytest.mark.asyncio
    async def test_stream_text_only(self, mock_get_session):
        events = [
            {"contentBlockDelta": {"delta": {"text": "Hello"}}},
            {"contentBlockDelta": {"delta": {"text": " world"}}},
            {"messageStop": {"stopReason": "end_turn"}},
            {"metadata": {"usage": {"inputTokens": 10, "outputTokens": 5}}},
        ]
        mock_client = MagicMock()
        mock_client.converse_stream.return_value = {"stream": iter(events)}
        mock_get_session.return_value.client.return_value = mock_client

        provider = BedrockConverseProvider()
        collected = []
        async for event in provider.route_request_stream({"messages": [{"role": "user", "content": "Hi"}]}):
            collected.append(event)

        types = [e["type"] for e in collected]
        assert "content_delta" in types
        assert "message_stop" in types
        assert "metadata" in types
        assert collected[0]["delta"] == "Hello"
        assert collected[1]["delta"] == " world"

    @pytest.mark.asyncio
    async def test_stream_tool_use(self, mock_get_session):
        events = [
            {
                "contentBlockStart": {
                    "start": {"toolUse": {"toolUseId": "tc-1", "name": "get_weather"}},
                }
            },
            {"contentBlockDelta": {"delta": {"toolUse": {"input": '{"city":'}}}},
            {"contentBlockDelta": {"delta": {"toolUse": {"input": '"NYC"}'}}}},
            {"contentBlockStop": {}},
            {"messageStop": {"stopReason": "tool_use"}},
        ]
        mock_client = MagicMock()
        mock_client.converse_stream.return_value = {"stream": iter(events)}
        mock_get_session.return_value.client.return_value = mock_client

        provider = BedrockConverseProvider()
        collected = []
        async for event in provider.route_request_stream({"messages": [{"role": "user", "content": "Weather?"}]}):
            collected.append(event)

        types = [e["type"] for e in collected]
        assert "tool_use_start" in types
        assert "tool_use_complete" in types

        complete = next(e for e in collected if e["type"] == "tool_use_complete")
        assert complete["name"] == "get_weather"
        assert complete["input"] == {"city": "NYC"}

    @pytest.mark.asyncio
    async def test_stream_tool_use_invalid_json(self, mock_get_session):
        events = [
            {
                "contentBlockStart": {
                    "start": {"toolUse": {"toolUseId": "tc-1", "name": "tool"}},
                }
            },
            {"contentBlockDelta": {"delta": {"toolUse": {"input": "not-json"}}}},
            {"contentBlockStop": {}},
        ]
        mock_client = MagicMock()
        mock_client.converse_stream.return_value = {"stream": iter(events)}
        mock_get_session.return_value.client.return_value = mock_client

        provider = BedrockConverseProvider()
        collected = []
        async for event in provider.route_request_stream({"messages": [{"role": "user", "content": "Hi"}]}):
            collected.append(event)

        complete = next(e for e in collected if e["type"] == "tool_use_complete")
        assert complete["input"] == {"raw": "not-json"}

    @pytest.mark.asyncio
    async def test_stream_empty(self, mock_get_session):
        mock_client = MagicMock()
        mock_client.converse_stream.return_value = {"stream": None}
        mock_get_session.return_value.client.return_value = mock_client

        provider = BedrockConverseProvider()
        collected = []
        async for event in provider.route_request_stream({"messages": [{"role": "user", "content": "Hi"}]}):
            collected.append(event)

        assert collected == []

    @pytest.mark.asyncio
    async def test_stream_with_inference_config_and_tools(self, mock_get_session):
        events = [
            {"contentBlockDelta": {"delta": {"text": "ok"}}},
            {"messageStop": {"stopReason": "end_turn"}},
        ]
        mock_client = MagicMock()
        mock_client.converse_stream.return_value = {"stream": iter(events)}
        mock_get_session.return_value.client.return_value = mock_client

        provider = BedrockConverseProvider()
        collected = []
        async for event in provider.route_request_stream(
            {
                "messages": [{"role": "user", "content": "Hi"}],
                "temperature": 0.5,
                "max_tokens": 100,
                "tools": [{"name": "calc", "description": "calc", "input_schema": {"type": "object"}}],
                "tool_choice": {"type": "auto"},
            }
        ):
            collected.append(event)

        call_kwargs = mock_client.converse_stream.call_args[1]
        assert call_kwargs["inferenceConfig"]["temperature"] == 0.5
        assert call_kwargs["inferenceConfig"]["maxTokens"] == 100
        assert "toolConfig" in call_kwargs
