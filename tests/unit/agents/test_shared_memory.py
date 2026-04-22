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

    @pytest.fixture
    def _shared_ns(self):
        from agentic_primitives_gateway.primitives.memory.context import (
            reset_shared_memory_namespace,
            set_shared_memory_namespace,
        )

        token = set_shared_memory_namespace("team:test")
        try:
            yield "team:test"
        finally:
            reset_shared_memory_namespace(token)

    @pytest.mark.asyncio
    async def test_shared_memory_store(self, _shared_ns):
        mock_mem = AsyncMock()
        with patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_reg:
            mock_reg.memory = mock_mem
            result = await shared_memory_store("api_docs", "REST API with JSON")

        mock_mem.store.assert_called_once_with(
            namespace="team:test", key="api_docs", content="REST API with JSON", metadata={}
        )
        assert "Shared" in result
        assert "api_docs" in result

    @pytest.mark.asyncio
    async def test_shared_memory_retrieve_found(self, _shared_ns):
        mock_record = AsyncMock()
        mock_record.content = "REST API docs"
        mock_mem = AsyncMock()
        mock_mem.retrieve.return_value = mock_record
        with patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_reg:
            mock_reg.memory = mock_mem
            result = await shared_memory_retrieve("api_docs")

        assert result == "REST API docs"

    @pytest.mark.asyncio
    async def test_shared_memory_retrieve_not_found(self, _shared_ns):
        mock_mem = AsyncMock()
        mock_mem.retrieve.return_value = None
        with patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_reg:
            mock_reg.memory = mock_mem
            result = await shared_memory_retrieve("missing")

        assert "No shared finding found" in result

    @pytest.mark.asyncio
    async def test_shared_memory_search(self, _shared_ns):
        mock_result = AsyncMock()
        mock_result.score = 0.9
        mock_result.record.key = "finding1"
        mock_result.record.content = "Some content"
        mock_mem = AsyncMock()
        mock_mem.search.return_value = [mock_result]
        with patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_reg:
            mock_reg.memory = mock_mem
            result = await shared_memory_search("test query")

        assert "finding1" in result
        assert "Some content" in result

    @pytest.mark.asyncio
    async def test_shared_memory_search_empty(self, _shared_ns):
        mock_mem = AsyncMock()
        mock_mem.search.return_value = []
        with patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_reg:
            mock_reg.memory = mock_mem
            result = await shared_memory_search("nothing")

        assert "no shared findings" in result.lower()

    @pytest.mark.asyncio
    async def test_shared_memory_list(self, _shared_ns):
        mock_record = AsyncMock()
        mock_record.key = "fact1"
        mock_record.content = "Important fact"
        mock_mem = AsyncMock()
        mock_mem.list_memories.return_value = [mock_record]
        with patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_reg:
            mock_reg.memory = mock_mem
            result = await shared_memory_list()

        assert "fact1" in result


class TestBuildToolListWithSharedMemory:
    """Test that build_tool_list surfaces shared_memory tools when the primitive is enabled."""

    def test_shared_tools_built_when_primitive_enabled(self):
        primitives = {
            "memory": PrimitiveConfig(enabled=True),
            "shared_memory": PrimitiveConfig(enabled=True),
        }
        tools = build_tool_list(primitives)
        shared_tools = [t for t in tools if t.primitive == "shared_memory"]
        assert len(shared_tools) == 4
        # Handlers are plain (read shared_memory_namespace from a contextvar
        # the runner installs before the tool loop); no partial-binding.
        for t in shared_tools:
            assert not isinstance(t.handler, partial)

    def test_shared_tools_not_added_when_primitive_missing(self):
        primitives = {"memory": PrimitiveConfig(enabled=True)}
        tools = build_tool_list(primitives)
        shared_tools = [t for t in tools if t.primitive == "shared_memory"]
        assert len(shared_tools) == 0


