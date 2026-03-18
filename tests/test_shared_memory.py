"""Tests for team-scoped shared memory."""

from __future__ import annotations

from functools import partial
from unittest.mock import AsyncMock, patch

import pytest

from agentic_primitives_gateway.agents.tools.catalog import (
    _TOOL_CATALOG,
    build_tool_list,
)
from agentic_primitives_gateway.agents.tools.handlers import (
    shared_memory_list,
    shared_memory_retrieve,
    shared_memory_search,
    shared_memory_store,
)
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import set_authenticated_principal
from agentic_primitives_gateway.models.agents import AgentSpec, PrimitiveConfig
from agentic_primitives_gateway.models.teams import TeamSpec

_ALICE = AuthenticatedPrincipal(id="alice", type="user", groups=frozenset(), scopes=frozenset())


class TestSharedMemoryToolCatalog:
    """Verify shared_memory tools exist in the catalog."""

    def test_catalog_has_shared_memory(self):
        assert "shared_memory" in _TOOL_CATALOG

    def test_shared_memory_tools_count(self):
        tools = _TOOL_CATALOG["shared_memory"]
        assert len(tools) == 4

    def test_shared_memory_tool_names(self):
        names = {t.name for t in _TOOL_CATALOG["shared_memory"]}
        assert names == {"share_finding", "read_shared", "search_shared", "list_shared"}

    def test_shared_memory_tools_have_schemas(self):
        for tool in _TOOL_CATALOG["shared_memory"]:
            assert tool.input_schema is not None
            assert "type" in tool.input_schema


class TestSharedMemoryHandlers:
    """Test shared memory handler functions."""

    @pytest.mark.asyncio
    async def test_shared_memory_store(self):
        mock_mem = AsyncMock()
        with patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_reg:
            mock_reg.memory = mock_mem
            result = await shared_memory_store("team:test:u:alice", "api_docs", "REST API with JSON")

        mock_mem.store.assert_called_once_with(
            namespace="team:test:u:alice", key="api_docs", content="REST API with JSON", metadata={}
        )
        assert "Shared" in result
        assert "api_docs" in result

    @pytest.mark.asyncio
    async def test_shared_memory_retrieve_found(self):
        mock_record = AsyncMock()
        mock_record.content = "REST API docs"
        mock_mem = AsyncMock()
        mock_mem.retrieve.return_value = mock_record
        with patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_reg:
            mock_reg.memory = mock_mem
            result = await shared_memory_retrieve("team:test:u:alice", "api_docs")

        assert result == "REST API docs"

    @pytest.mark.asyncio
    async def test_shared_memory_retrieve_not_found(self):
        mock_mem = AsyncMock()
        mock_mem.retrieve.return_value = None
        with patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_reg:
            mock_reg.memory = mock_mem
            result = await shared_memory_retrieve("team:test:u:alice", "missing")

        assert "No shared finding found" in result

    @pytest.mark.asyncio
    async def test_shared_memory_search(self):
        mock_result = AsyncMock()
        mock_result.score = 0.9
        mock_result.record.key = "finding1"
        mock_result.record.content = "Some content"
        mock_mem = AsyncMock()
        mock_mem.search.return_value = [mock_result]
        with patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_reg:
            mock_reg.memory = mock_mem
            result = await shared_memory_search("team:test:u:alice", "test query")

        assert "finding1" in result
        assert "Some content" in result

    @pytest.mark.asyncio
    async def test_shared_memory_search_empty(self):
        mock_mem = AsyncMock()
        mock_mem.search.return_value = []
        with patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_reg:
            mock_reg.memory = mock_mem
            result = await shared_memory_search("team:test:u:alice", "nothing")

        assert "no shared findings" in result.lower()

    @pytest.mark.asyncio
    async def test_shared_memory_list(self):
        mock_record = AsyncMock()
        mock_record.key = "fact1"
        mock_record.content = "Important fact"
        mock_mem = AsyncMock()
        mock_mem.list_memories.return_value = [mock_record]
        with patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_reg:
            mock_reg.memory = mock_mem
            result = await shared_memory_list("team:test:u:alice")

        assert "fact1" in result


