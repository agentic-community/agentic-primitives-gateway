"""LLM provider for any OpenAI-compatible API endpoint.

Works with OpenAI, LM Studio, Ollama, vLLM, TGI, Azure OpenAI, and any
server that implements the ``/v1/chat/completions`` endpoint. Supports
streaming and tool use. No dependencies beyond httpx.

Provider config examples::

    # OpenAI
    backend: agentic_primitives_gateway.primitives.llm.openai_compatible.OpenAICompatibleProvider
    config:
      base_url: "https://api.openai.com"
      default_model: "gpt-4o"
      api_key: "${OPENAI_API_KEY}"

    # LM Studio (local)
    backend: agentic_primitives_gateway.primitives.llm.openai_compatible.OpenAICompatibleProvider
    config:
      base_url: "http://localhost:1234"

    # Ollama
    backend: agentic_primitives_gateway.primitives.llm.openai_compatible.OpenAICompatibleProvider
    config:
      base_url: "http://localhost:11434"
      default_model: "llama3"
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from agentic_primitives_gateway.primitives._sync import SyncRunnerMixin
from agentic_primitives_gateway.primitives.llm.base import LLMProvider

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider(SyncRunnerMixin, LLMProvider):
    """LLM provider for any OpenAI-compatible chat completions endpoint."""

    def __init__(
        self,
        base_url: str = "https://api.openai.com",
        default_model: str = "",
        api_key: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        self._api_key = api_key or ""
        logger.info("OpenAI-compatible provider initialized (base_url=%s)", self._base_url)

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _build_openai_request(self, model_request: dict[str, Any]) -> dict[str, Any]:
        """Build an OpenAI-compatible request from the gateway's internal format."""
        model = model_request.get("model") or self._default_model

        messages: list[dict[str, Any]] = []
        system = model_request.get("system")
        if system:
            messages.append({"role": "system", "content": system})
        messages.extend(model_request.get("messages", []))

        openai_messages = _to_openai_messages(messages)

        request: dict[str, Any] = {"messages": openai_messages}
        if model:
            request["model"] = model

        if model_request.get("temperature") is not None:
            request["temperature"] = model_request["temperature"]
        if model_request.get("max_tokens") is not None:
            request["max_tokens"] = model_request["max_tokens"]

        tools = model_request.get("tools")
        if tools:
            request["tools"] = _to_openai_tools(tools)

        tool_choice = model_request.get("tool_choice")
        if tool_choice and tools:
            request["tool_choice"] = _to_openai_tool_choice(tool_choice)

        return request

    async def route_request(self, model_request: dict[str, Any]) -> dict[str, Any]:
        request = self._build_openai_request(model_request)

        def _call() -> dict[str, Any]:
            with httpx.Client(timeout=120) as client:
                resp = client.post(
                    f"{self._base_url}/v1/chat/completions",
                    json=request,
                    headers=self._headers(),
                )
                resp.raise_for_status()
                result: dict[str, Any] = resp.json()
                return result

        data: dict[str, Any] = await self._run_sync(_call)
        return _from_openai_response(data)

    async def route_request_stream(self, model_request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        request = self._build_openai_request(model_request)
        request["stream"] = True

        loop = asyncio.get_event_loop()
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        def _stream() -> None:
            try:
                with (
                    httpx.Client(timeout=120) as client,
                    client.stream(
                        "POST",
                        f"{self._base_url}/v1/chat/completions",
                        json=request,
                        headers=self._headers(),
                    ) as resp,
                ):
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        if line.startswith("data: "):
                            payload = line[6:].strip()
                            if payload == "[DONE]":
                                break
                            try:
                                chunk = json.loads(payload)
                                loop.call_soon_threadsafe(queue.put_nowait, chunk)
                            except json.JSONDecodeError:
                                continue
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        asyncio.get_event_loop().run_in_executor(None, _stream)

        tool_calls: dict[int, dict[str, Any]] = {}

        while True:
            chunk = await queue.get()
            if chunk is None:
                break

            choices = chunk.get("choices", [])
            if not choices:
                continue

            delta = choices[0].get("delta", {})
            finish_reason = choices[0].get("finish_reason")

            content = delta.get("content")
            if content:
                yield {"type": "content_delta", "delta": content}

            for tc in delta.get("tool_calls", []):
                idx = tc.get("index", 0)
                if idx not in tool_calls:
                    tool_calls[idx] = {
                        "id": tc.get("id", ""),
                        "name": tc.get("function", {}).get("name", ""),
                        "args": "",
                    }
                    yield {
                        "type": "tool_use_start",
                        "id": tool_calls[idx]["id"],
                        "name": tool_calls[idx]["name"],
                    }

                args_delta = tc.get("function", {}).get("arguments", "")
                if args_delta:
                    tool_calls[idx]["args"] += args_delta
                    yield {
                        "type": "tool_use_delta",
                        "id": tool_calls[idx]["id"],
                        "delta": args_delta,
                    }

            if finish_reason:
                for tc_data in tool_calls.values():
                    try:
                        parsed_input = json.loads(tc_data["args"]) if tc_data["args"] else {}
                    except json.JSONDecodeError:
                        parsed_input = {"raw": tc_data["args"]}
                    yield {
                        "type": "tool_use_complete",
                        "id": tc_data["id"],
                        "name": tc_data["name"],
                        "input": parsed_input,
                    }
                tool_calls.clear()

                stop_reason = "tool_use" if finish_reason == "tool_calls" else finish_reason
                yield {
                    "type": "message_stop",
                    "stop_reason": stop_reason,
                    "model": chunk.get("model", ""),
                }

            usage = chunk.get("usage")
            if usage:
                yield {
                    "type": "metadata",
                    "usage": {
                        "input_tokens": usage.get("prompt_tokens", 0),
                        "output_tokens": usage.get("completion_tokens", 0),
                    },
                }

    async def list_models(self) -> list[dict[str, Any]]:
        def _list() -> list[dict[str, Any]]:
            with httpx.Client(timeout=10) as client:
                resp = client.get(f"{self._base_url}/v1/models", headers=self._headers())
                resp.raise_for_status()
                data = resp.json()

            return [
                {
                    "name": m.get("id", ""),
                    "provider": "openai_compatible",
                    "capabilities": ["chat", "tool_use"],
                }
                for m in data.get("data", [])
            ]

        result: list[dict[str, Any]] = await self._run_sync(_list)
        return result

    async def healthcheck(self) -> bool | str:
        try:
            with httpx.Client(timeout=5) as client:
                resp = client.get(f"{self._base_url}/v1/models", headers=self._headers())
                return resp.status_code < 500
        except Exception:
            return False


# ── Format translation ────────────────────────────────────────────────


def _to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert gateway message format to OpenAI chat format."""
    result: list[dict[str, Any]] = []
    for msg in messages:
        if "tool_results" in msg:
            for tr in msg["tool_results"]:
                result.append(
                    {
                        "role": "tool",
                        "tool_call_id": tr["tool_use_id"],
                        "content": tr["content"],
                    }
                )
            continue
        if "tool_result" in msg:
            tr = msg["tool_result"]
            result.append(
                {
                    "role": "tool",
                    "tool_call_id": tr["tool_use_id"],
                    "content": tr["content"],
                }
            )
            continue

        role = msg.get("role", "user")
        content = msg.get("content", "")

        tool_calls = msg.get("tool_calls")
        if role == "assistant" and tool_calls:
            entry: dict[str, Any] = {"role": "assistant", "content": content or None}
            entry["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc.get("input", {})),
                    },
                }
                for tc in tool_calls
            ]
            result.append(entry)
            continue

        result.append({"role": role, "content": content})

    return result


def _to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert gateway tool format to OpenAI function-calling format."""
    result = []
    for tool in tools:
        if "function" in tool:
            result.append(tool)
            continue
        result.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", tool.get("parameters", {})),
                },
            }
        )
    return result


