"""Intent-level test: parent's provider overrides come back after sub-agent returns.

Contract (CLAUDE.md "Provider overrides in runner"): ``_apply_overrides``
and ``_restore_overrides`` save and restore the parent's provider
overrides around sub-agent execution so each agent uses its own
configured providers.

Observable breakage:
- Parent configured with ``provider_overrides: {memory: mem0}``
  delegates to child configured with ``{memory: in_memory}``.
- Child runs, reads/writes via ``in_memory``.
- Parent resumes on the next turn, reads the memory contextvar
  override — if restoration silently failed, parent would now be
  pointing at ``in_memory`` and its subsequent reads/writes would
  land in the wrong store.  Silent data corruption.

Existing tests (``test_runner.py``) exercise single-agent runs and
never verify the override contextvar state across a delegation
boundary.  This file verifies by capturing ``get_provider_override``
values at boundary transitions in a real parent→child→parent chain.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from agentic_primitives_gateway.agents.runner import AgentRunner
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import (
    get_provider_override,
    set_authenticated_principal,
    set_provider_overrides,
)
from agentic_primitives_gateway.models.agents import AgentSpec, HooksConfig, PrimitiveConfig

_RUNNER_MOD = "agentic_primitives_gateway.agents.runner"
_ALICE = AuthenticatedPrincipal(id="alice", type="user")


@pytest.fixture(autouse=True)
def _principal():
    set_authenticated_principal(_ALICE)
    set_provider_overrides({})
    yield
    set_authenticated_principal(None)  # type: ignore[arg-type]
    set_provider_overrides({})


class TestProviderOverrideRestoration:
    @pytest.mark.asyncio
    async def test_parent_overrides_restored_after_sub_agent(self):
        """Parent configured with memory=mem0, delegates to child
        configured with memory=in_memory.  Contract: after the child
        returns, ``get_provider_override('memory')`` reads ``mem0``
        again — not ``in_memory``, not None.

        The test watches the override value at three observable
        points:
        1. After parent's first LLM call (should see parent's mem0).
        2. Inside child's run (should see child's in_memory).
        3. After child returns, on parent's next LLM call (should
           see parent's mem0 again).
        """
        child_spec = AgentSpec(
            name="worker",
            model="m",
            system_prompt="x",
            provider_overrides={"memory": "in_memory"},
            primitives={"memory": PrimitiveConfig(enabled=True)},
            hooks=HooksConfig(auto_memory=False, auto_trace=False),
            max_turns=2,
        )
        parent_spec = AgentSpec(
            name="coordinator",
            model="m",
            system_prompt="x",
            provider_overrides={"memory": "mem0"},
            primitives={
                "memory": PrimitiveConfig(enabled=True),
                "agents": PrimitiveConfig(enabled=True, tools=["worker"]),
            },
            hooks=HooksConfig(auto_memory=False, auto_trace=False),
            max_turns=3,
        )

        async def _resolve_qualified(_owner: str, _name: str) -> AgentSpec | None:
            return child_spec if _name == "worker" else None

        store = AsyncMock()
        store.resolve_qualified = AsyncMock(side_effect=_resolve_qualified)

        # Record the memory override at each LLM call.  Each call
        # receives a running index so we can pair observations with
        # the run phase.
        observations: list[tuple[str, str | None]] = []

        async def _route_llm(request: dict[str, Any]) -> dict[str, Any]:
            override = get_provider_override("memory")
            # Determine the caller agent from the system prompt —
            # we only set unique prompts per spec above.
            system = request.get("system", "")
            observations.append((system, override))

            # Turn 1 of parent: delegate to worker.
            if len(observations) == 1:
                return {
                    "model": "m",
                    "content": "delegating",
                    "stop_reason": "tool_use",
                    "tool_calls": [{"id": "tc", "name": "call_worker", "input": {"message": "go"}}],
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            # Any subsequent call: end_turn (so child + parent both
            # terminate naturally).
            return {
                "model": "m",
                "content": "done",
                "stop_reason": "end_turn",
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
            await runner.run(parent_spec, message="go")

        # There should be at least 3 observations: parent turn 1,
        # child turn 1, parent turn 2 (after child returns).
        assert len(observations) >= 3, f"Expected >=3 LLM calls (parent, child, parent again); got {len(observations)}"

        # Distinguish observations by call order since both specs
        # use the same "x" system prompt: index 0 = parent's first
        # turn, index 1 = child's only turn (nested inside the
        # parent's turn-1 tool call), index 2 = parent's
        # continuation after the child returned.
        assert observations[0][1] == "mem0", (
            f"Parent turn 1: expected memory override 'mem0', got {observations[0][1]!r}.  "
            "Parent's provider_overrides didn't apply before the first LLM call."
        )
        assert observations[1][1] == "in_memory", (
            f"Child run: expected memory override 'in_memory', got {observations[1][1]!r}.  "
            "Child's provider_overrides didn't override parent's during delegation."
        )
        # Parent's turn 2 (index 2): override must be back to mem0.
        assert observations[2][1] == "mem0", (
            f"Parent turn 2 (after child returned): expected memory override "
            f"'mem0', got {observations[2][1]!r}.  The child's override leaked "
            "into the parent's continuation — restoration broken."
        )

    @pytest.mark.asyncio
    async def test_child_has_no_override_leaves_parent_intact(self):
        """Edge case: child has no provider_overrides.  Parent's
        overrides must persist across the delegation boundary and
        still be in effect when parent resumes.
        """
        child_spec = AgentSpec(
            name="worker",
            model="m",
            system_prompt="child-prompt",
            provider_overrides={},  # explicitly none
            primitives={"memory": PrimitiveConfig(enabled=True)},
            hooks=HooksConfig(auto_memory=False, auto_trace=False),
            max_turns=1,
        )
        parent_spec = AgentSpec(
            name="coordinator",
            model="m",
            system_prompt="parent-prompt",
            provider_overrides={"memory": "mem0"},
            primitives={
                "memory": PrimitiveConfig(enabled=True),
                "agents": PrimitiveConfig(enabled=True, tools=["worker"]),
            },
            hooks=HooksConfig(auto_memory=False, auto_trace=False),
            max_turns=3,
        )

        async def _resolve_qualified(_owner: str, _name: str) -> AgentSpec | None:
            return child_spec if _name == "worker" else None

        store = AsyncMock()
        store.resolve_qualified = AsyncMock(side_effect=_resolve_qualified)

        observations: list[tuple[str, str | None]] = []

        async def _route_llm(request: dict[str, Any]) -> dict[str, Any]:
            observations.append((request.get("system", ""), get_provider_override("memory")))
            if len(observations) == 1:
                return {
                    "model": "m",
                    "content": "d",
                    "stop_reason": "tool_use",
                    "tool_calls": [{"id": "tc", "name": "call_worker", "input": {"message": "go"}}],
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            return {
                "model": "m",
                "content": "done",
                "stop_reason": "end_turn",
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
            await runner.run(parent_spec, message="go")

        # Child had no override → it inherits parent's mem0 during
        # its run (since apply_provider_overrides merges over the
        # existing state when the child has no explicit override).
        # After child returns, parent still sees mem0.
        parent_calls = [o for o in observations if o[0] == "parent-prompt"]
        assert all(call[1] == "mem0" for call in parent_calls), (
            f"Parent's mem0 override not maintained across delegation: {parent_calls}"
        )
