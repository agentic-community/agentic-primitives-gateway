"""Intent-level test: cancelling a run actually halts tool execution.

Contract from ``CLAUDE.md`` (Cooperative cancellation):

    Team runs use ``asyncio.Event`` per run (``_cancel_events`` dict)
    checked at every turn boundary and before each tool execution in
    ``team_agent_loop.py``.  Cancel endpoints also soft-cancel via
    Redis: mark tasks as failed, delete checkpoint, set status to
    cancelled.

The existing cancellation tests (``test_team_remote_cancel.py``)
verify that setting the status flag in Redis propagates to the local
``asyncio.Event``.  Nothing asserts the end-to-end contract: when a
user clicks cancel mid-run, subsequent tool calls **actually do not
execute**.  If the cancel-check logic ever regresses (e.g. someone
removes the check before tool execution, or adds a new call site that
forgot to thread the event through), the existing tests still pass
because they only verify the event-signaling layer.

This file drives a full agent loop with a fake LLM that requests
multiple tool calls, sets the cancel event mid-loop, and asserts the
downstream tools observably never ran.  Side-effect tracking via a
counter on the tool handler is the observable: if the counter is
higher than expected, cancellation wasn't honored.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from agentic_primitives_gateway.agents.team_agent_loop import (
    run_agent_with_tools,
    run_agent_with_tools_stream,
)
from agentic_primitives_gateway.agents.tools.catalog import ToolDefinition
from agentic_primitives_gateway.models.agents import AgentSpec

_LLM_MOD = "agentic_primitives_gateway.agents.team_agent_loop.registry"


def _make_llm_responses(tool_call_batches: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Build an ordered list of LLM responses matching the batches.

    Each batch becomes a ``tool_use`` response; after all batches are
    consumed, return ``end_turn`` to terminate the loop naturally in
    the non-cancelled path.
    """
    responses = []
    for i, batch in enumerate(tool_call_batches):
        responses.append(
            {
                "content": f"turn-{i + 1}",
                "stop_reason": "tool_use",
                "tool_calls": batch,
            }
        )
    responses.append({"content": "done", "stop_reason": "end_turn", "tool_calls": []})
    return responses


def _make_stream_chunks(tool_call_batches: list[list[dict[str, Any]]]):
    """Build a list of streaming-chunk generators — one per turn.

    Matches the event shape that ``_process_stream_chunk`` expects:
    ``content_delta`` / ``tool_use_start`` / ``tool_use_complete`` /
    ``message_stop``.
    """

    async def _turn_stream(batch: list[dict[str, Any]], turn_idx: int):
        yield {"type": "content_delta", "delta": f"turn-{turn_idx + 1}"}
        for tc in batch:
            yield {"type": "tool_use_start", "id": tc["id"], "name": tc["name"]}
            yield {"type": "tool_use_complete", "input": tc.get("input", {})}
        yield {"type": "message_stop", "stop_reason": "tool_use"}

    async def _final_stream():
        yield {"type": "content_delta", "delta": "done"}
        yield {"type": "message_stop", "stop_reason": "end_turn"}

    return [_turn_stream(b, i) for i, b in enumerate(tool_call_batches)] + [_final_stream()]


@pytest.fixture
def tool_call_counter():
    return {"count": 0, "names": []}


@pytest.fixture
def canceller_tool(tool_call_counter):
    """Tool that records every invocation and sets the cancel event
    on the first call.  The test asserts that ``slow_tool`` is invoked
    zero additional times after ``canceller_tool`` triggers the cancel.
    """
    cancel_event = asyncio.Event()

    async def _handler(trigger: str = "") -> str:
        tool_call_counter["count"] += 1
        tool_call_counter["names"].append("canceller")
        cancel_event.set()
        return "cancelled"

    tool = ToolDefinition(
        name="canceller",
        description="Triggers cancellation",
        primitive="test",
        input_schema={"type": "object", "properties": {"trigger": {"type": "string"}}},
        handler=_handler,
    )
    return tool, cancel_event


