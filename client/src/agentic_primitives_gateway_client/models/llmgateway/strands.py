"""Strands-compatible model that routes inference through the Agentic Primitives Gateway.

Usage::

    from agentic_primitives_gateway_client import AgenticPlatformClient
    from agentic_primitives_gateway_client.models import LLMGateway
    from strands import Agent

    client = AgenticPlatformClient("http://localhost:8000", aws_from_environment=True)
    model = LLMGateway(client)

    agent = Agent(model=model, tools=client.get_tools_sync(["memory"], format="strands"))
    agent("Hello!")
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMGateway:
    """Strands Model implementation backed by the gateway's LLM primitive.

    All inference calls are routed through the gateway's ``/api/v1/llm/completions/stream``
    endpoint. The gateway controls which actual model and provider is used.

    Uses a sync httpx client in a background thread (like Strands' BedrockModel)
    to avoid cross-event-loop issues — Strands runs each invocation in a fresh
    ``asyncio.run()`` on a new thread.
    """

    def __init__(
        self,
        client: Any,
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> None:
        self._client = client
        self._base_url = str(client._client.base_url)
        self._timeout = client._client.timeout
        self._config: dict[str, Any] = {}
        if model is not None:
            self._config["model"] = model
        if max_tokens is not None:
            self._config["max_tokens"] = max_tokens
        if temperature is not None:
            self._config["temperature"] = temperature

    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    def get_config(self) -> dict[str, Any]:
        return dict(self._config)

    def _sync_stream(
        self,
        request: dict[str, Any],
        callback: Any,
    ) -> None:
        """Run the HTTP streaming request synchronously in a background thread."""
        try:
            with httpx.Client(base_url=self._base_url, timeout=self._timeout) as http:
                headers = self._client._headers
                with http.stream("POST", "/api/v1/llm/completions/stream", json=request, headers=headers) as resp:
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        if line.startswith("data: "):
                            event = json.loads(line[6:])
                            callback(event)
        finally:
            callback(None)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tool_specs: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
        *,
        tool_choice: dict[str, Any] | None = None,
        system_prompt_content: list[dict[str, Any]] | None = None,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream inference through the gateway, yielding Strands StreamEvents."""
        request = self._build_request(messages, tool_specs, system_prompt, system_prompt_content, tool_choice)

        loop = asyncio.get_event_loop()
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        def callback(event: dict[str, Any] | None) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

        task = asyncio.ensure_future(asyncio.to_thread(self._sync_stream, request, callback))

        content_block_index = 0
        in_text_block = False

        yield {"messageStart": {"role": "assistant"}}

        while True:
            event = await queue.get()
            if event is None:
                break
            event_type = event.get("type")

            if event_type == "content_delta":
                if not in_text_block:
                    yield {"contentBlockStart": {"contentBlockIndex": content_block_index, "start": {}}}
                    in_text_block = True
                yield {
                    "contentBlockDelta": {
                        "contentBlockIndex": content_block_index,
                        "delta": {"text": event["delta"]},
                    }
                }

            elif event_type == "tool_use_start":
                if in_text_block:
                    yield {"contentBlockStop": {"contentBlockIndex": content_block_index}}
                    content_block_index += 1
                    in_text_block = False
                yield {
                    "contentBlockStart": {
                        "contentBlockIndex": content_block_index,
                        "start": {
                            "toolUse": {
                                "toolUseId": event.get("id", str(uuid.uuid4())),
                                "name": event["name"],
                            }
                        },
                    }
                }
                # If the start event includes the full input (non-streaming provider),
                # emit it as a single delta.
                tool_input = event.get("input")
                if tool_input:
                    yield {
                        "contentBlockDelta": {
                            "contentBlockIndex": content_block_index,
                            "delta": {"toolUse": {"input": json.dumps(tool_input)}},
                        }
                    }

            elif event_type == "tool_use_delta":
                yield {
                    "contentBlockDelta": {
                        "contentBlockIndex": content_block_index,
                        "delta": {"toolUse": {"input": event["delta"]}},
                    }
                }

            elif event_type == "tool_use_complete":
                # Bedrock provider emits this after reassembling tool JSON.
                # Emit the full input as a delta, then close the block.
                tool_input = event.get("input", {})
                yield {
                    "contentBlockDelta": {
                        "contentBlockIndex": content_block_index,
                        "delta": {"toolUse": {"input": json.dumps(tool_input)}},
                    }
                }
                yield {"contentBlockStop": {"contentBlockIndex": content_block_index}}
                content_block_index += 1

            elif event_type == "message_stop":
                if in_text_block:
                    yield {"contentBlockStop": {"contentBlockIndex": content_block_index}}
                    content_block_index += 1
                    in_text_block = False
                yield {
                    "messageStop": {
                        "stopReason": event.get("stop_reason", "end_turn"),
                    }
                }

            elif event_type == "metadata":
                usage = event.get("usage", {})
                yield {
                    "metadata": {
                        "usage": {
                            "inputTokens": usage.get("input_tokens", 0),
                            "outputTokens": usage.get("output_tokens", 0),
                        },
                    }
                }

        await task

    async def structured_output(
        self,
        output_model: type[T],
        prompt: list[dict[str, Any]],
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, T | Any], None]:
        """Get structured output by forcing a tool call."""
        schema = output_model.model_json_schema()
        tool_spec = {
            "name": "structured_output",
            "description": f"Return a response matching the {output_model.__name__} schema.",
            "inputSchema": {"json": schema},
        }
        tool_choice: dict[str, Any] = {"tool": {"name": "structured_output"}}

        tool_input: dict[str, Any] = {}
        async for event in self.stream(
            prompt,
            tool_specs=[tool_spec],
            system_prompt=system_prompt,
            tool_choice=tool_choice,
            **kwargs,
        ):
            yield event
            # Collect tool input from deltas
            if "contentBlockDelta" in event:
                delta = event["contentBlockDelta"].get("delta", {})
                if "toolUse" in delta:
                    chunk = delta["toolUse"].get("input", "")
                    if isinstance(chunk, str):
                        tool_input.setdefault("_raw", "")
                        tool_input["_raw"] += chunk

        # Parse and validate
        raw = tool_input.get("_raw", "{}")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {}
        yield {"output": output_model.model_validate(parsed)}

    def _build_request(
        self,
        messages: list[dict[str, Any]],
        tool_specs: list[dict[str, Any]] | None,
        system_prompt: str | None,
        system_prompt_content: list[dict[str, Any]] | None,
        tool_choice: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Build a gateway CompletionRequest from Strands-format inputs."""
        # Convert Strands messages (Bedrock content-block format) to gateway format
        gateway_messages = _to_gateway_messages(messages)

        # Resolve system prompt
        sys_prompt = None
        if system_prompt_content:
            # Extract text from system content blocks
            parts = [block.get("text", "") for block in system_prompt_content if "text" in block]
            if parts:
                sys_prompt = "\n".join(parts)
        if sys_prompt is None and system_prompt:
            sys_prompt = system_prompt

        request: dict[str, Any] = {"messages": gateway_messages, **self._config}
        if sys_prompt:
            request["system"] = sys_prompt
        if tool_specs:
            request["tools"] = [
                {
                    "name": ts["name"],
                    "description": ts.get("description", ""),
                    "input_schema": ts.get("inputSchema", {}).get("json", ts.get("inputSchema", {})),
                }
                for ts in tool_specs
            ]
        if tool_choice:
            request["tool_choice"] = _to_gateway_tool_choice(tool_choice)

        return request


def _to_gateway_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Strands Bedrock-style messages to gateway internal format."""
    result: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "user")
        content_blocks = msg.get("content", [])

        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []

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
            elif "toolResult" in block:
                tr = block["toolResult"]
                content_text = ""
                for c in tr.get("content", []):
                    if "text" in c:
                        content_text += c["text"]
                    elif "json" in c:
                        content_text += json.dumps(c["json"])
                tool_results.append(
                    {
                        "tool_use_id": tr["toolUseId"],
                        "content": content_text,
                    }
                )

        if tool_results:
            result.append({"tool_results": tool_results})
        elif tool_calls:
            entry: dict[str, Any] = {"role": role, "content": "\n".join(text_parts)}
            entry["tool_calls"] = tool_calls
            result.append(entry)
        else:
            result.append({"role": role, "content": "\n".join(text_parts)})

    return result


def _to_gateway_tool_choice(tc: dict[str, Any]) -> str | dict[str, Any]:
    """Convert Strands ToolChoice to gateway format."""
    if "auto" in tc:
        return "auto"
    if "any" in tc:
        return "any"
    if "tool" in tc:
        return str(tc["tool"].get("name", "auto"))
    return "auto"
