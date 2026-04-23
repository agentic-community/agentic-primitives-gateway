"""Tests for agent-as-tool delegation and depth limiting."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from agentic_primitives_gateway.agents.file_store import FileAgentStore
from agentic_primitives_gateway.agents.runner import AgentRunner
from agentic_primitives_gateway.agents.tools import (
    MAX_AGENT_DEPTH,
    _build_agent_tools,
    build_tool_list,
)
from agentic_primitives_gateway.models.agents import AgentSpec, ChatResponse, PrimitiveConfig


@pytest.fixture()
def agent_store(tmp_path: Any) -> FileAgentStore:
    store = FileAgentStore(path=str(tmp_path / "agents.json"))
    return store


@pytest.fixture()
def runner(agent_store: FileAgentStore) -> AgentRunner:
    r = AgentRunner()
    r.set_store(agent_store)
    return r


class TestBuildAgentTools:
    def test_builds_tools_for_listed_agents(self, agent_store: FileAgentStore, runner: AgentRunner) -> None:
        config = PrimitiveConfig(enabled=True, tools=["researcher", "coder"])
        tools = _build_agent_tools(config, agent_store, runner, depth=0)
        assert len(tools) == 2
        assert tools[0].name == "call_researcher"
        assert tools[1].name == "call_coder"
        assert all(t.primitive == "agents" for t in tools)

    def test_empty_tools_list(self, agent_store: FileAgentStore, runner: AgentRunner) -> None:
        config = PrimitiveConfig(enabled=True, tools=[])
        tools = _build_agent_tools(config, agent_store, runner, depth=0)
        assert tools == []

    def test_skipped_at_max_depth(self) -> None:
        primitives = {"agents": PrimitiveConfig(enabled=True, tools=["sub"])}
        tools = build_tool_list(
            primitives,
            agent_store=AsyncMock(),
            agent_runner=AsyncMock(),
            agent_depth=MAX_AGENT_DEPTH,
        )
        # No agent tools should be added at max depth
        assert all(t.primitive != "agents" for t in tools)

    def test_tool_schema(self, agent_store: FileAgentStore, runner: AgentRunner) -> None:
        config = PrimitiveConfig(enabled=True, tools=["helper"])
        tools = _build_agent_tools(config, agent_store, runner, depth=0)
        assert len(tools) == 1
        schema = tools[0].input_schema
        assert schema["type"] == "object"
        assert "message" in schema["properties"]
        assert schema["required"] == ["message"]


class TestAgentDelegationHandler:
    @pytest.mark.asyncio()
    async def test_agent_not_found(self, agent_store: FileAgentStore, runner: AgentRunner) -> None:
        config = PrimitiveConfig(enabled=True, tools=["nonexistent"])
        tools = _build_agent_tools(config, agent_store, runner, depth=0)
        result = await tools[0].handler(message="hello")
        assert "not found" in result

    @pytest.mark.asyncio()
    async def test_successful_delegation(self, agent_store: FileAgentStore, runner: AgentRunner) -> None:
        # Create a sub-agent in the store
        sub_spec = AgentSpec(name="helper", model="test-model", system_prompt="You help.")
        await agent_store.create(sub_spec)

        config = PrimitiveConfig(enabled=True, tools=["helper"])
        tools = _build_agent_tools(config, agent_store, runner, depth=0)

        # Mock the runner.run to avoid actual LLM calls
        mock_response = ChatResponse(
            response="I helped!",
            session_id="test",
            agent_name="helper",
            turns_used=1,
            tools_called=[],
        )
        with patch.object(runner, "run", return_value=mock_response):
            result = await tools[0].handler(message="help me")
        assert "I helped!" in result

    @pytest.mark.asyncio()
    async def test_delegation_includes_artifacts(self, agent_store: FileAgentStore, runner: AgentRunner) -> None:
        from agentic_primitives_gateway.models.agents import ToolArtifact

        sub_spec = AgentSpec(name="coder", model="test-model")
        await agent_store.create(sub_spec)

        config = PrimitiveConfig(enabled=True, tools=["coder"])
        tools = _build_agent_tools(config, agent_store, runner, depth=0)

        mock_response = ChatResponse(
            response="Here's the code.",
            session_id="test",
            agent_name="coder",
            turns_used=2,
            tools_called=["execute_code"],
            artifacts=[
                ToolArtifact(
                    tool_name="execute_code",
                    tool_input={"code": "print('hello')", "language": "python"},
                    output='{"output": "hello"}',
                ),
            ],
        )
        with patch.object(runner, "run", return_value=mock_response):
            result = await tools[0].handler(message="write code")
        assert "Tool Artifacts" in result
        assert "print('hello')" in result
        assert "hello" in result


class TestMaxDepthEnforcement:
    @pytest.mark.asyncio()
    async def test_run_returns_early_at_max_depth(self, runner: AgentRunner) -> None:
        spec = AgentSpec(name="deep", model="test-model")
        response = await runner.run(spec, message="hello", _depth=MAX_AGENT_DEPTH)
        assert "Maximum agent delegation depth" in response.response
        assert response.turns_used == 0

    @pytest.mark.asyncio()
    async def test_run_stream_returns_early_at_max_depth(self, runner: AgentRunner) -> None:
        spec = AgentSpec(name="deep", model="test-model")
        events = []
        async for event in runner.run_stream(spec, message="hello", _depth=MAX_AGENT_DEPTH):
            events.append(event)
        assert any("Maximum agent delegation depth" in str(e) for e in events)
        done_events = [e for e in events if e.get("type") == "done"]
        assert len(done_events) == 1
        assert done_events[0]["turns_used"] == 0