@pytest.fixture
def tracking_slow_tool(tool_call_counter):
    """Tool that records its invocation in the shared counter so the
    test can assert it didn't run when cancellation was honored.
    """

    async def _handler(x: int = 0) -> str:
        tool_call_counter["count"] += 1
        tool_call_counter["names"].append(f"slow-{x}")
        await asyncio.sleep(0.01)
        return f"slow-{x}"

    return ToolDefinition(
        name="slow_tool",
        description="Slow tool",
        primitive="test",
        input_schema={"type": "object", "properties": {"x": {"type": "integer"}}},
        handler=_handler,
    )


@pytest.fixture
def spec():
    return AgentSpec(name="test-worker", model="test-model", system_prompt="x")


class TestRunAgentWithToolsRespectsCancellation:
    """The non-streaming ``run_agent_with_tools`` is used by planner,
    synthesizer, and the non-streaming worker path.  Before this
    commit it ignored cancellation entirely — none of those phases
    could be cancelled.
    """

    @pytest.mark.asyncio
    async def test_cancellation_between_turns_prevents_next_turn(
        self, spec, tool_call_counter, canceller_tool, tracking_slow_tool
    ):
        """The LLM would emit two turns of tool calls.  After turn 1
        (which invokes ``canceller`` and sets the event), turn 2 must
        not execute — so ``slow-0`` / ``slow-1`` from turn 2 must
        never appear in the call log.
        """
        cancel_tool, cancel_event = canceller_tool

        responses = _make_llm_responses(
            [
                [{"id": "c1", "name": "canceller", "input": {}}],  # turn 1
                [
                    {"id": "s1", "name": "slow_tool", "input": {"x": 0}},
                    {"id": "s2", "name": "slow_tool", "input": {"x": 1}},
                ],  # turn 2 — must not run
            ]
        )

        with patch(f"{_LLM_MOD}.llm.route_request", side_effect=responses):
            await run_agent_with_tools(
                spec,
                "start",
                tools=[cancel_tool, tracking_slow_tool],
                max_turns=10,
                cancel_event=cancel_event,
            )

        assert tool_call_counter["count"] == 1, (
            f"Expected 1 tool call (canceller), got {tool_call_counter['count']}: "
            f"{tool_call_counter['names']}.  Cancellation between turns did not halt "
            "the loop — the contract is not delivered."
        )
        assert tool_call_counter["names"] == ["canceller"]

    @pytest.mark.asyncio
    async def test_cancellation_between_tools_skips_remaining_in_batch(
        self, spec, tool_call_counter, canceller_tool, tracking_slow_tool
    ):
        """Within a single turn, if the first tool triggers the
        cancel, the remaining tools in the same batch must not
        execute.  The contract says checks happen "before each tool
        execution".
        """
        cancel_tool, cancel_event = canceller_tool

        responses = _make_llm_responses(
            [
                [
                    {"id": "c1", "name": "canceller", "input": {}},  # triggers cancel
                    {"id": "s1", "name": "slow_tool", "input": {"x": 0}},  # must not run
                    {"id": "s2", "name": "slow_tool", "input": {"x": 1}},  # must not run
                ],
            ]
        )

        with patch(f"{_LLM_MOD}.llm.route_request", side_effect=responses):
            await run_agent_with_tools(
                spec,
                "start",
                tools=[cancel_tool, tracking_slow_tool],
                max_turns=10,
                cancel_event=cancel_event,
            )

        assert tool_call_counter["count"] == 1, (
            f"Expected 1 tool call (only the canceller), got "
            f"{tool_call_counter['count']}: {tool_call_counter['names']}.  "
            "Cancellation did not halt remaining tools in the same turn's batch."
        )

    @pytest.mark.asyncio
    async def test_cancellation_before_first_turn_prevents_all_work(self, spec, tool_call_counter, tracking_slow_tool):
        """If cancel is already set when the loop starts, no LLM call
        and no tool call should happen.
        """
        cancel_event = asyncio.Event()
        cancel_event.set()

        llm_calls = {"count": 0}

        async def _count(_request):
            llm_calls["count"] += 1
            return {"content": "x", "stop_reason": "end_turn", "tool_calls": []}

        with patch(f"{_LLM_MOD}.llm.route_request", side_effect=_count):
            await run_agent_with_tools(
                spec,
                "start",
                tools=[tracking_slow_tool],
                max_turns=10,
                cancel_event=cancel_event,
            )

        assert llm_calls["count"] == 0, (
            "Cancellation set before the loop started did not prevent the "
            "first LLM call.  Turn-boundary check is not running."
        )
        assert tool_call_counter["count"] == 0