class TestBuildToolListWithSharedMemory:
    """Test that build_tool_list includes shared memory tools when shared_namespace is set."""

    def test_no_shared_tools_without_namespace(self):
        primitives = {
            "memory": PrimitiveConfig(enabled=True),
            "shared_memory": PrimitiveConfig(enabled=True),
        }
        tools = build_tool_list(primitives, namespace="agent:test")
        shared_tools = [t for t in tools if t.primitive == "shared_memory"]
        # Tools are created but handlers aren't bound (no shared_namespace)
        for t in shared_tools:
            # Handler should be the raw function (not partial) since no namespace was bound
            assert not isinstance(t.handler, partial)

    def test_shared_tools_bound_with_namespace(self):
        primitives = {
            "memory": PrimitiveConfig(enabled=True),
            "shared_memory": PrimitiveConfig(enabled=True),
        }
        tools = build_tool_list(primitives, namespace="agent:test", shared_namespace="team:research:u:alice")
        shared_tools = [t for t in tools if t.primitive == "shared_memory"]
        assert len(shared_tools) == 4
        # Handlers should be partial-bound with the shared namespace
        for t in shared_tools:
            assert isinstance(t.handler, partial)
            assert t.handler.keywords.get("shared_namespace") == "team:research:u:alice"

    def test_shared_tools_not_added_when_primitive_missing(self):
        primitives = {"memory": PrimitiveConfig(enabled=True)}
        tools = build_tool_list(primitives, namespace="agent:test", shared_namespace="team:research:u:alice")
        shared_tools = [t for t in tools if t.primitive == "shared_memory"]
        assert len(shared_tools) == 0


class TestTeamRunnerSharedNamespace:
    """Test the team runner's shared namespace resolution."""

    def test_resolve_shared_namespace(self):
        from agentic_primitives_gateway.agents.team_runner import TeamRunner

        set_authenticated_principal(_ALICE)
        runner = TeamRunner()
        spec = TeamSpec(
            name="research-team",
            planner="p",
            synthesizer="s",
            workers=["w"],
            shared_memory_namespace="team:{team_name}",
        )
        ns = runner._resolve_shared_namespace(spec)
        assert ns == "team:research-team:u:alice"

    def test_resolve_shared_namespace_none(self):
        from agentic_primitives_gateway.agents.team_runner import TeamRunner

        runner = TeamRunner()
        spec = TeamSpec(
            name="research-team",
            planner="p",
            synthesizer="s",
            workers=["w"],
        )
        assert runner._resolve_shared_namespace(spec) is None

    def test_build_worker_tools_includes_shared_memory(self):
        from agentic_primitives_gateway.agents.team_runner import TeamRunner

        set_authenticated_principal(_ALICE)
        runner = TeamRunner()
        worker_spec = AgentSpec(
            name="researcher",
            model="test-model",
            primitives={"memory": PrimitiveConfig(enabled=True)},
        )
        team_spec = TeamSpec(
            name="research-team",
            planner="p",
            synthesizer="s",
            workers=["researcher"],
            shared_memory_namespace="team:{team_name}",
        )
        tools = runner._build_worker_tools(worker_spec, team_spec, "run-1", {})
        tool_names = {t.name for t in tools}
        # Should have both private memory and shared memory tools
        assert "remember" in tool_names
        assert "share_finding" in tool_names
        assert "search_shared" in tool_names

    def test_build_worker_tools_no_shared_without_config(self):
        from agentic_primitives_gateway.agents.team_runner import TeamRunner

        set_authenticated_principal(_ALICE)
        runner = TeamRunner()
        worker_spec = AgentSpec(
            name="researcher",
            model="test-model",
            primitives={"memory": PrimitiveConfig(enabled=True)},
        )
        team_spec = TeamSpec(
            name="research-team",
            planner="p",
            synthesizer="s",
            workers=["researcher"],
            # No shared_memory_namespace
        )
        tools = runner._build_worker_tools(worker_spec, team_spec, "run-1", {})
        tool_names = {t.name for t in tools}
        assert "remember" in tool_names
        assert "share_finding" not in tool_names


class TestPlannerPromptSharedMemory:
    """Test that the planner prompt mentions shared memory when configured."""

    @pytest.mark.asyncio
    async def test_planner_prompt_includes_shared_hint(self):
        from agentic_primitives_gateway.agents.team_prompts import build_planner_prompt

        mock_store = AsyncMock()
        mock_store.get.return_value = AgentSpec(name="researcher", model="m", description="A researcher")
        spec = TeamSpec(
            name="team",
            planner="p",
            synthesizer="s",
            workers=["researcher"],
            shared_memory_namespace="team:{team_name}",
        )
        prompt = await build_planner_prompt(spec, "test message", mock_store)
        assert "SHARED MEMORY" in prompt
        assert "share_finding" in prompt

    @pytest.mark.asyncio
    async def test_planner_prompt_no_shared_hint_without_config(self):
        from agentic_primitives_gateway.agents.team_prompts import build_planner_prompt

        mock_store = AsyncMock()
        mock_store.get.return_value = AgentSpec(name="researcher", model="m", description="A researcher")
        spec = TeamSpec(
            name="team",
            planner="p",
            synthesizer="s",
            workers=["researcher"],
        )
        prompt = await build_planner_prompt(spec, "test message", mock_store)
        assert "SHARED MEMORY" not in prompt


