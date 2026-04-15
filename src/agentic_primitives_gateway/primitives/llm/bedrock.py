from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from agentic_primitives_gateway.context import get_boto3_session
from agentic_primitives_gateway.primitives._sync import SyncRunnerMixin
from agentic_primitives_gateway.primitives.llm.base import LLMProvider

logger = logging.getLogger(__name__)


async def _parse_bedrock_stream(
    queue: asyncio.Queue[dict[str, Any] | None],
    model_id: str,
) -> AsyncIterator[dict[str, Any]]:
    """Parse Bedrock converse_stream events into normalized dicts.

    Handles tool-use reassembly: input JSON arrives as incremental string
    chunks across contentBlockDelta events, assembled on contentBlockStop.
    """
    tool_id = ""
    tool_name = ""
    tool_json = ""

    while True:
        event = await queue.get()
        if event is None:
            break

        if "contentBlockStart" in event:
            start = event["contentBlockStart"].get("start", {})
            if "toolUse" in start:
                tu = start["toolUse"]
                tool_id = tu.get("toolUseId", "")
                tool_name = tu.get("name", "")
                tool_json = ""
                yield {"type": "tool_use_start", "id": tool_id, "name": tool_name}

        elif "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                yield {"type": "content_delta", "delta": delta["text"]}
            elif "toolUse" in delta:
                tool_json += delta["toolUse"].get("input", "")

        elif "contentBlockStop" in event:
            if tool_name:
                try:
                    parsed_input = json.loads(tool_json) if tool_json else {}
                except json.JSONDecodeError:
                    parsed_input = {"raw": tool_json}
                yield {"type": "tool_use_complete", "id": tool_id, "name": tool_name, "input": parsed_input}
                tool_name = ""
                tool_json = ""

        elif "messageStop" in event:
            yield {
                "type": "message_stop",
                "stop_reason": event["messageStop"].get("stopReason", "end_turn"),
                "model": model_id,
            }

        elif "metadata" in event:
            usage = event["metadata"].get("usage", {})
            yield {
                "type": "metadata",
                "usage": {"input_tokens": usage.get("inputTokens", 0), "output_tokens": usage.get("outputTokens", 0)},
            }


class BedrockConverseProvider(SyncRunnerMixin, LLMProvider):
    """Gateway provider using the Bedrock Converse API.

    Supports tool_use: pass tools in the request dict, get tool_calls back
    in the response. Translates between the gateway's internal message format
    and Bedrock Converse format.

    Provider config example::

        backend: agentic_primitives_gateway.primitives.llm.bedrock.BedrockConverseProvider
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
        model_id = model_request.get("model") or self._default_model

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
        model_id = model_request.get("model") or self._default_model

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

        try:
            async for parsed in _parse_bedrock_stream(queue, model_id):
                yield parsed
        finally:
            # Close the boto3 EventStream to kill the HTTP connection.
            # This stops the drain thread immediately instead of letting
            # the LLM response complete (which could take minutes).
            if hasattr(stream, "close"):
                stream.close()

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


def _tool_result_block(tr: dict[str, Any]) -> dict[str, Any]:
    """Convert a single tool result dict to a Bedrock toolResult content block."""
    content_text = tr.get("content", "")
    if not isinstance(content_text, str):
        content_text = json.dumps(content_text, default=str)
    return {"toolResult": {"toolUseId": tr["tool_use_id"], "content": [{"text": content_text}]}}


def _convert_message(msg: dict[str, Any], system_prompts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Convert a single internal message to Bedrock format. Returns None for system messages."""
    role = msg.get("role", "user")

    if role == "system":
        system_prompts.append({"text": msg.get("content", "")})
        return None

    if "tool_results" in msg:
        return {"role": "user", "content": [_tool_result_block(tr) for tr in msg["tool_results"]]}

    if "tool_result" in msg:
        return {"role": "user", "content": [_tool_result_block(msg["tool_result"])]}

    tool_calls = msg.get("tool_calls")
    if role == "assistant" and tool_calls:
        blocks: list[dict[str, Any]] = []
        if msg.get("content"):
            blocks.append({"text": msg["content"]})
        for tc in tool_calls:
            tool_input = tc.get("input", {})
            if isinstance(tool_input, str):
                try:
                    tool_input = json.loads(tool_input)
                except (json.JSONDecodeError, TypeError):
                    tool_input = {"raw": tool_input}
            blocks.append({"toolUse": {"toolUseId": tc["id"], "name": tc["name"], "input": tool_input}})
        return {"role": "assistant", "content": blocks}

    content = msg.get("content", "")
    if isinstance(content, list):
        return {"role": role, "content": content}
    return {"role": role, "content": [{"text": str(content)}]}


def _to_bedrock_messages(
    model_request: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convert internal messages to Bedrock Converse format.

    Returns (system_prompts, messages).

    The internal format uses flat dicts with optional keys:
      - {"role": "user/assistant", "content": "text"}
      - {"role": "assistant", "content": "text", "tool_calls": [...]}
      - {"tool_results": [{"tool_use_id": "...", "content": "..."}]}  (batched)
      - {"tool_result": {"tool_use_id": "...", "content": "..."}}     (single format)

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
        converted = _convert_message(msg, system_prompts)
        if converted is not None:
            bedrock_messages.append(converted)

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
