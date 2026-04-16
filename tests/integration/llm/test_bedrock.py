"""Integration tests for the Bedrock Converse LLM provider.

Full stack with real AWS calls:
AgenticPlatformClient -> ASGI -> middleware -> route -> registry ->
BedrockConverseProvider -> real Bedrock Converse API.

Requires: AWS credentials with Bedrock access.
"""

from __future__ import annotations

import os

import httpx
import pytest

import agentic_primitives_gateway.config as _config_module
from agentic_primitives_gateway.config import Settings
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway_client import AgenticPlatformClient

pytestmark = pytest.mark.integration

MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"


# -- Skip logic ---------------------------------------------------------------


def _has_aws_credentials() -> bool:
    """Check if AWS credentials are available via boto3."""
    try:
        import boto3

        sts = boto3.client("sts")
        sts.get_caller_identity()
        return True
    except Exception:
        return False


if not _has_aws_credentials():
    pytest.skip(
        "AWS credentials not available -- skipping Bedrock LLM integration tests",
        allow_module_level=True,
    )


# -- Registry initialization --------------------------------------------------


@pytest.fixture(autouse=True)
def _init_registry():
    """Initialise registry with Bedrock Converse for LLM and noop for everything else."""
    region = os.environ.get("AWS_REGION", "us-east-1")

    test_settings = Settings(
        allow_server_credentials="always",
        providers={
            "memory": {
                "backend": "agentic_primitives_gateway.primitives.memory.noop.NoopMemoryProvider",
                "config": {},
            },
            "observability": {
                "backend": "agentic_primitives_gateway.primitives.observability.noop.NoopObservabilityProvider",
                "config": {},
            },
            "llm": {
                "backend": "agentic_primitives_gateway.primitives.llm.bedrock.BedrockConverseProvider",
                "config": {"region": region, "default_model": MODEL_ID},
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
            "policy": {
                "backend": "agentic_primitives_gateway.primitives.policy.noop.NoopPolicyProvider",
                "config": {},
            },
            "evaluations": {
                "backend": "agentic_primitives_gateway.primitives.evaluations.noop.NoopEvaluationsProvider",
                "config": {},
            },
        },
    )
    orig_settings = _config_module.settings
    _config_module.settings = test_settings
    _config_module.settings.allow_server_credentials = "always"
    registry.initialize(test_settings)

    yield

    _config_module.settings = orig_settings


# -- Client fixture ------------------------------------------------------------


@pytest.fixture
async def client():
    """AgenticPlatformClient wired to the ASGI app with real AWS credentials."""
    transport = httpx.ASGITransport(app=app)
    async with AgenticPlatformClient(
        base_url="http://testserver",
        aws_from_environment=True,
        max_retries=2,
        transport=transport,
    ) as c:
        yield c


# -- Completions ---------------------------------------------------------------


class TestCompletions:
    async def test_simple_completion(self, client: AgenticPlatformClient) -> None:
        """Send a simple question and verify the response shape and content."""
        result = await client.completions(
            {
                "model": MODEL_ID,
                "messages": [{"role": "user", "content": "What is 2 + 2? Reply with just the number."}],
                "max_tokens": 64,
                "temperature": 0.0,
            }
        )

        assert result["model"] == MODEL_ID
        assert "content" in result
        assert "4" in result["content"]
        assert "usage" in result
        assert result["usage"]["input_tokens"] > 0
        assert result["usage"]["output_tokens"] > 0
        assert result["stop_reason"] == "end_turn"

    async def test_completion_with_system_prompt(self, client: AgenticPlatformClient) -> None:
        """Verify that the system prompt is respected."""
        result = await client.completions(
            {
                "model": MODEL_ID,
                "messages": [{"role": "user", "content": "What is the capital of France?"}],
                "system": "You are a helpful assistant. Always answer in exactly one word.",
                "max_tokens": 32,
                "temperature": 0.0,
            }
        )

        assert result["model"] == MODEL_ID
        assert "content" in result
        assert "Paris" in result["content"]

    async def test_completion_multi_turn(self, client: AgenticPlatformClient) -> None:
        """Verify multi-turn conversation works correctly."""
        result = await client.completions(
            {
                "model": MODEL_ID,
                "messages": [
                    {"role": "user", "content": "My name is Alice."},
                    {"role": "assistant", "content": "Hello, Alice! Nice to meet you."},
                    {"role": "user", "content": "What is my name? Reply with just the name."},
                ],
                "max_tokens": 32,
                "temperature": 0.0,
            }
        )

        assert "Alice" in result["content"]


# -- Completions with tool_use -------------------------------------------------


class TestCompletionsWithTools:
    async def test_tool_use(self, client: AgenticPlatformClient) -> None:
        """Send a request that should trigger tool use and verify the tool call shape."""
        tools = [
            {
                "name": "get_weather",
                "description": "Get the current weather for a given city.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "The city name"},
                    },
                    "required": ["city"],
                },
            }
        ]

        result = await client.completions(
            {
                "model": MODEL_ID,
                "messages": [{"role": "user", "content": "What is the weather in Paris?"}],
                "tools": tools,
                "max_tokens": 256,
                "temperature": 0.0,
            }
        )

        assert result["model"] == MODEL_ID
        assert result["stop_reason"] == "tool_use"
        assert "tool_calls" in result
        assert len(result["tool_calls"]) >= 1

        tool_call = result["tool_calls"][0]
        assert tool_call["name"] == "get_weather"
        assert "id" in tool_call
        assert "input" in tool_call
        assert tool_call["input"]["city"].lower() == "paris"

    async def test_tool_use_with_tool_choice(self, client: AgenticPlatformClient) -> None:
        """Verify tool_choice forces a specific tool to be called."""
        tools = [
            {
                "name": "calculator",
                "description": "Perform arithmetic calculations.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "expression": {"type": "string", "description": "The math expression to evaluate"},
                    },
                    "required": ["expression"],
                },
            },
            {
                "name": "search",
                "description": "Search the web for information.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                    },
                    "required": ["query"],
                },
            },
        ]

        result = await client.completions(
            {
                "model": MODEL_ID,
                "messages": [{"role": "user", "content": "What is 15 * 23?"}],
                "tools": tools,
                "tool_choice": "any",
                "max_tokens": 256,
                "temperature": 0.0,
            }
        )

        assert result["stop_reason"] == "tool_use"
        assert result["tool_calls"]
        assert result["tool_calls"][0]["name"] in ("calculator", "search")

    async def test_tool_result_round_trip(self, client: AgenticPlatformClient) -> None:
        """Verify the model can process a tool result and produce a final answer."""
        tools = [
            {
                "name": "get_weather",
                "description": "Get the current weather for a given city.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "The city name"},
                    },
                    "required": ["city"],
                },
            }
        ]

        # First turn: model requests tool use
        first = await client.completions(
            {
                "model": MODEL_ID,
                "messages": [{"role": "user", "content": "What is the weather in Tokyo?"}],
                "tools": tools,
                "max_tokens": 256,
                "temperature": 0.0,
            }
        )

        assert first["tool_calls"]
        tc = first["tool_calls"][0]

        # Second turn: provide tool result, model should produce a text answer
        second = await client.completions(
            {
                "model": MODEL_ID,
                "messages": [
                    {"role": "user", "content": "What is the weather in Tokyo?"},
                    {"role": "assistant", "content": first.get("content", ""), "tool_calls": first["tool_calls"]},
                    {
                        "tool_results": [
                            {
                                "tool_use_id": tc["id"],
                                "content": "Sunny, 25 degrees Celsius",
                            }
                        ]
                    },
                ],
                "tools": tools,
                "max_tokens": 256,
                "temperature": 0.0,
            }
        )

        assert second["model"] == MODEL_ID
        assert second["content"]
        # The model should mention the weather info from the tool result
        lower = second["content"].lower()
        assert "sunny" in lower or "25" in lower