# ── Level 2: Agent-level shared namespaces ───────────────────────────


class TestResolveSharedPools:
    """Test pool resolution from agent spec."""

    def test_resolve_pools(self):
        from agentic_primitives_gateway.agents.namespace import resolve_shared_pools

        set_authenticated_principal(_ALICE)
        spec = AgentSpec(
            name="researcher",
            model="m",
            primitives={
                "memory": PrimitiveConfig(
                    enabled=True,
                    shared_namespaces=["project:alpha", "team:research"],
                ),
            },
        )
        pools = resolve_shared_pools(spec, _ALICE)
        assert pools is not None
        assert pools["project:alpha"] == "project:alpha:u:alice"
        assert pools["team:research"] == "team:research:u:alice"

    def test_resolve_pools_none_without_config(self):
        from agentic_primitives_gateway.agents.namespace import resolve_shared_pools

        spec = AgentSpec(name="agent", model="m")
        assert resolve_shared_pools(spec, _ALICE) is None

    def test_resolve_pools_with_agent_name_placeholder(self):
        from agentic_primitives_gateway.agents.namespace import resolve_shared_pools

        spec = AgentSpec(
            name="researcher",
            model="m",
            primitives={
                "memory": PrimitiveConfig(
                    enabled=True,
                    shared_namespaces=["project:{agent_name}:shared"],
                ),
            },
        )
        pools = resolve_shared_pools(spec, _ALICE)
        assert pools is not None
        assert pools["project:{agent_name}:shared"] == "project:researcher:shared:u:alice"


class TestPoolMemoryHandlers:
    """Test pool-based shared memory handlers."""

    @pytest.mark.asyncio
    async def test_pool_store(self):
        from agentic_primitives_gateway.agents.tools.handlers import pool_memory_store

        pools = {"project:alpha": "project:alpha:u:alice"}
        mock_mem = AsyncMock()
        with patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_reg:
            mock_reg.memory = mock_mem
            result = await pool_memory_store(pools, "project:alpha", "docs", "API docs")

        mock_mem.store.assert_called_once_with(
            namespace="project:alpha:u:alice", key="docs", content="API docs", metadata={}
        )
        assert "project:alpha" in result

    @pytest.mark.asyncio
    async def test_pool_store_invalid_pool(self):
        from agentic_primitives_gateway.agents.tools.handlers import pool_memory_store

        pools = {"project:alpha": "project:alpha:u:alice"}
        with pytest.raises(ValueError, match="Unknown pool"):
            await pool_memory_store(pools, "nonexistent", "key", "content")

    @pytest.mark.asyncio
    async def test_pool_search(self):
        from agentic_primitives_gateway.agents.tools.handlers import pool_memory_search

        pools = {"project:alpha": "project:alpha:u:alice"}
        mock_result = AsyncMock()
        mock_result.score = 0.9
        mock_result.record.key = "fact1"
        mock_result.record.content = "Important"
        mock_mem = AsyncMock()
        mock_mem.search.return_value = [mock_result]
        with patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_reg:
            mock_reg.memory = mock_mem
            result = await pool_memory_search(pools, "project:alpha", "test")

        assert "fact1" in result


class TestBuildToolListWithPools:
    """Test that build_tool_list injects pool tools when resolved_pools is set."""

    def test_pool_tools_injected(self):
        primitives = {"memory": PrimitiveConfig(enabled=True)}
        pools = {"project:alpha": "project:alpha:u:alice", "team:research": "team:research:u:alice"}
        tools = build_tool_list(primitives, namespace="agent:test", resolved_pools=pools)
        pool_tools = [t for t in tools if t.name in ("share_to", "read_from_pool", "search_pool", "list_pool")]
        assert len(pool_tools) == 4
        # Pool names should be in the description
        for t in pool_tools:
            assert "project:alpha" in t.description
            assert "team:research" in t.description

    def test_no_pool_tools_without_pools(self):
        primitives = {"memory": PrimitiveConfig(enabled=True)}
        tools = build_tool_list(primitives, namespace="agent:test")
        pool_tools = [t for t in tools if t.name in ("share_to", "search_pool")]
        assert len(pool_tools) == 0

    def test_pool_tools_coexist_with_private_memory(self):
        primitives = {"memory": PrimitiveConfig(enabled=True)}
        pools = {"project:alpha": "ns:u:alice"}
        tools = build_tool_list(primitives, namespace="agent:test", resolved_pools=pools)
        tool_names = {t.name for t in tools}
        # Private memory tools
        assert "remember" in tool_names
        assert "recall" in tool_names
        # Pool-based shared tools
        assert "share_to" in tool_names
        assert "search_pool" in tool_names
