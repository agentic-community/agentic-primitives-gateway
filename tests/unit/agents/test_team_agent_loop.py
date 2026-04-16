"""Tests for team_agent_loop: _process_stream_chunk and run_agent_with_tools_stream."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_primitives_gateway.agents.team_agent_loop import (
    _process_stream_chunk,
    run_agent_with_tools_stream,
)
from agentic_primitives_gateway.models.agents import AgentSpec

# ── _process_stream_chunk ────────────────────────────────────────────


class TestProcessStreamChunk:
    def test_content_delta_includes_invocation_id(self) -> None:
        """content_delta events include the invocation_id."""
        tool_calls: list[dict[str, Any]] = []
        chunk = {"type": "content_delta", "delta": "hello"}

        result = _process_stream_chunk(chunk, tool_calls, "planner", "inv-123")

        assert result is not None
        assert result["type"] == "agent_token"
        assert result["agent"] == "planner"
        assert result["invocation_id"] == "inv-123"
        assert result["content"] == "hello"
        assert result["_content_delta"] == "hello"

    def test_tool_use_start_includes_invocation_id(self) -> None:
        """tool_use_start events include the invocation_id."""
        tool_calls: list[dict[str, Any]] = []
        chunk = {"type": "tool_use_start", "id": "tc-1", "name": "search"}

        result = _process_stream_chunk(chunk, tool_calls, "worker", "inv-456")

        assert result is not None
        assert result["type"] == "agent_tool"
        assert result["agent"] == "worker"
        assert result["invocation_id"] == "inv-456"
        assert result["name"] == "search"
        # tool_calls list should be updated
        assert len(tool_calls) == 1
        assert tool_calls[0]["id"] == "tc-1"

    def test_tool_use_complete_returns_none(self) -> None:
        """tool_use_complete updates tool_calls but returns None."""
        tool_calls: list[dict[str, Any]] = [{"id": "tc-1", "name": "search", "input": {}}]
        chunk = {"type": "tool_use_complete", "input": {"query": "test"}}

        result = _process_stream_chunk(chunk, tool_calls, "worker", "inv-1")

        assert result is None
        assert tool_calls[0]["input"] == {"query": "test"}

    def test_message_stop_returns_stop_reason(self) -> None:
        """message_stop returns a dict with _stop_reason."""
        tool_calls: list[dict[str, Any]] = []
        chunk = {"type": "message_stop", "stop_reason": "tool_use"}

        result = _process_stream_chunk(chunk, tool_calls, "planner", "inv-1")

        assert result is not None
        assert result["_stop_reason"] == "tool_use"

    def test_unknown_event_returns_none(self) -> None:
        """Unknown event types return None."""
        tool_calls: list[dict[str, Any]] = []
        chunk = {"type": "unknown_thing", "data": 42}

        result = _process_stream_chunk(chunk, tool_calls, "planner", "inv-1")

        assert result is None

    def test_message_stop_default_reason(self) -> None:
        """message_stop without stop_reason defaults to 'end_turn'."""
        result = _process_stream_chunk({"type": "message_stop"}, [], "planner", "inv-1")
        assert result is not None
        assert result["_stop_reason"] == "end_turn"

    def test_tool_use_complete_with_empty_tool_calls(self) -> None:
        """tool_use_complete with empty tool_calls doesn't crash."""
        tool_calls: list[dict[str, Any]] = []
        result = _process_stream_chunk(
            {"type": "tool_use_complete", "input": {"q": "x"}},
            tool_calls,
            "worker",
            "inv-1",
        )
        assert result is None


# ── run_agent_with_tools_stream ──────────────────────────────────────