class TestRunAgentWithToolsStreamRespectsCancellation:
    """Streaming path: used by worker execution in the default flow.
    The stream generator must stop emitting events after cancel.
    """

    @pytest.mark.asyncio
    async def test_cancellation_between_tools_stops_stream(
        self, spec, tool_call_counter, canceller_tool, tracking_slow_tool
    ):
        cancel_tool, cancel_event = canceller_tool

        stream_sequence = _make_stream_chunks(
            [
                [
                    {"id": "c1", "name": "canceller", "input": {}},
                    {"id": "s1", "name": "slow_tool", "input": {"x": 0}},
                    {"id": "s2", "name": "slow_tool", "input": {"x": 1}},
                ],
            ]
        )
        # Each call to ``route_request_stream`` returns the next
        # turn's async generator.
        seq_iter = iter(stream_sequence)

        def _next_stream(_req):
            return next(seq_iter)

        with patch(f"{_LLM_MOD}.llm.route_request_stream", side_effect=_next_stream):
            events = []
            async for event in run_agent_with_tools_stream(
                spec,
                "start",
                tools=[cancel_tool, tracking_slow_tool],
                role_label="worker",
                max_turns=10,
                cancel_event=cancel_event,
            ):
                events.append(event)

        assert tool_call_counter["count"] == 1, (
            f"Streaming path did not halt — got {tool_call_counter['count']} calls: {tool_call_counter['names']}"
        )


class TestRedisCancelSignalPropagates:
    """The Redis-side cancel signal (``checkpoint_store.is_cancelled``)
    is how cross-replica cancellation actually reaches a running loop.
    This test fakes a checkpoint store that returns True after the
    first tool to simulate another replica marking the run cancelled.
    """

    @pytest.mark.asyncio
    async def test_run_id_cancelled_in_redis_halts_loop(self, spec, tool_call_counter, tracking_slow_tool):
        """A checkpoint store that becomes cancelled between turns
        must halt the loop before the next turn's work starts.

        The fake store returns False until after turn 1 completes,
        then True.  Contract: turn 1's tool runs, turn 2's tool does
        not.
        """
        turn_counter = {"count": 0}

        class _FakeCheckpointStore:
            async def is_cancelled(self, run_id: str) -> bool:
                # Cancel only after the first full turn (one pre-turn
                # check + one pre-tool check).  Past that point,
                # return True to cancel the run.
                turn_counter["count"] += 1
                return turn_counter["count"] > 2

        responses = _make_llm_responses(
            [
                [{"id": "s1", "name": "slow_tool", "input": {"x": 0}}],  # turn 1 — runs
                [{"id": "s2", "name": "slow_tool", "input": {"x": 1}}],  # turn 2 — must not run
            ]
        )

        with patch(f"{_LLM_MOD}.llm.route_request", side_effect=responses):
            await run_agent_with_tools(
                spec,
                "start",
                tools=[tracking_slow_tool],
                max_turns=10,
                checkpoint_store=_FakeCheckpointStore(),  # type: ignore[arg-type]
                run_id="team-run-abc",
            )

        assert tool_call_counter["names"] == ["slow-0"], (
            f"Redis cancel signal didn't halt loop after turn 1.  Tools seen: "
            f"{tool_call_counter['names']}.  Expected slow-0 to run (turn 1 "
            "started before cancel), but slow-1 (turn 2) must not run."
        )
