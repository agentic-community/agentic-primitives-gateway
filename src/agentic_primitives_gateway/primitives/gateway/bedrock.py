from __future__ import annotations

import json
import logging
from typing import Any

from agentic_primitives_gateway.context import get_boto3_session
from agentic_primitives_gateway.primitives._sync import SyncRunnerMixin
from agentic_primitives_gateway.primitives.gateway.base import GatewayProvider

logger = logging.getLogger(__name__)


class BedrockConverseProvider(SyncRunnerMixin, GatewayProvider):
    """Gateway provider using the Bedrock Converse API.

    Supports tool_use: pass tools in the request dict, get tool_calls back
    in the response. Translates between the gateway's internal message format
    and Bedrock Converse format.

    Provider config example::

        backend: agentic_primitives_gateway.primitives.gateway.bedrock.BedrockConverseProvider
        config:
          region: "us-east-1"
          default_model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
    """

    def __init__(
        self,
        region: str = "us-east-1",
        default_model: str = "us.anthropic.claude-sonnet-4-20250514-v1:0",
        **kwargs: Any,
    ) -> None:
        self._region = region
        self._default_model = default_model
        logger.info("Bedrock Converse gateway provider initialized (region=%s)", region)

    def _get_client(self) -> Any:
        """Create a per-request bedrock-runtime client from context credentials."""
        session = get_boto3_session(default_region=self._region)
        return session.client("bedrock-runtime")

    async def route_request(self, model_request: dict[str, Any]) -> dict[str, Any]:
        client = self._get_client()
        model_id = model_request.get("model", self._default_model)

        system_prompts, messages = _to_bedrock_messages(model_request)
        converse_kwargs: dict[str, Any] = {
            "modelId": model_id,
            "messages": messages,
        }
        if system_prompts:
            converse_kwargs["system"] = system_prompts

        inference_config: dict[str, Any] = {}
        if model_request.get("temperature") is not None:
            inference_config["temperature"] = model_request["temperature"]
        if model_request.get("max_tokens") is not None:
            inference_config["maxTokens"] = model_request["max_tokens"]
        if inference_config:
            converse_kwargs["inferenceConfig"] = inference_config

        tools = model_request.get("tools")
        if tools:
            converse_kwargs["toolConfig"] = {"tools": _to_bedrock_tools(tools)}

        tool_choice = model_request.get("tool_choice")
        if tool_choice and tools:
            converse_kwargs["toolConfig"]["toolChoice"] = _to_bedrock_tool_choice(
                tool_choice,
            )

        def _call() -> dict[str, Any]:
            return client.converse(**converse_kwargs)

        response = await self._run_sync(_call)
        return _from_bedrock_response(response, model_id)

    async def list_models(self) -> list[dict[str, Any]]:
        return [
            {
                "name": self._default_model,
                "provider": "bedrock",
                "capabilities": ["chat", "tool_use"],
            }
        ]

    async def healthcheck(self) -> bool:
        try:
            self._get_client()
            return True
        except Exception:
            logger.exception("Bedrock healthcheck failed")
            return False


# ── Message translation ──────────────────────────────────────────────


