"""Tests for build_tool_list, execute_tool, and delegation edge cases."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_primitives_gateway.agents.tools.catalog import (
    ToolDefinition,
    build_tool_list,
    execute_tool,
    to_llm_tools,
)
from agentic_primitives_gateway.agents.tools.delegation import MAX_AGENT_DEPTH, _build_agent_tools
from agentic_primitives_gateway.models.agents import PrimitiveConfig


class TestBuildToolList:
    def test_disabled_primitive_skipped(self) -> None:
        tools = build_tool_list({"memory": PrimitiveConfig(enabled=False)})
        assert tools == []

    def test_agents_primitive_no_store(self) -> None:
        tools = build_tool_list(
            {"agents": PrimitiveConfig(enabled=True, tools=["researcher"])},
            agent_store=None,
            agent_runner=None,
        )
        assert tools == []

    def test_agents_primitive_depth_exceeded(self) -> None:
        tools = build_tool_list(
            {"agents": PrimitiveConfig(enabled=True, tools=["researcher"])},
            agent_store=MagicMock(),
            agent_runner=MagicMock(),
            agent_depth=MAX_AGENT_DEPTH,
        )
        assert tools == []

    def test_agents_primitive_builds_tools(self) -> None:
        tools = build_tool_list(
            {"agents": PrimitiveConfig(enabled=True, tools=["researcher"])},
            agent_store=MagicMock(),
            agent_runner=MagicMock(),
            agent_depth=0,
        )
        assert any(t.name == "call_researcher" for t in tools)

    def test_memory_tools_built(self) -> None:
        tools = build_tool_list({"memory": PrimitiveConfig(enabled=True)})
        assert len(tools) > 0
        for t in tools:
            assert t.primitive == "memory"

    def test_tool_filter_by_name(self) -> None:
        tools = build_tool_list({"memory": PrimitiveConfig(enabled=True, tools=["remember"])})
        names = [t.name for t in tools]
        assert "remember" in names
        assert "search_memory" not in names

    def test_knowledge_tool_included_when_enabled(self) -> None:
        """``knowledge.enabled: true`` puts ``search_knowledge`` on the list.

        Knowledge follows the same gating rule as every other primitive:
        include if enabled on the spec.  The actual corpus namespace is
        resolved at run time by the runner and lives in a contextvar
        that the handler reads.
        """
        tools = build_tool_list({"knowledge": PrimitiveConfig(enabled=True)})
        assert [t.name for t in tools] == ["search_knowledge"]

    def test_knowledge_skipped_when_disabled(self) -> None:
        tools = build_tool_list({"knowledge": PrimitiveConfig(enabled=False)})
        assert tools == []


class TestExecuteTool:
    async def test_execute_known_tool(self) -> None:
        handler = AsyncMock(return_value="result")
        tools = [
            ToolDefinition(
                name="my_tool",
                description="test",
                primitive="test",
                # ``execute_tool`` filters ``tool_input`` against the
                # schema's properties to prevent LLM-driven kwarg
                # overrides of bound context (see security fix).
                input_schema={
                    "type": "object",
                    "properties": {"arg": {"type": "string"}},
                },
                handler=handler,
            )
        ]
        result = await execute_tool("my_tool", {"arg": "val"}, tools)
        assert result == "result"
        handler.assert_awaited_once_with(arg="val")

    async def test_execute_unknown_tool_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown tool"):
            await execute_tool("nonexistent", {}, [])


class TestToGatewayTools:
    def test_converts_to_dicts(self) -> None:
        tools = [
            ToolDefinition(
                name="t1",
                description="desc1",
                primitive="memory",
                input_schema={"type": "object"},
                handler=AsyncMock(),
            )
        ]
        result = to_llm_tools(tools)
        assert result == [{"name": "t1", "description": "desc1", "input_schema": {"type": "object"}}]


class TestBuildAgentTools:
    def test_builds_delegation_tools(self) -> None:
        config = PrimitiveConfig(enabled=True, tools=["researcher", "coder"])
        tools = _build_agent_tools(config, store=MagicMock(), runner=MagicMock(), depth=0)
        names = [t.name for t in tools]
        assert "call_researcher" in names
        assert "call_coder" in names

    def test_empty_tools_list(self) -> None:
        config = PrimitiveConfig(enabled=True, tools=[])
        tools = _build_agent_tools(config, store=MagicMock(), runner=MagicMock(), depth=0)
        assert tools == []

    async def test_delegation_handler_calls_runner(self) -> None:
        store = AsyncMock()
        spec = MagicMock()
        store.resolve_qualified.return_value = spec
        runner = AsyncMock()
        response = MagicMock()
        response.response = "answer"
        response.artifacts = []
        runner.run.return_value = response

        config = PrimitiveConfig(enabled=True, tools=["helper"])
        tools = _build_agent_tools(config, store=store, runner=runner, depth=0)
        result = await tools[0].handler(message="do something")
        assert result == "answer"

    async def test_delegation_handler_agent_not_found(self) -> None:
        store = AsyncMock()
        store.resolve_qualified.return_value = None
        runner = AsyncMock()

        config = PrimitiveConfig(enabled=True, tools=["missing"])
        tools = _build_agent_tools(config, store=store, runner=runner, depth=0)
        result = await tools[0].handler(message="hi")
        assert "not found" in result

    async def test_delegation_handler_error(self) -> None:
        store = AsyncMock()
        store.resolve_qualified.return_value = MagicMock()
        runner = AsyncMock()
        runner.run.side_effect = RuntimeError("boom")

        config = PrimitiveConfig(enabled=True, tools=["broken"])
        tools = _build_agent_tools(config, store=store, runner=runner, depth=0)
        result = await tools[0].handler(message="hi")
        assert "failed" in result
        assert "boom" in result

    async def test_delegation_handler_with_artifacts(self) -> None:
        store = AsyncMock()
        store.resolve_qualified.return_value = MagicMock()
        runner = AsyncMock()
        artifact = MagicMock()
        artifact.tool_name = "code_execute"
        artifact.tool_input = {"code": "print(1)"}
        artifact.output = "1"
        response = MagicMock()
        response.response = "done"
        response.artifacts = [artifact]
        runner.run.return_value = response

        config = PrimitiveConfig(enabled=True, tools=["coder"])
        tools = _build_agent_tools(config, store=store, runner=runner, depth=0)
        result = await tools[0].handler(message="write code")
        assert "code_execute" in result
        assert "print(1)" in result
