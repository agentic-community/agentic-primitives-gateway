from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
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

        response = await self._run_sync(client.converse, **converse_kwargs)
        result: dict[str, Any] = _from_bedrock_response(response, model_id)
        return result

    async def route_request_stream(self, model_request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Stream an LLM response via Bedrock's converse_stream API.

        Bedrock streams events as a synchronous iterator from a background thread.
        We bridge this to async using a queue: a thread drains the boto3 stream
        into the queue, and the async generator awaits events one at a time.

        Bedrock's stream event lifecycle for tool calls:
          contentBlockStart (toolUse with id+name) → contentBlockDelta (input JSON chunks)
          → contentBlockStop → we reassemble the accumulated JSON into a complete tool call.
        For text: contentBlockDelta contains text chunks directly.
        """
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
            converse_kwargs["toolConfig"]["toolChoice"] = _to_bedrock_tool_choice(tool_choice)

        response = await self._run_sync(client.converse_stream, **converse_kwargs)
        stream = response.get("stream")
        if stream is None:
            return

        # Bridge sync→async: boto3's stream is a blocking iterator that yields
        # one event at a time from the network. We can't iterate it directly in
        # async code. A background thread reads events and puts them on an
        # asyncio.Queue; call_soon_threadsafe ensures thread-safe enqueue.
        # None is the sentinel signaling the stream is exhausted.
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def _drain_stream() -> None:
            try:
                for ev in stream:
                    loop.call_soon_threadsafe(queue.put_nowait, ev)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        loop.run_in_executor(None, _drain_stream)

        # Tool use reassembly state: Bedrock sends tool input as incremental
        # JSON string chunks across multiple contentBlockDelta events. We
        # accumulate them and parse the complete JSON on contentBlockStop.
        current_tool_id = ""
        current_tool_name = ""
        current_tool_input_json = ""

        while True:
            event = await queue.get()
            if event is None:
                break

            # contentBlockStart: marks the beginning of a new content block.
            # For tool calls, this contains the tool name and ID.
            if "contentBlockStart" in event:
                start = event["contentBlockStart"].get("start", {})
                if "toolUse" in start:
                    tu = start["toolUse"]
                    current_tool_id = tu.get("toolUseId", "")
                    current_tool_name = tu.get("name", "")
                    current_tool_input_json = ""
                    yield {
                        "type": "tool_use_start",
                        "id": current_tool_id,
                        "name": current_tool_name,
                    }

            # contentBlockDelta: incremental content. Text deltas are yielded
            # immediately; tool input JSON chunks are accumulated for later parsing.
            elif "contentBlockDelta" in event:
                delta = event["contentBlockDelta"].get("delta", {})
                if "text" in delta:
                    yield {"type": "content_delta", "delta": delta["text"]}
                elif "toolUse" in delta:
                    current_tool_input_json += delta["toolUse"].get("input", "")

            # contentBlockStop: the block is complete. If we were accumulating
            # tool input JSON, parse it now and emit the complete tool call.
            elif "contentBlockStop" in event:
                if current_tool_name:
                    try:
                        parsed_input = json.loads(current_tool_input_json) if current_tool_input_json else {}
                    except json.JSONDecodeError:
                        parsed_input = {"raw": current_tool_input_json}
                    yield {
                        "type": "tool_use_complete",
                        "id": current_tool_id,
                        "name": current_tool_name,
                        "input": parsed_input,
                    }
                    current_tool_name = ""
                    current_tool_input_json = ""

            elif "messageStop" in event:
                stop_reason = event["messageStop"].get("stopReason", "end_turn")
                yield {
                    "type": "message_stop",
                    "stop_reason": stop_reason,
                    "model": model_id,
                }

            elif "metadata" in event:
                usage = event["metadata"].get("usage", {})
                yield {
                    "type": "metadata",
                    "usage": {
                        "input_tokens": usage.get("inputTokens", 0),
                        "output_tokens": usage.get("outputTokens", 0),
                    },
                }

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

    The internal format uses flat dicts with optional keys:
      - {"role": "user/assistant", "content": "text"}
      - {"role": "assistant", "content": "text", "tool_calls": [...]}
      - {"tool_results": [{"tool_use_id": "...", "content": "..."}]}  (batched)
      - {"tool_result": {"tool_use_id": "...", "content": "..."}}     (legacy single)

    Bedrock Converse requires content blocks:
      - Text: [{"text": "..."}]
      - Tool use: [{"toolUse": {"toolUseId": "...", "name": "...", "input": {...}}}]
      - Tool result: [{"toolResult": {"toolUseId": "...", "content": [{"text": "..."}]}}]

    System messages are extracted to a separate list (Bedrock requires them
    in the top-level "system" parameter, not inline in messages).
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
            content_blocks = []
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