def _to_bedrock_messages(
    model_request: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convert internal messages to Bedrock Converse format.

    Returns (system_prompts, messages).
    """
    raw_messages = model_request.get("messages", [])
    system_prompt = model_request.get("system")

    system_prompts: list[dict[str, Any]] = []
    if system_prompt:
        system_prompts.append({"text": system_prompt})

    bedrock_messages: list[dict[str, Any]] = []

    for msg in raw_messages:
        role = msg.get("role", "user")

        # System messages extracted to system_prompts
        if role == "system":
            system_prompts.append({"text": msg.get("content", "")})
            continue

        # Batched tool results (multiple results in one user message)
        if "tool_results" in msg:
            content_blocks: list[dict[str, Any]] = []
            for tr in msg["tool_results"]:
                content_text = tr.get("content", "")
                if not isinstance(content_text, str):
                    content_text = json.dumps(content_text, default=str)
                content_blocks.append(
                    {
                        "toolResult": {
                            "toolUseId": tr["tool_use_id"],
                            "content": [{"text": content_text}],
                        }
                    }
                )
            bedrock_messages.append({"role": "user", "content": content_blocks})
            continue

        # Single tool result message (legacy / direct API usage)
        if "tool_result" in msg:
            tr = msg["tool_result"]
            content_text = tr.get("content", "")
            if not isinstance(content_text, str):
                content_text = json.dumps(content_text, default=str)
            bedrock_messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "toolResult": {
                                "toolUseId": tr["tool_use_id"],
                                "content": [{"text": content_text}],
                            }
                        }
                    ],
                }
            )
            continue

        # Assistant messages with tool_calls
        tool_calls = msg.get("tool_calls")
        if role == "assistant" and tool_calls:
            content_blocks: list[dict[str, Any]] = []
            text_content = msg.get("content")
            if text_content:
                content_blocks.append({"text": text_content})
            for tc in tool_calls:
                tool_input = tc.get("input", {})
                if isinstance(tool_input, str):
                    try:
                        tool_input = json.loads(tool_input)
                    except (json.JSONDecodeError, TypeError):
                        tool_input = {"raw": tool_input}
                content_blocks.append(
                    {
                        "toolUse": {
                            "toolUseId": tc["id"],
                            "name": tc["name"],
                            "input": tool_input,
                        }
                    }
                )
            bedrock_messages.append({"role": "assistant", "content": content_blocks})
            continue

        # Regular text messages
        content = msg.get("content", "")
        if isinstance(content, str):
            bedrock_messages.append({"role": role, "content": [{"text": content}]})
        elif isinstance(content, list):
            # Already in content-block format
            bedrock_messages.append({"role": role, "content": content})
        else:
            bedrock_messages.append({"role": role, "content": [{"text": str(content)}]})

    return system_prompts, bedrock_messages


def _to_bedrock_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert internal tool definitions to Bedrock Converse toolConfig format."""
    bedrock_tools = []
    for tool in tools:
        # Support both {"toolSpec": ...} (already Bedrock format) and flat format
        if "toolSpec" in tool:
            bedrock_tools.append(tool)
            continue
        bedrock_tools.append(
            {
                "toolSpec": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "inputSchema": {"json": tool.get("input_schema", tool.get("parameters", {}))},
                }
            }
        )
    return bedrock_tools


def _to_bedrock_tool_choice(tool_choice: str | dict[str, Any]) -> dict[str, Any]:
    """Convert tool_choice to Bedrock format."""
    if isinstance(tool_choice, str):
        if tool_choice == "auto":
            return {"auto": {}}
        if tool_choice == "any":
            return {"any": {}}
        # Treat as specific tool name
        return {"tool": {"name": tool_choice}}
    return tool_choice


def _from_bedrock_response(response: dict[str, Any], model_id: str) -> dict[str, Any]:
    """Convert Bedrock Converse response to internal format."""
    output = response.get("output", {})
    message = output.get("message", {})
    content_blocks = message.get("content", [])

    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    for block in content_blocks:
        if "text" in block:
            text_parts.append(block["text"])
        elif "toolUse" in block:
            tu = block["toolUse"]
            tool_calls.append(
                {
                    "id": tu["toolUseId"],
                    "name": tu["name"],
                    "input": tu.get("input", {}),
                }
            )

    stop_reason = response.get("stopReason", "end_turn")
    usage = response.get("usage", {})

    result: dict[str, Any] = {
        "model": model_id,
        "content": "\n".join(text_parts) if text_parts else "",
        "stop_reason": stop_reason,
        "usage": {
            "input_tokens": usage.get("inputTokens", 0),
            "output_tokens": usage.get("outputTokens", 0),
        },
    }
    if tool_calls:
        result["tool_calls"] = tool_calls

    return result
