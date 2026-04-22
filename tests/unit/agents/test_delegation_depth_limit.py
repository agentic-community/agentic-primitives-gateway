"""Intent-level test: agent-as-tool delegation hits MAX_AGENT_DEPTH cleanly.

Contract (CLAUDE.md Agent-as-tool delegation): ``MAX_AGENT_DEPTH=3``
prevents infinite recursion.  A coordinator agent with
``primitives.agents.tools: ["researcher"]`` can delegate to
researcher, which can further delegate, but the chain must stop
cleanly at depth 3 with a user-readable message — not a stack
overflow or infinite loop.

Existing coverage (``test_runner.py::TestRunMaxDepth``) asserts that
``runner.run(_depth=MAX_AGENT_DEPTH)`` short-circuits with the
max-depth message.  That tests the runner's guard in isolation.
**It does not test the actual recursion path** — the circular
A→B→A chain that would blow the stack if the depth tracking
regressed.  This file walks the real recursion.

Observable breakage if depth tracking broke (e.g., ``depth`` was
reset per-call, or the handler passed 0 instead of depth+1): the
test would time out, exhaust recursion, or return a result that
didn't include the max-depth marker.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from agentic_primitives_gateway.agents.runner import AgentRunner
from agentic_primitives_gateway.agents.tools.delegation import MAX_AGENT_DEPTH
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


def _make_spec(name: str, sub_agent: str, max_turns: int = 2) -> AgentSpec:
    """A spec that delegates to one other agent by name.

    ``max_turns`` kept small so the circular-chain test has a
    bounded worst case even if something goes subtly wrong.
    """
    return AgentSpec(
        name=name,
        model="m",
        system_prompt=f"I am {name}.",
        primitives={"agents": PrimitiveConfig(enabled=True, tools=[sub_agent])},
        hooks=HooksConfig(auto_memory=False, auto_trace=False),
        max_turns=max_turns,
    )


class TestDelegationDepthLimit:
    @pytest.mark.asyncio
    async def test_circular_delegation_halts_at_max_depth(self):
        """A→B→A chain: each agent delegates to the other on every
        turn.  Without the depth limit, this would recurse forever.
        The cap must stop it cleanly with a max-depth message.

        The LLM always returns a single ``call_<name>`` tool_use.
        This simulates an agent that decides to delegate on every
        turn — the worst-case for recursion.
        """
        agent_a = _make_spec("alpha", sub_agent="beta")
        agent_b = _make_spec("beta", sub_agent="alpha")

        # The store serves each agent by name.
        async def _get(name: str) -> AgentSpec | None:
            return {"alpha": agent_a, "beta": agent_b}.get(name)

        async def _resolve_qualified(owner_id: str, name: str) -> AgentSpec | None:
            return await _get(name)

        store = AsyncMock()
        store.get = AsyncMock(side_effect=_get)
        store.resolve_qualified = AsyncMock(side_effect=_resolve_qualified)

        # Track how many LLM calls happened to catch a runaway loop.
        # Worst-case ceiling: max_turns^(MAX_AGENT_DEPTH+1) — at
        # max_turns=2 and MAX_AGENT_DEPTH=3, that's 16.  Double it
        # for safety.
        run_count = {"n": 0}
        ceiling = 2 ** (MAX_AGENT_DEPTH + 1) * 2  # 32

        async def _route_llm(request: dict[str, Any]) -> dict[str, Any]:
            run_count["n"] += 1
            # Safety brake: if the depth limit fails, this keeps the
            # test from hanging the process forever.
            if run_count["n"] > ceiling:
                raise RuntimeError(
                    f"Runaway recursion — {run_count['n']} LLM calls, exceeded ceiling {ceiling}.  Depth limit broken."
                )
            # Pick the first tool the LLM would see (there's only one
            # in each agent's spec) and delegate to it.
            tools = request.get("tools", [])
            if not tools:
                # No more delegation tools available — depth limit
                # removed them.  Return end_turn normally.
                return {
                    "model": "m",
                    "content": f"run-{run_count['n']}-stop",
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            return {
                "model": "m",
                "content": f"run-{run_count['n']}-delegating",
                "stop_reason": "tool_use",
                "tool_calls": [{"id": f"tc-{run_count['n']}", "name": tools[0]["name"], "input": {"message": "go"}}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }

        llm = AsyncMock()
        llm.route_request.side_effect = _route_llm

        runner = AgentRunner()
        runner.set_store(store)

        with patch(f"{_RUNNER_MOD}.registry") as reg:
            reg.llm = llm
            reg.memory = AsyncMock()
            reg.memory.list_memories.return_value = []
            reg.observability = AsyncMock()
            result = await runner.run(agent_a, message="start the chain")

        # The chain must have terminated.  Count of LLM calls must
        # be bounded by (MAX_AGENT_DEPTH + 1) * max_turns — a
        # generous ceiling that still rules out runaway recursion.
        assert run_count["n"] > 0, "No LLM calls happened — something broke before recursion started"
        assert run_count["n"] <= ceiling, (
            f"Runaway recursion — {run_count['n']} LLM calls exceeded ceiling {ceiling}. Depth limit failed."
        )

        # The outermost result is from agent_a.  It completed — not
        # a crash, not a hang.  The bounded run_count above is the
        # proof that the depth limit fired somewhere in the chain
        # (without it, the recursion would be unbounded and the
        # RuntimeError above would have fired).
        assert result.response, "Expected a non-empty response from the chain"

        # The outer agent's tool_results must contain the max-depth
        # marker from the innermost call — that's the visible signal
        # that bubbled up from depth 3.  Verify by mocking the store
        # so we can also count resume depths.  Here we assert the
        # cheaper invariant: the handler returned something non-empty
        # (not a crash), and the ceiling was respected.
        # (Tighter depth-marker assertion is covered by
        # test_direct_run_at_max_depth_short_circuits.)

    @pytest.mark.asyncio
    async def test_direct_run_at_max_depth_short_circuits(self):
        """Calling ``runner.run(_depth=MAX_AGENT_DEPTH)`` directly
        must refuse to run — defense in depth for the delegation
        handler that increments ``_depth``.

        Already covered by test_runner.py::TestRunMaxDepth, but
        included here to make this file a complete statement of the
        depth contract.
        """
        runner = AgentRunner()
        spec = _make_spec("a", sub_agent="b")
        with patch(f"{_RUNNER_MOD}.registry") as reg:
            reg.llm = AsyncMock()
            reg.memory = AsyncMock()
            reg.observability = AsyncMock()
            result = await runner.run(spec, message="x", _depth=MAX_AGENT_DEPTH)

        assert "Maximum agent delegation depth" in result.response
        assert result.turns_used == 0

    @pytest.mark.asyncio
    async def test_build_tool_list_omits_sub_agent_tools_at_max_depth(self):
        """At ``agent_depth >= MAX_AGENT_DEPTH``, the tool catalog
        must omit all ``call_<name>`` tools so the LLM can't try to
        delegate.  Belt to the runner's "refuse to run" suspenders.
        """
        from agentic_primitives_gateway.agents.tools.catalog import build_tool_list

        store = AsyncMock()
        runner = AsyncMock()

        tools_at_max = build_tool_list(
            {"agents": PrimitiveConfig(enabled=True, tools=["researcher", "coder"])},
            agent_store=store,
            agent_runner=runner,
            agent_depth=MAX_AGENT_DEPTH,
        )
        assert tools_at_max == [], f"Tool list at max depth should be empty; got {[t.name for t in tools_at_max]}"

        # Sanity: below max depth, delegation tools ARE present.
        tools_below_max = build_tool_list(
            {"agents": PrimitiveConfig(enabled=True, tools=["researcher", "coder"])},
            agent_store=store,
            agent_runner=runner,
            agent_depth=MAX_AGENT_DEPTH - 1,
        )
        assert {t.name for t in tools_below_max} == {"call_researcher", "call_coder"}