def _to_openai_tool_choice(tool_choice: str | dict[str, Any]) -> str | dict[str, Any]:
    """Convert gateway tool_choice to OpenAI format."""
    if isinstance(tool_choice, str):
        if tool_choice in ("auto", "none", "required"):
            return tool_choice
        if tool_choice == "any":
            return "required"
        return {"type": "function", "function": {"name": tool_choice}}
    return tool_choice


def _from_openai_response(data: dict[str, Any]) -> dict[str, Any]:
    """Convert an OpenAI chat completion response to gateway format."""
    choices = data.get("choices", [])
    if not choices:
        return {"model": data.get("model", ""), "content": "", "usage": {}}

    message = choices[0].get("message", {})
    content = message.get("content", "") or ""

    tool_calls = None
    if message.get("tool_calls"):
        tool_calls = []
        for tc in message["tool_calls"]:
            fn = tc.get("function", {})
            args = fn.get("arguments", "")
            try:
                parsed = json.loads(args) if args else {}
            except json.JSONDecodeError:
                parsed = {"raw": args}
            tool_calls.append(
                {
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "input": parsed,
                }
            )

    usage_data = data.get("usage", {})
    usage = {
        "input_tokens": usage_data.get("prompt_tokens", 0),
        "output_tokens": usage_data.get("completion_tokens", 0),
    }

    finish = choices[0].get("finish_reason", "stop")
    if finish == "tool_calls":
        finish = "tool_use"

    result: dict[str, Any] = {
        "model": data.get("model", ""),
        "content": content,
        "stop_reason": finish,
        "usage": usage,
    }
    if tool_calls:
        result["tool_calls"] = tool_calls

    return result
