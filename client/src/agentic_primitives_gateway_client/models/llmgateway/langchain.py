"""LangChain-compatible chat model that routes inference through the gateway.

Usage::

    from agentic_primitives_gateway_client import AgenticPlatformClient
    from agentic_primitives_gateway_client.models import LLMGateway
    from langchain_core.messages import HumanMessage

    client = AgenticPlatformClient("http://localhost:8000", aws_from_environment=True)
    model = LLMGateway(client=client)

    response = model.invoke([HumanMessage(content="Hello!")])
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Iterator, Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool

logger = logging.getLogger(__name__)


class LLMGateway(BaseChatModel):
    """LangChain chat model backed by the gateway's LLM primitive."""

    client: Any = None
    model_name: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None

    @property
    def _llm_type(self) -> str:
        return "gateway"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable:
        formatted = [convert_to_openai_tool(t) for t in tools]
        bind_kwargs: dict[str, Any] = {"tools": formatted, **kwargs}
        if tool_choice is not None:
            bind_kwargs["tool_choice"] = tool_choice
        return self.bind(**bind_kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        request = self._build_request(messages, stop, **kwargs)

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(self.client.completions(request))
        finally:
            loop.close()

        return _parse_response(result)

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        request = self._build_request(messages, stop, **kwargs)

        loop = asyncio.new_event_loop()
        try:
            queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

            async def _drain():
                async for event in self.client.completions_stream(request):
                    await queue.put(event)
                await queue.put(None)

            task = loop.create_task(_drain())

            while True:
                event = loop.run_until_complete(queue.get())
                if event is None:
                    break
                chunk = _event_to_chunk(event)
                if chunk is not None:
                    if run_manager:
                        run_manager.on_llm_new_token(
                            chunk.text,
                            chunk=chunk,
                        )
                    yield chunk

            loop.run_until_complete(task)
        finally:
            loop.close()

    def _build_request(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        gateway_messages, system_prompt = _to_gateway_messages(messages)
        request: dict[str, Any] = {"messages": gateway_messages}

        if self.model_name is not None:
            request["model"] = self.model_name
        if self.temperature is not None:
            request["temperature"] = self.temperature
        if self.max_tokens is not None:
            request["max_tokens"] = self.max_tokens
        if system_prompt:
            request["system"] = system_prompt

        # Merge any kwargs (e.g. tools from bind_tools)
        if "tools" in kwargs:
            request["tools"] = [_langchain_tool_to_gateway(t) for t in kwargs["tools"]]
        if stop:
            request["stop"] = stop

        return request


def _to_gateway_messages(
    messages: list[BaseMessage],
) -> tuple[list[dict[str, Any]], str | None]:
    """Convert LangChain messages to gateway format, extracting system prompt."""
    result: list[dict[str, Any]] = []
    system_prompt: str | None = None

    for msg in messages:
        if isinstance(msg, SystemMessage):
            system_prompt = str(msg.content)
        elif isinstance(msg, HumanMessage):
            result.append({"role": "user", "content": str(msg.content)})
        elif isinstance(msg, AIMessage):
            entry: dict[str, Any] = {"role": "assistant", "content": str(msg.content)}
            if msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": tc.get("args", {}),
                    }
                    for tc in msg.tool_calls
                ]
            result.append(entry)
        elif isinstance(msg, ToolMessage):
            content = msg.content
            if not isinstance(content, str):
                content = json.dumps(content)
            result.append({"tool_results": [{"tool_use_id": msg.tool_call_id, "content": content}]})

    return result, system_prompt


def _langchain_tool_to_gateway(tool: dict[str, Any]) -> dict[str, Any]:
    """Convert a LangChain-format tool dict to gateway format."""
    if "function" in tool:
        # OpenAI-style tool format
        fn = tool["function"]
        return {
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {}),
        }
    return {
        "name": tool.get("name", ""),
        "description": tool.get("description", ""),
        "input_schema": tool.get("input_schema", tool.get("parameters", {})),
    }


def _parse_response(result: dict[str, Any]) -> ChatResult:
    """Convert a gateway completion response to a LangChain ChatResult."""
    content = result.get("content", "")
    tool_calls_raw = result.get("tool_calls")

    tool_calls = []
    if tool_calls_raw:
        for tc in tool_calls_raw:
            tool_calls.append(
                {
                    "id": tc["id"],
                    "name": tc["name"],
                    "args": tc.get("input", {}),
                }
            )

    msg = AIMessage(content=content, tool_calls=tool_calls)
    usage = result.get("usage", {})
    generation = ChatGeneration(
        message=msg,
        generation_info={
            "stop_reason": result.get("stop_reason"),
            "usage": usage,
        },
    )
    return ChatResult(
        generations=[generation],
        llm_output={"model": result.get("model", ""), "usage": usage},
    )


def _event_to_chunk(event: dict[str, Any]) -> ChatGenerationChunk | None:
    """Convert a gateway SSE event to a LangChain ChatGenerationChunk."""
    event_type = event.get("type")

    if event_type == "content_delta":
        return ChatGenerationChunk(
            message=AIMessageChunk(content=event.get("delta", "")),
        )

    if event_type == "tool_use_start":
        return ChatGenerationChunk(
            message=AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": event["name"],
                        "args": "",
                        "id": event.get("id", ""),
                        "index": 0,
                    }
                ],
            ),
        )

    if event_type == "tool_use_complete":
        tool_input = event.get("input", {})
        return ChatGenerationChunk(
            message=AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": event.get("name", ""),
                        "args": json.dumps(tool_input) if isinstance(tool_input, dict) else str(tool_input),
                        "id": event.get("id", ""),
                        "index": 0,
                    }
                ],
            ),
        )

    if event_type == "tool_use_delta":
        return ChatGenerationChunk(
            message=AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": "",
                        "args": event.get("delta", ""),
                        "id": "",
                        "index": 0,
                    }
                ],
            ),
        )

    if event_type == "message_stop":
        return ChatGenerationChunk(
            message=AIMessageChunk(content=""),
            generation_info={"stop_reason": event.get("stop_reason", "end_turn")},
        )

    return None