class TestTeamRunnerSharedNamespace:
    """Test the team runner's shared namespace resolution."""

    def test_resolve_shared_namespace(self):
        from agentic_primitives_gateway.agents.team_runner import TeamRunner

        runner = TeamRunner()
        spec = TeamSpec(
            name="research-team",
            planner="p",
            synthesizer="s",
            workers=["w"],
            shared_memory_namespace="team:{team_name}",
        )
        ns = runner._resolve_shared_namespace(spec)
        # Team shared namespace is cross-user (no ``:u:`` suffix); the whole
        # point of team shared memory is that workers collaborate.
        assert ns == "team:research-team"

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
        tools = runner._build_worker_tools(worker_spec, team_spec)
        tool_names = {t.name for t in tools}
        # Should have both private memory and shared memory tools
        assert "remember" in tool_names
        assert "share_finding" in tool_names
        assert "search_shared" in tool_names

    def test_build_worker_tools_no_shared_without_config(self):
        from agentic_primitives_gateway.agents.team_runner import TeamRunner

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
        tools = runner._build_worker_tools(worker_spec, team_spec)
        tool_names = {t.name for t in tools}
        assert "remember" in tool_names
        assert "share_finding" not in tool_names


class TestPlannerPromptSharedMemory:
    """Test that the planner prompt mentions shared memory when configured."""

    @pytest.mark.asyncio
    async def test_planner_prompt_includes_shared_hint(self):
        from agentic_primitives_gateway.agents.team_prompts import build_planner_prompt

        mock_store = AsyncMock()
        mock_store.resolve_qualified.return_value = AgentSpec(name="researcher", model="m", description="A researcher")
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
        mock_store.resolve_qualified.return_value = AgentSpec(name="researcher", model="m", description="A researcher")
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
        pools = resolve_shared_pools(spec)
        assert pools is not None
        # Pools are cross-user by design — no ``:u:`` suffix.
        assert pools["project:alpha"] == "project:alpha"
        assert pools["team:research"] == "team:research"

    def test_resolve_pools_none_without_config(self):
        from agentic_primitives_gateway.agents.namespace import resolve_shared_pools

        spec = AgentSpec(name="agent", model="m")
        assert resolve_shared_pools(spec) is None

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
        pools = resolve_shared_pools(spec)
        assert pools is not None
        # Pools are cross-user; ``:u:`` suffix no longer applied.
        assert pools["project:{agent_name}:shared"] == "project:researcher:shared"

    @pytest.mark.asyncio
    async def test_two_users_see_same_pool_data(self):
        """Regression guard: a shared pool must genuinely be shared.

        Previously ``resolve_shared_pools`` silently appended
        ``:u:{principal.id}``, which meant Alice and Bob writing to the
        same *declared* pool landed in different namespaces and never
        saw each other's entries.  That defeated the feature.  This
        test simulates two separate runs against the same agent spec
        and the same ``InMemoryProvider`` and asserts Bob can read
        what Alice wrote.
        """
        from agentic_primitives_gateway.agents.namespace import resolve_shared_pools
        from agentic_primitives_gateway.agents.tools.handlers import (
            pool_memory_retrieve,
            pool_memory_store,
        )
        from agentic_primitives_gateway.primitives.memory.context import (
            reset_memory_pools,
            set_memory_pools,
        )
        from agentic_primitives_gateway.primitives.memory.in_memory import InMemoryProvider

        bob = AuthenticatedPrincipal(id="bob", type="user", groups=frozenset(), scopes=frozenset())

        spec = AgentSpec(
            name="researcher",
            model="m",
            primitives={
                "memory": PrimitiveConfig(
                    enabled=True,
                    shared_namespaces=["org:engineering-docs"],
                ),
            },
        )

        shared_backend = InMemoryProvider()

        # Alice's run: resolve pools for her principal, install the
        # contextvar, write through the handler.
        alice_pools = resolve_shared_pools(spec)
        assert alice_pools is not None
        token_a = set_memory_pools(alice_pools)
        try:
            with patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_reg:
                mock_reg.memory = shared_backend
                await pool_memory_store("org:engineering-docs", "api-spec", "v1.2")
        finally:
            reset_memory_pools(token_a)

        # Bob's run: independent principal, same spec, same backend.
        # He must see Alice's entry.
        bob_pools = resolve_shared_pools(spec)
        assert bob_pools is not None
        # Both resolve to the exact same namespace string — that's the
        # whole invariant this test is guarding.
        assert bob_pools == alice_pools
        token_b = set_memory_pools(bob_pools)
        try:
            with patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_reg:
                mock_reg.memory = shared_backend
                result = await pool_memory_retrieve("org:engineering-docs", "api-spec")
        finally:
            reset_memory_pools(token_b)

        assert result == "v1.2"
        # Sanity: Bob's principal is genuinely different; the contract
        # is that shared data crosses that boundary.
        assert bob.id != "alice"


