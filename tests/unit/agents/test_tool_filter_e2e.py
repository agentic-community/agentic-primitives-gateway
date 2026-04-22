"""Intent-level test: per-primitive tool filter actually limits the LLM's tool list.

Contract: in an agent spec, ``primitives.<name>.tools = ["t1", "t2"]``
restricts the tool list the LLM sees to exactly those names.  If the
filter silently passed through everything, two things break:

- Security posture — an agent scoped to only ``remember``/``recall``
  could still ``forget`` every memory, ``list_memories``, or invoke
  arbitrary shared-pool writes.
- LLM behavior — the model's tool choices drift because it sees
  tools the agent author said to hide.

Existing ``test_tool_filter_by_name`` (``test_tool_catalog.py:56-60``)
asserts the catalog-level filter at a *single* primitive with a
*single* allowed tool.  It doesn't cover:

- Multiple primitives in the same spec with different filter lists.
- ``tools=[]`` meaning "none of this primitive's tools" (vs. the
  Python-idiomatic interpretation of empty = no filter).
- The filter surviving all the way into ``AgentRunner.run()`` — i.e.,
  the LLM's ``request['tools']`` reflects the filter.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from agentic_primitives_gateway.agents.runner import AgentRunner
from agentic_primitives_gateway.agents.tools.catalog import build_tool_list
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import set_authenticated_principal
from agentic_primitives_gateway.models.agents import AgentSpec, HooksConfig, PrimitiveConfig

_RUNNER_MOD = "agentic_primitives_gateway.agents.runner"
_ALICE = AuthenticatedPrincipal(id="alice", type="user")


@pytest.fixture(autouse=True)
def _principal():
    set_authenticated_principal(_ALICE)
    yield
    set_authenticated_principal(None)  # type: ignore[arg-type]


class TestToolFilterCatalogLevel:
    """Filter applied at ``build_tool_list`` — the lowest layer."""

    def test_filter_excludes_unlisted_tools_from_same_primitive(self):
        tools = build_tool_list({"memory": PrimitiveConfig(enabled=True, tools=["remember"])})
        names = {t.name for t in tools}
        assert "remember" in names
        # Every other memory tool must be excluded.
        for excluded in ("recall", "search_memory", "forget", "list_memories"):
            assert excluded not in names, (
                f"Tool '{excluded}' leaked past the filter — agent was scoped to ['remember'] only"
            )

    def test_filter_allows_multiple_tools(self):
        tools = build_tool_list({"memory": PrimitiveConfig(enabled=True, tools=["remember", "recall"])})
        names = {t.name for t in tools}
        assert "remember" in names
        assert "recall" in names
        assert "search_memory" not in names

    def test_empty_filter_list_excludes_all_tools_from_that_primitive(self):
        """``tools=[]`` is explicit: no tools from this primitive.
        Distinct from ``tools=None`` which means "all tools".  A
        regression that treated empty as "all" would bypass the
        agent author's intent.
        """
        tools = build_tool_list({"memory": PrimitiveConfig(enabled=True, tools=[])})
        memory_tool_names = {t.name for t in tools if t.primitive in ("memory", "shared_memory")}
        assert memory_tool_names == set(), f"tools=[] should exclude all memory tools, but got {memory_tool_names}"

    def test_filter_on_one_primitive_does_not_affect_others(self):
        """Filtering memory must NOT exclude tools from, e.g., browser."""
        tools = build_tool_list(
            {
                "memory": PrimitiveConfig(enabled=True, tools=["remember"]),
                "browser": PrimitiveConfig(enabled=True),  # no filter
            }
        )
        names_by_primitive: dict[str, set[str]] = {}
        for t in tools:
            names_by_primitive.setdefault(t.primitive, set()).add(t.name)

        # Memory narrowed to exactly ``remember``.
        assert names_by_primitive.get("memory") == {"remember"}
        # Browser unaffected — must include its full tool set.
        browser = names_by_primitive.get("browser", set())
        assert "navigate" in browser
        assert "screenshot" in browser
        assert len(browser) >= 5, f"Browser primitive should have many tools; got {browser}"

    def test_filter_with_unknown_tool_name_silently_matches_nothing(self):
        """``tools=["nonexistent"]`` → no tools emerge.  (Not an error
        at build time — the agent just gets zero memory tools.)
        """
        tools = build_tool_list({"memory": PrimitiveConfig(enabled=True, tools=["nonexistent"])})
        assert [t.name for t in tools if t.primitive == "memory"] == []


class TestToolFilterReachesLLM:
    """End-to-end: the filter survives ``AgentRunner.run()`` and the
    LLM sees exactly the filtered tool list.
    """

    @pytest.mark.asyncio
    async def test_runner_sends_only_filtered_tools_to_llm(self):
        """Run an agent with ``memory: {tools: ["remember"]}`` →
        capture the LLM request → assert ``tools`` contains
        ``remember`` only, not the other memory tools.
        """
        captured: list[list[str]] = []

        async def _capture(request: dict[str, Any]) -> dict[str, Any]:
            tool_names = [t["name"] for t in request.get("tools", [])]
            captured.append(tool_names)
            return {
                "model": "m",
                "content": "done",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }

        llm = AsyncMock()
        llm.route_request.side_effect = _capture

        spec = AgentSpec(
            name="scoped-agent",
            model="m",
            system_prompt="x",
            primitives={"memory": PrimitiveConfig(enabled=True, tools=["remember"])},
            hooks=HooksConfig(auto_memory=False, auto_trace=False),
        )
        runner = AgentRunner()
        with patch(f"{_RUNNER_MOD}.registry") as reg:
            reg.llm = llm
            reg.memory = AsyncMock()
            reg.memory.list_memories.return_value = []
            reg.observability = AsyncMock()
            await runner.run(spec, message="hi")

        assert len(captured) == 1
        tool_names = captured[0]
        assert "remember" in tool_names, f"Expected 'remember' in LLM tools; got {tool_names}"
        # Every other memory tool must not be visible to the model.
        for excluded in ("recall", "search_memory", "forget", "list_memories"):
            assert excluded not in tool_names, (
                f"Tool '{excluded}' reached the LLM despite tools=['remember']. Full tool list: {tool_names}"
            )
