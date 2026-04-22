"""Intent-level test: max_turns stops the loop at exactly N turns.

Contract: ``AgentSpec.max_turns`` is the safety cap on the tool-call
loop.  An agent stuck in a runaway tool-call pattern (LLM always
returns ``tool_use``) must halt after exactly ``max_turns`` LLM
calls — no fewer, no more.  No fewer: the cap is the *max*, not the
*expected*.  No more: exceeding it defeats the protection and
risks unbounded resource consumption and API cost.

Existing tests cover a single value (max_turns=2, max_turns=1) and
verify ``turns_used == N``.  This file verifies exactness across
a range of values and against both ``run`` (non-streaming) and
``run_stream``.  A regression introducing ``<=`` instead of ``<``
(or a stray ``+ 1``) would slip past the existing tests because
off-by-one at N=2 looks identical to N=3 output.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from agentic_primitives_gateway.agents.runner import AgentRunner
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


def _infinite_tool_use_response() -> dict[str, Any]:
    """An LLM response that always asks for another tool call — no
    matter how many turns pass, it never produces end_turn.
    """
    return {
        "model": "m",
        "content": "let me keep going",
        "stop_reason": "tool_use",
        "tool_calls": [{"id": "tc", "name": "remember", "input": {"key": "k", "content": "c"}}],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _spec(max_turns: int) -> AgentSpec:
    return AgentSpec(
        name="loopy-agent",
        model="m",
        system_prompt="x",
        primitives={"memory": PrimitiveConfig(enabled=True)},
        hooks=HooksConfig(auto_memory=False, auto_trace=False),
        max_turns=max_turns,
    )


class TestMaxTurnsExactness:
    """The cap is a hard exact limit, not a suggestion."""

    @pytest.mark.parametrize("cap", [1, 2, 3, 5, 10])
    @pytest.mark.asyncio
    async def test_run_stops_at_exactly_max_turns(self, cap: int):
        """For each ``cap``, the LLM is called exactly ``cap`` times
        (never cap-1, never cap+1) when the model never returns
        end_turn.
        """
        llm = AsyncMock()
        llm.route_request.return_value = _infinite_tool_use_response()

        runner = AgentRunner()
        spec = _spec(max_turns=cap)

        with (
            patch(f"{_RUNNER_MOD}.registry") as reg,
            patch(f"{_RUNNER_MOD}.execute_tool", new_callable=AsyncMock) as exec_tool,
        ):
            reg.llm = llm
            reg.memory = AsyncMock()
            reg.memory.list_memories.return_value = []
            reg.observability = AsyncMock()
            exec_tool.return_value = "tool result"
            result = await runner.run(spec, message="go")

        assert result.turns_used == cap, (
            f"max_turns={cap}: expected exactly {cap} turns, got {result.turns_used}.  "
            f"Off-by-one in the loop guard would show up here."
        )
        # The LLM was called exactly ``cap`` times — confirms the
        # loop ran the expected number of iterations rather than
        # using a batched short-cut.
        assert llm.route_request.call_count == cap, (
            f"Expected LLM called {cap} times, got {llm.route_request.call_count}"
        )

    @pytest.mark.asyncio
    async def test_run_stream_stops_at_exactly_max_turns(self):
        """Streaming path must honor max_turns the same way.  A
        regression where the streaming loop used ``<=`` or had a
        stray pre-increment would leak here.
        """
        cap = 3

        async def _stream(_req: dict[str, Any]):
            # Minimal provider shape: ``tool_use_start`` carries id+name;
            # ``tool_use_complete`` carries only input.  The runner
            # remembers id/name from the start event.  (This also
            # guards against a regression where the streaming path
            # required name on the complete event — a latent crash
            # for any provider that omitted it.)
            yield {"type": "tool_use_start", "id": "tc", "name": "remember"}
            yield {"type": "tool_use_complete", "input": {"key": "k", "content": "c"}}
            yield {"type": "message_stop", "stop_reason": "tool_use"}

        llm = AsyncMock()
        call_count = {"n": 0}

        def _route(_req):
            call_count["n"] += 1
            return _stream(_req)

        llm.route_request_stream = _route
        runner = AgentRunner()
        spec = _spec(max_turns=cap)

        events: list[dict[str, Any]] = []
        with (
            patch(f"{_RUNNER_MOD}.registry") as reg,
            patch(f"{_RUNNER_MOD}.execute_tool", new_callable=AsyncMock) as exec_tool,
        ):
            reg.llm = llm
            reg.memory = AsyncMock()
            reg.memory.list_memories.return_value = []
            reg.observability = AsyncMock()
            exec_tool.return_value = "tool result"
            async for e in runner.run_stream(spec, message="go"):
                events.append(e)

        # The LLM stream was invoked exactly ``cap`` times.
        assert call_count["n"] == cap, f"Streaming run called LLM {call_count['n']} times, expected {cap}"
        # Terminal "done" event reports the cap turns.
        done = [e for e in events if e.get("type") == "done"]
        assert done, "Expected a done event at the end of the stream"
        assert done[-1].get("turns_used") == cap

    @pytest.mark.asyncio
    async def test_model_ending_early_before_cap_does_not_force_cap(self):
        """If the model returns end_turn before hitting max_turns,
        the loop exits normally.  Guards against a regression that
        blindly loops to max_turns regardless of stop_reason.
        """
        llm = AsyncMock()
        llm.route_request.return_value = {
            "model": "m",
            "content": "done early",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

        runner = AgentRunner()
        spec = _spec(max_turns=10)
        with patch(f"{_RUNNER_MOD}.registry") as reg:
            reg.llm = llm
            reg.memory = AsyncMock()
            reg.memory.list_memories.return_value = []
            reg.observability = AsyncMock()
            result = await runner.run(spec, message="go")

        assert result.turns_used == 1, (
            f"Model returned end_turn on turn 1; expected turns_used=1, got {result.turns_used}"
        )


class TestMaxTurnsMessageSurfaces:
    """When the cap is hit, the user-visible response indicates so.
    A regression that stopped silently with an empty response would
    be more confusing than helpful.
    """

    @pytest.mark.asyncio
    async def test_hitting_cap_adds_max_turns_indicator_to_response(self):
        llm = AsyncMock()
        llm.route_request.return_value = _infinite_tool_use_response()

        runner = AgentRunner()
        spec = _spec(max_turns=2)
        with (
            patch(f"{_RUNNER_MOD}.registry") as reg,
            patch(f"{_RUNNER_MOD}.execute_tool", new_callable=AsyncMock) as exec_tool,
        ):
            reg.llm = llm
            reg.memory = AsyncMock()
            reg.memory.list_memories.return_value = []
            reg.observability = AsyncMock()
            exec_tool.return_value = "r"
            result = await runner.run(spec, message="loop")

        # The response string carries a user-visible signal that the
        # cap was hit — helps users debug why their agent stopped.
        assert "maximum" in result.response.lower() or "max_turns" in result.response.lower(), (
            f"Response should indicate max_turns hit, got: {result.response!r}"
        )