# -- Streaming -----------------------------------------------------------------


class TestCompletionsStream:
    async def test_stream_completion(self, client: AgenticPlatformClient) -> None:
        """Verify streaming produces content_delta and message_stop events."""
        events: list[dict] = []
        async for event in client.completions_stream(
            {
                "model": MODEL_ID,
                "messages": [{"role": "user", "content": "Say hello in one word."}],
                "max_tokens": 32,
                "temperature": 0.0,
            }
        ):
            events.append(event)

        types = [e["type"] for e in events]
        assert "content_delta" in types, f"Expected content_delta events, got: {types}"
        assert "message_stop" in types, f"Expected message_stop event, got: {types}"

        # Reassemble streamed text
        text = "".join(e.get("delta", "") for e in events if e.get("type") == "content_delta")
        assert len(text) > 0

    async def test_stream_tool_use(self, client: AgenticPlatformClient) -> None:
        """Verify streaming correctly delivers tool_use events."""
        tools = [
            {
                "name": "get_weather",
                "description": "Get the current weather for a given city.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "The city name"},
                    },
                    "required": ["city"],
                },
            }
        ]

        events: list[dict] = []
        async for event in client.completions_stream(
            {
                "model": MODEL_ID,
                "messages": [{"role": "user", "content": "What is the weather in London?"}],
                "tools": tools,
                "max_tokens": 256,
                "temperature": 0.0,
            }
        ):
            events.append(event)

        types = [e["type"] for e in events]
        assert "tool_use_start" in types, f"Expected tool_use_start event, got: {types}"
        assert "tool_use_complete" in types, f"Expected tool_use_complete event, got: {types}"
        assert "message_stop" in types

        # Verify the completed tool call has expected structure
        complete_events = [e for e in events if e.get("type") == "tool_use_complete"]
        assert len(complete_events) >= 1
        tc = complete_events[0]
        assert tc["name"] == "get_weather"
        assert "id" in tc
        assert "input" in tc


# -- List models ---------------------------------------------------------------


class TestListModels:
    async def test_list_models(self, client: AgenticPlatformClient) -> None:
        """Verify the list_models endpoint returns the configured Bedrock model."""
        result = await client.list_models()

        assert "models" in result
        models = result["models"]
        assert len(models) >= 1

        names = [m["name"] for m in models]
        assert MODEL_ID in names

        # Verify model info shape
        bedrock_model = next(m for m in models if m["name"] == MODEL_ID)
        assert bedrock_model["provider"] == "bedrock"
        assert "chat" in bedrock_model["capabilities"]
        assert "tool_use" in bedrock_model["capabilities"]
