"""Generic agent LLM tool-call loops for team execution.

These helpers run an agent's LLM loop with a given set of tools, handling
tool calls and message threading. Used by the team runner for planner,
worker, and synthesizer agents.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from agentic_primitives_gateway.agents.tools import ToolDefinition, execute_tool, to_gateway_tools
from agentic_primitives_gateway.models.agents import AgentSpec
from agentic_primitives_gateway.registry import registry

logger = logging.getLogger(__name__)


def _process_stream_chunk(
    chunk: dict[str, Any],
    tool_calls: list[dict[str, Any]],
    role_label: str,
) -> dict[str, Any] | None:
    """Process a single stream chunk, updating tool_calls in place.

    Returns an event dict to yield, or None to skip. Internal fields
    ``_content_delta`` and ``_stop_reason`` carry data back to the caller.
    """
    event_type = chunk.get("type", "")
    if event_type == "content_delta":
        delta = chunk.get("delta", "")
        return {"type": "agent_token", "agent": role_label, "content": delta, "_content_delta": delta}
    if event_type == "tool_use_start":
        tool_calls.append({"id": chunk["id"], "name": chunk["name"], "input": {}})
        return {"type": "agent_tool", "agent": role_label, "name": chunk["name"]}
    if event_type == "tool_use_complete":
        if tool_calls:
            tool_calls[-1]["input"] = chunk.get("input", {})
        return None
    if event_type == "message_stop":
        return {"_stop_reason": chunk.get("stop_reason", "end_turn")}
    return None


async def run_agent_with_tools(
    spec: AgentSpec,
    message: str,
    tools: list[ToolDefinition],
    max_turns: int = 20,
) -> str:
    """Run an agent's LLM loop with a specific set of tools.

    Returns the final text content from the agent.
    """
    gateway_tools = to_gateway_tools(tools) if tools else None
    messages: list[dict[str, Any]] = [{"role": "user", "content": message}]
    content = ""

    for _turn in range(max_turns):
        request_dict: dict[str, Any] = {
            "model": spec.model,
            "messages": messages,
            "system": spec.system_prompt,
            "temperature": spec.temperature,
        }
        if spec.max_tokens is not None:
            request_dict["max_tokens"] = spec.max_tokens
        if gateway_tools:
            request_dict["tools"] = gateway_tools

        response = await registry.gateway.route_request(request_dict)
        stop_reason = response.get("stop_reason", "end_turn")
        tool_calls = response.get("tool_calls")
        turn_content = response.get("content", "")
        if turn_content:
            content = turn_content

        logger.info(
            "Agent[%s] turn %d: stop=%s, tool_calls=%d, content_len=%d",
            spec.name,
            _turn + 1,
            stop_reason,
            len(tool_calls) if tool_calls else 0,
            len(turn_content),
        )

        if stop_reason != "tool_use" or not tool_calls:
            messages.append({"role": "assistant", "content": content})
            break

        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

        results = []
        for tc in tool_calls:
            logger.info("Agent[%s] tool call: %s(%s)", spec.name, tc["name"], str(tc.get("input", {}))[:200])
            try:
                result = await execute_tool(tc["name"], tc.get("input", {}), tools)
            except Exception as e:
                result = f"Error: {e}"
                logger.error("Agent[%s] tool error: %s — %s", spec.name, tc["name"], e)
            results.append({"tool_use_id": tc["id"], "content": result})
        messages.append({"role": "user", "tool_results": results})

    return content


async def run_agent_with_tools_stream(
    spec: AgentSpec,
    message: str,
    tools: list[ToolDefinition],
    role_label: str,
    max_turns: int = 20,
) -> AsyncIterator[dict[str, Any]]:
    """Streaming version of run_agent_with_tools.

    Yields SSE-friendly event dicts:
      - ``agent_token``: streamed text content
      - ``agent_tool``: tool call started
    """
    gateway_tools = to_gateway_tools(tools) if tools else None
    messages: list[dict[str, Any]] = [{"role": "user", "content": message}]
    content = ""

    for _turn in range(max_turns):
        request_dict: dict[str, Any] = {
            "model": spec.model,
            "messages": messages,
            "system": spec.system_prompt,
            "temperature": spec.temperature,
        }
        if spec.max_tokens is not None:
            request_dict["max_tokens"] = spec.max_tokens
        if gateway_tools:
            request_dict["tools"] = gateway_tools

        logger.info(
            "Agent[%s] stream turn %d: %d messages, %d tools",
            spec.name,
            _turn + 1,
            len(messages),
            len(gateway_tools) if gateway_tools else 0,
        )

        # Stream LLM response
        turn_content = ""
        tool_calls: list[dict[str, Any]] = []
        stop_reason = "end_turn"

        async for chunk in registry.gateway.route_request_stream(request_dict):
            parsed = _process_stream_chunk(chunk, tool_calls, role_label)
            if parsed is None:
                continue
            if "_content_delta" in parsed:
                turn_content += parsed.pop("_content_delta")
            if "_stop_reason" in parsed:
                stop_reason = parsed["_stop_reason"]
                continue
            if "type" in parsed:
                yield parsed

        if turn_content:
            content = turn_content

        logger.info(
            "Agent[%s] stream turn %d done: stop=%s, tool_calls=%d",
            spec.name,
            _turn + 1,
            stop_reason,
            len(tool_calls),
        )

        if stop_reason != "tool_use" or not tool_calls:
            messages.append({"role": "assistant", "content": content})
            break

        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

        results = []
        for tc in tool_calls:
            logger.info("Agent[%s] stream tool: %s(%s)", spec.name, tc["name"], str(tc.get("input", {}))[:200])
            try:
                result = await execute_tool(tc["name"], tc.get("input", {}), tools)
            except Exception as e:
                result = f"Error: {e}"
                logger.error("Agent[%s] stream tool error: %s — %s", spec.name, tc["name"], e)
            results.append({"tool_use_id": tc["id"], "content": result})
        messages.append({"role": "user", "tool_results": results})

    return