class TestPoolMemoryHandlers:
    """Test pool-based shared memory handlers.

    Pools are resolved from the ``memory_pools`` contextvar that the
    runner sets at run start — handlers don't receive the pool map as
    a parameter.  These tests install the contextvar directly.
    """

    @pytest.mark.asyncio
    async def test_pool_store(self):
        from agentic_primitives_gateway.agents.tools.handlers import pool_memory_store
        from agentic_primitives_gateway.primitives.memory.context import (
            reset_memory_pools,
            set_memory_pools,
        )

        token = set_memory_pools({"project:alpha": "project:alpha"})
        try:
            mock_mem = AsyncMock()
            with patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_reg:
                mock_reg.memory = mock_mem
                result = await pool_memory_store("project:alpha", "docs", "API docs")
            mock_mem.store.assert_called_once_with(
                namespace="project:alpha", key="docs", content="API docs", metadata={}
            )
            assert "project:alpha" in result
        finally:
            reset_memory_pools(token)

    @pytest.mark.asyncio
    async def test_pool_store_invalid_pool(self):
        from agentic_primitives_gateway.agents.tools.handlers import pool_memory_store
        from agentic_primitives_gateway.primitives.memory.context import (
            reset_memory_pools,
            set_memory_pools,
        )

        token = set_memory_pools({"project:alpha": "project:alpha"})
        try:
            with pytest.raises(ValueError, match="Unknown pool"):
                await pool_memory_store("nonexistent", "key", "content")
        finally:
            reset_memory_pools(token)

    @pytest.mark.asyncio
    async def test_pool_search(self):
        from agentic_primitives_gateway.agents.tools.handlers import pool_memory_search
        from agentic_primitives_gateway.primitives.memory.context import (
            reset_memory_pools,
            set_memory_pools,
        )

        token = set_memory_pools({"project:alpha": "project:alpha"})
        try:
            mock_result = AsyncMock()
            mock_result.score = 0.9
            mock_result.record.key = "fact1"
            mock_result.record.content = "Important"
            mock_mem = AsyncMock()
            mock_mem.search.return_value = [mock_result]
            with patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_reg:
                mock_reg.memory = mock_mem
                result = await pool_memory_search("project:alpha", "test")
            assert "fact1" in result
        finally:
            reset_memory_pools(token)


class TestBuildToolListWithPools:
    """Test that build_tool_list injects pool tools when pool_names is set."""

    def test_pool_tools_injected(self):
        primitives = {"memory": PrimitiveConfig(enabled=True)}
        tools = build_tool_list(primitives, pool_names=["project:alpha", "team:research"])
        pool_tools = [t for t in tools if t.name in ("share_to", "read_from_pool", "search_pool", "list_pool")]
        assert len(pool_tools) == 4
        # Pool names should be in the description
        for t in pool_tools:
            assert "project:alpha" in t.description
            assert "team:research" in t.description

    def test_no_pool_tools_without_pools(self):
        primitives = {"memory": PrimitiveConfig(enabled=True)}
        tools = build_tool_list(primitives)
        pool_tools = [t for t in tools if t.name in ("share_to", "search_pool")]
        assert len(pool_tools) == 0

    def test_pool_tools_coexist_with_private_memory(self):
        primitives = {"memory": PrimitiveConfig(enabled=True)}
        tools = build_tool_list(primitives, pool_names=["project:alpha"])
        tool_names = {t.name for t in tools}
        # Private memory tools
        assert "remember" in tool_names
        assert "recall" in tool_names
        # Pool-based shared tools
        assert "share_to" in tool_names
        assert "search_pool" in tool_names