class TestRunAgentWithToolsStream:
    @pytest.mark.asyncio
    async def test_emits_invocation_start_event(self) -> None:
        """The stream starts with an invocation_start event containing invocation_id."""
        spec = AgentSpec(name="test-agent", model="test-model")

        async def mock_stream(request: dict) -> Any:
            yield {"type": "content_delta", "delta": "hi"}
            yield {"type": "message_stop", "stop_reason": "end_turn"}

        with patch("agentic_primitives_gateway.agents.team_agent_loop.registry") as mock_reg:
            mock_reg.llm.route_request_stream = mock_stream
            events: list[dict[str, Any]] = []
            async for event in run_agent_with_tools_stream(spec, "hello", [], "planner"):
                events.append(event)

        assert len(events) >= 1
        assert events[0]["type"] == "invocation_start"
        assert events[0]["agent"] == "planner"
        assert "invocation_id" in events[0]
        invocation_id = events[0]["invocation_id"]
        assert len(invocation_id) == 12  # uuid hex[:12]

    @pytest.mark.asyncio
    async def test_all_events_share_invocation_id(self) -> None:
        """All agent_token events share the same invocation_id from invocation_start."""
        spec = AgentSpec(name="test-agent", model="test-model")

        async def mock_stream(request: dict) -> Any:
            yield {"type": "content_delta", "delta": "hello "}
            yield {"type": "content_delta", "delta": "world"}
            yield {"type": "message_stop", "stop_reason": "end_turn"}

        with patch("agentic_primitives_gateway.agents.team_agent_loop.registry") as mock_reg:
            mock_reg.llm.route_request_stream = mock_stream
            events: list[dict[str, Any]] = []
            async for event in run_agent_with_tools_stream(spec, "hello", [], "worker"):
                events.append(event)

        invocation_id = events[0]["invocation_id"]
        token_events = [e for e in events if e.get("type") == "agent_token"]
        assert len(token_events) == 2
        for te in token_events:
            assert te["invocation_id"] == invocation_id

    @pytest.mark.asyncio
    async def test_tool_events_include_invocation_id(self) -> None:
        """Tool call events also include the invocation_id."""
        spec = AgentSpec(name="test-agent", model="test-model")

        call_count = 0

        async def mock_stream(request: dict) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield {"type": "tool_use_start", "id": "tc-1", "name": "my_tool"}
                yield {"type": "tool_use_complete", "input": {"arg": "val"}}
                yield {"type": "message_stop", "stop_reason": "tool_use"}
            else:
                yield {"type": "content_delta", "delta": "done"}
                yield {"type": "message_stop", "stop_reason": "end_turn"}

        # Create a mock tool that the executor can find
        mock_tool = MagicMock()
        mock_tool.name = "my_tool"
        mock_tool.handler = AsyncMock(return_value="tool result")

        with (
            patch("agentic_primitives_gateway.agents.team_agent_loop.registry") as mock_reg,
            patch(
                "agentic_primitives_gateway.agents.team_agent_loop.execute_tool", new_callable=AsyncMock
            ) as mock_exec,
        ):
            mock_reg.llm.route_request_stream = mock_stream
            mock_exec.return_value = "tool result"

            events: list[dict[str, Any]] = []
            async for event in run_agent_with_tools_stream(spec, "use tool", [mock_tool], "worker"):
                events.append(event)

        invocation_id = events[0]["invocation_id"]
        tool_events = [e for e in events if e.get("type") == "agent_tool"]
        assert len(tool_events) == 1
        assert tool_events[0]["invocation_id"] == invocation_id
        assert tool_events[0]["name"] == "my_tool"

    @pytest.mark.asyncio
    async def test_cancel_event_stops_stream(self) -> None:
        """Setting cancel_event stops the stream before next turn."""
        import asyncio

        spec = AgentSpec(name="test-agent", model="test-model")
        cancel = asyncio.Event()

        call_count = 0

        async def mock_stream(request: dict) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield {"type": "tool_use_start", "id": "tc-1", "name": "my_tool"}
                yield {"type": "tool_use_complete", "input": {}}
                yield {"type": "message_stop", "stop_reason": "tool_use"}
            else:
                yield {"type": "content_delta", "delta": "should not see this"}
                yield {"type": "message_stop", "stop_reason": "end_turn"}

        with (
            patch("agentic_primitives_gateway.agents.team_agent_loop.registry") as mock_reg,
            patch(
                "agentic_primitives_gateway.agents.team_agent_loop.execute_tool", new_callable=AsyncMock
            ) as mock_exec,
        ):
            mock_reg.llm.route_request_stream = mock_stream
            mock_exec.return_value = "result"

            events: list[dict[str, Any]] = []

            async def exec_and_cancel(*args: Any, **kwargs: Any) -> str:
                cancel.set()
                return "result"

            mock_exec.side_effect = exec_and_cancel

            async for event in run_agent_with_tools_stream(spec, "go", [MagicMock()], "worker", cancel_event=cancel):
                events.append(event)

        # Should not have gotten to the second LLM call
        assert call_count == 1
        # No token events from the second call
        token_events = [e for e in events if e.get("type") == "agent_token"]
        assert len(token_events) == 0

    @pytest.mark.asyncio
    async def test_resume_hint_injected(self) -> None:
        """resume_hint is injected into the system prompt on the first turn."""
        spec = AgentSpec(name="test-agent", model="test-model", system_prompt="Be helpful.")
        captured_requests: list[dict[str, Any]] = []

        async def mock_stream(request: dict) -> Any:
            captured_requests.append(request)
            yield {"type": "content_delta", "delta": "ok"}
            yield {"type": "message_stop", "stop_reason": "end_turn"}

        with patch("agentic_primitives_gateway.agents.team_agent_loop.registry") as mock_reg:
            mock_reg.llm.route_request_stream = mock_stream
            async for _ in run_agent_with_tools_stream(spec, "continue", [], "worker", resume_hint="partial text"):
                pass

        assert len(captured_requests) == 1
        system = captured_requests[0]["system"]
        assert "Be helpful." in system
        assert "RESUME CONTEXT" in system
        assert "partial text" in system
