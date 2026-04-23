"""Intent-level test: correlation_id threads across sub-agent runs.

Contract (CLAUDE.md Correlation ID): ``correlation_id`` is a
contextvar set from the ``x-correlation-id`` header (falling back to
``request_id``).  It's threaded across sub-agent + background runs
to stitch multi-step workflows together in audit + logs.  Without
this, a user looking at audit events for a multi-agent workflow
would see unrelated-looking trees — no way to correlate Alice's
parent run with the sub-agent runs it spawned.

Existing ``test_checkpoint_correlation.py`` tests that the checkpoint
serialize/restore round-trips the value.  Nothing tests the
end-to-end path: parent fires an audit event, delegates to a sub-
agent, sub-agent fires audit events — all three share the same
correlation_id.

A regression where the sub-agent runner re-initialized the
contextvar (e.g., accidentally called ``set_correlation_id("")``
at run start) would cause sub-agent events to lose the chain.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from agentic_primitives_gateway.agents.runner import AgentRunner
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import (
    get_correlation_id,
    set_authenticated_principal,
    set_correlation_id,
)
from agentic_primitives_gateway.models.agents import AgentSpec, HooksConfig, PrimitiveConfig

_RUNNER_MOD = "agentic_primitives_gateway.agents.runner"
_ALICE = AuthenticatedPrincipal(id="alice", type="user")


@pytest.fixture(autouse=True)
def _principal_and_corr():
    set_authenticated_principal(_ALICE)
    set_correlation_id("")
    yield
    set_authenticated_principal(None)  # type: ignore[arg-type]
    set_correlation_id("")


def _spec(name: str, sub: str | None = None, max_turns: int = 2) -> AgentSpec:
    primitives: dict[str, PrimitiveConfig] = {}
    if sub:
        primitives["agents"] = PrimitiveConfig(enabled=True, tools=[sub])
    return AgentSpec(
        name=name,
        model="m",
        system_prompt=f"I am {name}.",
        primitives=primitives,
        hooks=HooksConfig(auto_memory=False, auto_trace=False),
        max_turns=max_turns,
    )


class TestCorrelationIdThreading:
    @pytest.mark.asyncio
    async def test_correlation_id_propagates_into_sub_agent_run(self):
        """Parent is called with correlation_id=abc.  Parent
        delegates to child.  Every LLM call — parent's and child's —
        reads the same correlation_id inside the contextvar.
        """
        set_correlation_id("workflow-abc")

        parent = _spec("alpha", sub="beta")
        child = _spec("beta")

        async def _resolve_qualified(_owner: str, name: str) -> AgentSpec | None:
            return child if name == "beta" else None

        store = AsyncMock()
        store.resolve_qualified = AsyncMock(side_effect=_resolve_qualified)

        observed_corr: list[str] = []

        async def _route(_req: dict[str, Any]) -> dict[str, Any]:
            observed_corr.append(get_correlation_id())
            if len(observed_corr) == 1:
                return {
                    "model": "m",
                    "content": "delegating",
                    "stop_reason": "tool_use",
                    "tool_calls": [{"id": "tc", "name": "call_beta", "input": {"message": "hi"}}],
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            return {
                "model": "m",
                "content": "done",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }

        llm = AsyncMock()
        llm.route_request.side_effect = _route

        runner = AgentRunner()
        runner.set_store(store)
        with patch(f"{_RUNNER_MOD}.registry") as reg:
            reg.llm = llm
            reg.memory = AsyncMock()
            reg.memory.list_memories.return_value = []
            reg.observability = AsyncMock()
            await runner.run(parent, message="x")

        assert len(observed_corr) >= 2, f"Expected at least parent + child LLM calls; got {len(observed_corr)}"
        # Every observation must carry the workflow id.
        assert all(c == "workflow-abc" for c in observed_corr), (
            f"Correlation ID lost across sub-agent boundary.  Observed: {observed_corr}.  "
            "Sub-agent runner reset the contextvar or failed to inherit it."
        )

    @pytest.mark.asyncio
    async def test_correlation_id_restored_after_sub_agent(self):
        """After the sub-agent finishes, the parent's next turn still
        reads the same correlation_id.  (Belt-and-suspenders: even if
        sub-agent modified the contextvar, parent's continuation
        must see the original.)
        """
        set_correlation_id("parent-corr")

        parent = _spec("p", sub="c")
        child = _spec("c")

        async def _resolve_qualified(_owner: str, name: str) -> AgentSpec | None:
            return child if name == "c" else None

        store = AsyncMock()
        store.resolve_qualified = AsyncMock(side_effect=_resolve_qualified)

        observed: list[str] = []

        async def _route(_req: dict[str, Any]) -> dict[str, Any]:
            observed.append(get_correlation_id())
            # Turn 1 of parent: delegate.
            if len(observed) == 1:
                return {
                    "model": "m",
                    "content": "d",
                    "stop_reason": "tool_use",
                    "tool_calls": [{"id": "tc", "name": "call_c", "input": {"message": "x"}}],
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            return {
                "model": "m",
                "content": "done",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }

        llm = AsyncMock()
        llm.route_request.side_effect = _route
        runner = AgentRunner()
        runner.set_store(store)
        with patch(f"{_RUNNER_MOD}.registry") as reg:
            reg.llm = llm
            reg.memory = AsyncMock()
            reg.memory.list_memories.return_value = []
            reg.observability = AsyncMock()
            await runner.run(parent, message="x")

        # All observations (parent turn 1, child turn, parent turn 2)
        # must share the correlation id.
        assert observed and all(c == "parent-corr" for c in observed), (
            f"Correlation ID changed across delegation.  Observed: {observed}"
        )

        # After the full chain, the outer context still has it too.
        assert get_correlation_id() == "parent-corr"

    @pytest.mark.asyncio
    async def test_no_correlation_id_stays_unset_across_delegation(self):
        """If correlation_id isn't set at the top-level request,
        sub-agent runs also don't inherit a surprise value.  Guards
        against a regression that auto-generated an id inside the
        sub-agent path.
        """
        # Start with an explicitly empty correlation id.
        set_correlation_id("")

        parent = _spec("p", sub="c")
        child = _spec("c")

        async def _resolve_qualified(_owner: str, name: str) -> AgentSpec | None:
            return child if name == "c" else None

        store = AsyncMock()
        store.resolve_qualified = AsyncMock(side_effect=_resolve_qualified)

        observed: list[str] = []

        async def _route(_req: dict[str, Any]) -> dict[str, Any]:
            observed.append(get_correlation_id())
            if len(observed) == 1:
                return {
                    "model": "m",
                    "content": "d",
                    "stop_reason": "tool_use",
                    "tool_calls": [{"id": "tc", "name": "call_c", "input": {"message": "x"}}],
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            return {
                "model": "m",
                "content": "done",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }

        llm = AsyncMock()
        llm.route_request.side_effect = _route
        runner = AgentRunner()
        runner.set_store(store)
        with patch(f"{_RUNNER_MOD}.registry") as reg:
            reg.llm = llm
            reg.memory = AsyncMock()
            reg.memory.list_memories.return_value = []
            reg.observability = AsyncMock()
            await runner.run(parent, message="x")

        assert all(c == "" for c in observed), f"Correlation ID was auto-populated somewhere in the chain: {observed}"
