"""Intent-level test: replanner creates new tasks when instructed.

Contract (CLAUDE.md Teams Phase 2): between worker waves, a
re-planner evaluates completed results and creates follow-up tasks.
The loop terminates only when the replanner emits no new tasks.

Observable breakage:
- Replanner's LLM emits ``create_task`` → the new task should
  land on the board and the loop should continue.
- If ``create_task`` silently no-ops (e.g., the tool wasn't bound
  correctly to the planner role, or the contextvar chain broke),
  the replanner returns 0 every time and the loop terminates
  after wave 1.  Users see a team that never adapts to mid-run
  information.

Existing team_runner tests set the replanner to return "No new
tasks" — the no-op path.  Nothing verifies the positive path where
the replanner actually calls create_task and the team picks up the
new work.

This test wires a real InMemoryTasksProvider + fake LLM that
emits ``create_task`` on the replanner's first call, then end_turn.
Asserts that the board grows and the planner's follow-up count
reflects the addition.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from agentic_primitives_gateway.agents.file_store import FileAgentStore
from agentic_primitives_gateway.agents.team_runner import TeamRunner
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import set_authenticated_principal
from agentic_primitives_gateway.models.agents import AgentSpec, HooksConfig
from agentic_primitives_gateway.models.teams import TeamSpec
from agentic_primitives_gateway.primitives.tasks.in_memory import InMemoryTasksProvider
from agentic_primitives_gateway.registry import _PrimitiveProviders, registry

_ALICE = AuthenticatedPrincipal(id="alice", type="user")
_LOOP_MOD = "agentic_primitives_gateway.agents.team_agent_loop.registry"


@pytest.fixture(autouse=True)
def _principal():
    set_authenticated_principal(_ALICE)
    yield
    set_authenticated_principal(None)  # type: ignore[arg-type]


@pytest.fixture
def tasks_provider():
    p = InMemoryTasksProvider()
    original = registry._primitives.get("tasks")
    registry._primitives["tasks"] = _PrimitiveProviders(
        primitive="tasks", default_name="default", providers={"default": p}
    )
    yield p
    if original is not None:
        registry._primitives["tasks"] = original


class TestReplannerAddsTasks:
    @pytest.mark.asyncio
    async def test_replanner_create_task_tool_lands_on_board(self, tasks_provider: InMemoryTasksProvider, tmp_path):
        """Replanner LLM emits create_task → a new task lands on the
        InMemoryTasksProvider.  Returned count from ``_run_replanner``
        reflects the addition.
        """
        run_id = "run-1"

        # Seed a prior completed task so the replanner has something
        # to reason about.
        t1 = await tasks_provider.create_task(run_id, "Wave 1 task")
        await tasks_provider.claim_task(run_id, t1.id, "worker-1")
        await tasks_provider.update_task(run_id, t1.id, status="done", result="Found FastAPI is fastest")

        agent_store = FileAgentStore(path=str(tmp_path / "agents.json"))
        # Seed the planner agent.
        planner = AgentSpec(
            name="planner",
            model="m",
            system_prompt="You are a planner.",
            primitives={},
            hooks=HooksConfig(auto_memory=False, auto_trace=False),
            max_turns=3,
        )
        await agent_store.create(planner)

        team = TeamSpec(
            name="team-under-test",
            planner="planner",
            synthesizer="syn",  # not used in this test
            workers=["w"],
        )

        # Fake LLM: first call emits create_task, second call emits
        # end_turn.
        call_idx = {"n": 0}

        async def _route(_req: dict[str, Any]) -> dict[str, Any]:
            call_idx["n"] += 1
            if call_idx["n"] == 1:
                return {
                    "model": "m",
                    "content": "need a follow-up task",
                    "stop_reason": "tool_use",
                    "tool_calls": [
                        {
                            "id": "tc-1",
                            "name": "create_task",
                            "input": {
                                "title": "Wave 2 follow-up",
                                "description": "Benchmark FastAPI under load",
                            },
                        },
                    ],
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

        runner = TeamRunner()
        # _run_replanner only reads self._agent_store — minimal
        # install, skip team_store and agent_runner.
        runner._agent_store = agent_store

        with patch(f"{_LOOP_MOD}.llm.route_request", side_effect=llm.route_request.side_effect):
            new_count = await runner._run_replanner(team, run_id, "benchmark frameworks")

        assert new_count == 1, (
            f"Expected replanner to report 1 new task; got {new_count}.  "
            "create_task tool did not actually create a task on the board, "
            "or the count calculation is off."
        )

        # The new task must actually exist on the board.
        board = await tasks_provider.list_tasks(run_id)
        titles = [t.title for t in board]
        assert "Wave 2 follow-up" in titles, (
            f"Replanner's create_task tool didn't land the task on the board.  Board contents: {titles}"
        )

        # Sanity: original wave 1 task is still there.
        assert "Wave 1 task" in titles

    @pytest.mark.asyncio
    async def test_replanner_no_new_tasks_returns_zero(self, tasks_provider: InMemoryTasksProvider, tmp_path):
        """Inverse: replanner returns text without calling
        create_task → ``_run_replanner`` returns 0 (the termination
        signal for the team loop).
        """
        run_id = "run-none"
        t1 = await tasks_provider.create_task(run_id, "Prior")
        await tasks_provider.claim_task(run_id, t1.id, "w")
        await tasks_provider.update_task(run_id, t1.id, status="done", result="all set")

        agent_store = FileAgentStore(path=str(tmp_path / "agents.json"))
        await agent_store.create(
            AgentSpec(
                name="planner",
                model="m",
                system_prompt="x",
                primitives={},
                hooks=HooksConfig(auto_memory=False, auto_trace=False),
                max_turns=2,
            )
        )
        team = TeamSpec(name="t", planner="planner", synthesizer="s", workers=["w"])

        async def _route(_req):
            return {
                "model": "m",
                "content": "Nothing new",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }

        runner = TeamRunner()
        # _run_replanner only reads self._agent_store — minimal
        # install, skip team_store and agent_runner.
        runner._agent_store = agent_store

        with patch(f"{_LOOP_MOD}.llm.route_request", side_effect=_route):
            new_count = await runner._run_replanner(team, run_id, "do it")

        assert new_count == 0, f"Replanner without create_task should return 0; got {new_count}"
