"""End-to-end tests for TeamRunner.

Uses the real InMemoryTasksProvider and mocks only the LLM responses
so the full plan → execute → synthesize cycle runs against a real task board.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_primitives_gateway.agents.runner import AgentRunner
from agentic_primitives_gateway.agents.store import FileAgentStore
from agentic_primitives_gateway.agents.team_runner import TeamRunner
from agentic_primitives_gateway.agents.team_store import FileTeamStore
from agentic_primitives_gateway.models.agents import AgentSpec, HooksConfig, PrimitiveConfig
from agentic_primitives_gateway.models.tasks import TaskStatus
from agentic_primitives_gateway.models.teams import TeamRunPhase, TeamSpec
from agentic_primitives_gateway.registry import registry

_GATEWAY_MOD = "agentic_primitives_gateway.agents.team_agent_loop.registry.llm"


# ── Helpers ──────────────────────────────────────────────────────────


def _make_agent(name: str, description: str = "") -> AgentSpec:
    return AgentSpec(
        name=name,
        model="test-model",
        description=description,
        system_prompt=f"You are {name}.",
        primitives={},
        hooks=HooksConfig(auto_memory=False, auto_trace=False),
        max_turns=10,
    )


def _make_worker(name: str, with_code_interpreter: bool = False) -> AgentSpec:
    prims: dict[str, PrimitiveConfig] = {}
    if with_code_interpreter:
        prims["code_interpreter"] = PrimitiveConfig(enabled=True)
    return AgentSpec(
        name=name,
        model="test-model",
        description=f"Worker {name}",
        system_prompt=f"You are worker {name}.",
        primitives=prims,
        hooks=HooksConfig(auto_memory=False, auto_trace=False),
        max_turns=10,
    )


def _make_team(workers: list[str] | None = None) -> TeamSpec:
    return TeamSpec(
        name="test-team",
        planner="planner",
        synthesizer="synthesizer",
        workers=workers or ["worker1"],
    )


def _tool_call(name: str, input_data: dict[str, Any]) -> dict[str, Any]:
    return {"id": f"tc-{uuid.uuid4().hex[:8]}", "name": name, "input": input_data}


def _planner_response_with_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build a sequence of LLM responses for a planner that creates tasks."""
    responses = []
    for task in tasks:
        # Turn 1..N: tool_use to create each task
        responses.append(
            {
                "stop_reason": "tool_use",
                "content": "",
                "tool_calls": [_tool_call("create_task", task)],
            }
        )
    # Final turn: end_turn
    responses.append(
        {
            "stop_reason": "end_turn",
            "content": "Planning complete.",
        }
    )
    return responses


def _text_response(text: str) -> dict[str, Any]:
    return {"stop_reason": "end_turn", "content": text, "tool_calls": None}


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _use_in_memory_tasks() -> None:
    """Replace the noop tasks provider with InMemoryTasksProvider for these tests."""
    from agentic_primitives_gateway.primitives.tasks.in_memory import InMemoryTasksProvider
    from agentic_primitives_gateway.registry import _PrimitiveProviders

    original = registry._primitives.get("tasks")
    in_mem = InMemoryTasksProvider()
    registry._primitives["tasks"] = _PrimitiveProviders(
        primitive="tasks", default_name="default", providers={"default": in_mem}
    )
    yield  # type: ignore[misc]
    if original is not None:
        registry._primitives["tasks"] = original


@pytest.fixture
def agent_store(tmp_path) -> FileAgentStore:
    return FileAgentStore(path=str(tmp_path / "agents.json"))


@pytest.fixture
def team_store(tmp_path) -> FileTeamStore:
    return FileTeamStore(path=str(tmp_path / "teams.json"))


@pytest.fixture
def runner(agent_store, team_store) -> TeamRunner:
    r = TeamRunner()
    agent_runner = AgentRunner()
    agent_runner.set_store(agent_store)
    r.set_stores(agent_store, team_store, agent_runner)
    return r


# ── Tests ────────────────────────────────────────────────────────────


class TestTeamRunHappyPath:
    """Full plan → execute → synthesize cycle."""

    async def test_plan_execute_synthesize(self, runner: TeamRunner, agent_store: FileAgentStore) -> None:
        """Planner creates 2 tasks, worker completes them, synthesizer responds."""
        planner = _make_agent("planner")
        worker = _make_worker("worker1")
        synthesizer = _make_agent("synthesizer")
        await agent_store.create(planner)
        await agent_store.create(worker)
        await agent_store.create(synthesizer)

        team = _make_team(workers=["worker1"])

        # Planner: create 2 tasks then stop
        planner_responses = _planner_response_with_tasks(
            [
                {"title": "Task A", "description": "Do task A", "assigned_to": "worker1"},
                {"title": "Task B", "description": "Do task B", "assigned_to": "worker1"},
            ]
        )

        # Worker: just respond with text for each task
        worker_responses = [
            _text_response("Task A result"),
            _text_response("Task B result"),
        ]

        # Synthesizer: final response
        synth_responses = [_text_response("Here is the final synthesis.")]

        # Replanner: no new tasks
        replanner_responses = [_text_response("No new tasks needed.")]

        all_responses = planner_responses + worker_responses + replanner_responses + synth_responses

        with patch(f"{_GATEWAY_MOD}.route_request", new_callable=AsyncMock, side_effect=all_responses):
            result = await runner.run(team, "Do something complex")

        assert result.response == "Here is the final synthesis."
        assert result.phase == TeamRunPhase.DONE
        assert result.tasks_created == 2
        assert result.tasks_completed == 2
        assert "worker1" in result.workers_used

    async def test_worker_not_found_skipped(self, runner: TeamRunner, agent_store: FileAgentStore) -> None:
        """Worker that doesn't exist in agent store is skipped."""
        planner = _make_agent("planner")
        synthesizer = _make_agent("synthesizer")
        await agent_store.create(planner)
        await agent_store.create(synthesizer)
        # Note: "missing-worker" is NOT created

        team = _make_team(workers=["missing-worker"])

        planner_responses = _planner_response_with_tasks(
            [
                {"title": "Task X", "description": "Do X", "assigned_to": "missing-worker"},
            ]
        )
        synth_responses = [_text_response("Synthesis with missing worker.")]
        all_responses = planner_responses + synth_responses

        with patch(f"{_GATEWAY_MOD}.route_request", new_callable=AsyncMock, side_effect=all_responses):
            result = await runner.run(team, "test")

        # Task was created but worker couldn't claim it
        assert result.tasks_created == 1
        assert result.tasks_completed == 0


class TestTeamRunStreaming:
    """Streaming variant produces expected event types."""

    async def test_stream_produces_events(self, runner: TeamRunner, agent_store: FileAgentStore) -> None:
        planner = _make_agent("planner")
        worker = _make_worker("worker1")
        synthesizer = _make_agent("synthesizer")
        await agent_store.create(planner)
        await agent_store.create(worker)
        await agent_store.create(synthesizer)

        team = _make_team(workers=["worker1"])

        planner_responses = _planner_response_with_tasks(
            [
                {"title": "Stream Task", "description": "Do it", "assigned_to": "worker1"},
            ]
        )
        worker_responses = [_text_response("stream result")]
        replanner_responses = [_text_response("No new tasks.")]
        synth_responses = [_text_response("Streamed synthesis.")]

        all_responses = planner_responses + worker_responses + replanner_responses + synth_responses

        async def mock_stream(request_dict):
            """Mock streaming by yielding a single content chunk + message_stop."""
            resp = all_responses.pop(0)
            if resp.get("tool_calls"):
                for tc in resp["tool_calls"]:
                    yield {"type": "tool_use_start", "id": tc["id"], "name": tc["name"]}
                    yield {"type": "tool_use_complete", "input": tc["input"]}
                yield {"type": "message_stop", "stop_reason": "tool_use"}
            else:
                yield {"type": "content_delta", "delta": resp["content"]}
                yield {"type": "message_stop", "stop_reason": "end_turn"}

        with (
            patch(f"{_GATEWAY_MOD}.route_request", new_callable=AsyncMock, side_effect=all_responses),
            patch(f"{_GATEWAY_MOD}.route_request_stream", side_effect=mock_stream),
        ):
            events = []
            async for event in runner.run_stream(team, "test streaming"):
                events.append(event)

        event_types = [e["type"] for e in events]
        assert "team_start" in event_types
        assert "phase_change" in event_types
        assert "tasks_created" in event_types
        assert "done" in event_types

        done_event = next(e for e in events if e["type"] == "done")
        assert done_event["tasks_created"] == 1


class TestGatherUpstreamContext:
    """Tests for _gather_upstream_context which reads dependent task results."""

    async def test_no_dependencies(self, runner: TeamRunner) -> None:
        run_id = uuid.uuid4().hex[:16]
        await registry.tasks.create_task(run_id, title="solo", description="no deps")

        tasks = await registry.tasks.list_tasks(run_id)
        result = await runner._gather_upstream_context(run_id, tasks[0].id)
        assert result == ""

    async def test_with_completed_dependency(self, runner: TeamRunner) -> None:
        run_id = uuid.uuid4().hex[:16]
        await registry.tasks.create_task(run_id, title="dep task", description="upstream")
        tasks = await registry.tasks.list_tasks(run_id)
        dep_id = tasks[0].id

        # Complete the dependency
        await registry.tasks.update_task(run_id, dep_id, status=TaskStatus.DONE, result="upstream result")

        # Create a task that depends on it
        await registry.tasks.create_task(run_id, title="main task", description="downstream", depends_on=[dep_id])
        tasks = await registry.tasks.list_tasks(run_id)
        main_task = next(t for t in tasks if t.title == "main task")

        result = await runner._gather_upstream_context(run_id, main_task.id)
        assert "upstream result" in result
        assert "dep task" in result


class TestStartStopSessions:
    """Tests for _start_sessions and _stop_sessions."""

    async def test_start_sessions_code_interpreter(self, runner: TeamRunner) -> None:
        worker = _make_worker("ci-worker", with_code_interpreter=True)
        ctx = await runner._start_sessions(worker)
        assert "code_interpreter" in ctx

    async def test_start_sessions_no_primitives(self, runner: TeamRunner) -> None:
        worker = _make_worker("plain-worker")
        ctx = await runner._start_sessions(worker)
        assert ctx == {}

    async def test_stop_sessions_noop(self, runner: TeamRunner) -> None:
        """Stop sessions on empty context doesn't raise."""
        await runner._stop_sessions({})

    async def test_stop_sessions_code_interpreter(self, runner: TeamRunner) -> None:
        """Stop sessions calls the provider."""
        await runner._stop_sessions({"code_interpreter": "fake-sid"})


class TestApplyRestoreOverrides:
    """Tests for _apply_overrides and _restore_overrides."""

    def test_no_overrides(self) -> None:
        spec = _make_agent("test")
        prev = TeamRunner._apply_overrides(spec)
        assert prev == {} or isinstance(prev, dict)
        TeamRunner._restore_overrides(prev)

    def test_with_overrides(self) -> None:
        spec = _make_agent("test")
        spec.provider_overrides = {"memory": "custom-provider"}
        prev = TeamRunner._apply_overrides(spec)
        TeamRunner._restore_overrides(prev)


class TestHelpers:
    async def test_get_agent_not_found(self, runner: TeamRunner) -> None:
        with pytest.raises(ValueError, match="not found"):
            await runner._get_agent("nonexistent", "Worker")

    def test_worker_names_respects_max_concurrent(self) -> None:
        team = _make_team(workers=["a", "b", "c"])
        team.max_concurrent = 2
        names = TeamRunner()._worker_names(team)
        assert names == ["a", "b"]

    def test_worker_names_no_limit(self) -> None:
        team = _make_team(workers=["a", "b", "c"])
        names = TeamRunner()._worker_names(team)
        assert names == ["a", "b", "c"]

    async def test_has_incomplete_tasks(self) -> None:
        run_id = uuid.uuid4().hex[:16]
        assert not await TeamRunner._has_incomplete_tasks(run_id)

        await registry.tasks.create_task(run_id, title="pending", description="test")
        assert await TeamRunner._has_incomplete_tasks(run_id)

    async def test_claim_batch(self) -> None:
        run_id = uuid.uuid4().hex[:16]
        await registry.tasks.create_task(run_id, title="claim me", description="test", suggested_worker="w1")
        available = await registry.tasks.get_available(run_id, worker_name="w1")
        claimed = await TeamRunner._claim_batch(run_id, available, "w1")
        assert len(claimed) == len(available)


# ── Cancel run tests ─────────────────────────────────────────────────


class TestCancelRun:
    """Tests for cancel_run() setting the cancel event."""

    def test_cancel_run_with_active_event(self) -> None:
        tr = TeamRunner()
        event = asyncio.Event()
        tr._cancel_events["run-1"] = event
        assert not event.is_set()

        tr.cancel_run("run-1")
        assert event.is_set()

    def test_cancel_run_no_event(self) -> None:
        tr = TeamRunner()
        # Should not raise when event doesn't exist
        tr.cancel_run("nonexistent")

    def test_cancel_run_idempotent(self) -> None:
        tr = TeamRunner()
        event = asyncio.Event()
        tr._cancel_events["run-1"] = event

        tr.cancel_run("run-1")
        tr.cancel_run("run-1")  # Second call is fine
        assert event.is_set()


# ── Recover partial tokens tests ─────────────────────────────────────


class TestRecoverPartialTokens:
    """Tests for _recover_partial_tokens with and without event store."""

    async def test_no_event_store(self) -> None:
        tr = TeamRunner()
        result = await tr._recover_partial_tokens("run-1", "planner")
        assert result == ""

    async def test_empty_events(self) -> None:
        tr = TeamRunner()
        mock_event_store = AsyncMock()
        mock_event_store.get_events = AsyncMock(return_value=[])

        with patch.object(tr, "_get_event_store", return_value=mock_event_store):
            result = await tr._recover_partial_tokens("run-1", "planner")
        assert result == ""

    async def test_no_matching_invocation(self) -> None:
        tr = TeamRunner()
        mock_event_store = AsyncMock()
        mock_event_store.get_events = AsyncMock(
            return_value=[
                {"type": "agent_token", "content": "hello", "invocation_id": "inv-1"},
                {"type": "invocation_start", "agent": "worker1", "invocation_id": "inv-1"},
            ]
        )

        with patch.object(tr, "_get_event_store", return_value=mock_event_store):
            result = await tr._recover_partial_tokens("run-1", "planner")
        assert result == ""

    async def test_recovers_tokens_for_matching_role(self) -> None:
        tr = TeamRunner()
        mock_event_store = AsyncMock()
        mock_event_store.get_events = AsyncMock(
            return_value=[
                {"type": "invocation_start", "agent": "planner", "invocation_id": "inv-1"},
                {"type": "agent_token", "content": "Hello ", "invocation_id": "inv-1"},
                {"type": "agent_token", "content": "world", "invocation_id": "inv-1"},
            ]
        )

        with patch.object(tr, "_get_event_store", return_value=mock_event_store):
            result = await tr._recover_partial_tokens("run-1", "planner")
        assert result == "Hello world"

    async def test_only_recovers_last_invocation(self) -> None:
        tr = TeamRunner()
        mock_event_store = AsyncMock()
        mock_event_store.get_events = AsyncMock(
            return_value=[
                {"type": "invocation_start", "agent": "planner", "invocation_id": "inv-1"},
                {"type": "agent_token", "content": "old ", "invocation_id": "inv-1"},
                {"type": "invocation_start", "agent": "planner", "invocation_id": "inv-2"},
                {"type": "agent_token", "content": "new ", "invocation_id": "inv-2"},
                {"type": "agent_token", "content": "tokens", "invocation_id": "inv-2"},
            ]
        )

        with patch.object(tr, "_get_event_store", return_value=mock_event_store):
            result = await tr._recover_partial_tokens("run-1", "planner")
        assert result == "new tokens"

    async def test_exception_returns_empty(self) -> None:
        tr = TeamRunner()
        mock_event_store = AsyncMock()
        mock_event_store.get_events = AsyncMock(side_effect=RuntimeError("Redis down"))

        with patch.object(tr, "_get_event_store", return_value=mock_event_store):
            result = await tr._recover_partial_tokens("run-1", "planner")
        assert result == ""

    async def test_non_dict_events_skipped(self) -> None:
        tr = TeamRunner()
        mock_event_store = AsyncMock()
        mock_event_store.get_events = AsyncMock(
            return_value=[
                "not-a-dict",
                {"type": "invocation_start", "agent": "planner", "invocation_id": "inv-1"},
                "another-string",
                {"type": "agent_token", "content": "token", "invocation_id": "inv-1"},
            ]
        )

        with patch.object(tr, "_get_event_store", return_value=mock_event_store):
            result = await tr._recover_partial_tokens("run-1", "planner")
        assert result == "token"


# ── Worker tool building tests ───────────────────────────────────────


class TestBuildWorkerTools:
    """Tests for _build_worker_tools."""

    def test_builds_tools_with_task_board(self) -> None:
        tr = TeamRunner()
        worker_spec = _make_worker("w1")
        team_spec = _make_team(workers=["w1"])
        tools = tr._build_worker_tools(worker_spec, team_spec, "run-1", {})
        tool_names = [t.name for t in tools]
        # Should include task board tools
        assert "list_tasks" in tool_names
        assert "complete_task" in tool_names

    def test_builds_tools_with_session_context(self) -> None:
        tr = TeamRunner()
        worker_spec = _make_worker("w1", with_code_interpreter=True)
        team_spec = _make_team(workers=["w1"])
        tools = tr._build_worker_tools(worker_spec, team_spec, "run-1", {"code_interpreter": "sid-1"})
        tool_names = [t.name for t in tools]
        assert "execute_code" in tool_names


# ── Team checkpoint tests ────────────────────────────────────────────


class TestTeamCheckpoint:
    """Tests for _team_checkpoint and _delete_team_checkpoint."""

    async def test_checkpoint_saves_data(self) -> None:
        from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
        from agentic_primitives_gateway.context import set_authenticated_principal

        tr = TeamRunner()
        mock_store = AsyncMock()
        tr._checkpoint_store = mock_store
        tr._replica_id = "replica-1"

        principal = AuthenticatedPrincipal(id="alice", type="user", groups=frozenset(), scopes=frozenset())
        set_authenticated_principal(principal)

        team_spec = _make_team()
        team_spec.checkpointing_enabled = True
        await tr._team_checkpoint(team_spec, "run-1", "do something", "planning")

        mock_store.save.assert_called_once()
        call_args = mock_store.save.call_args
        data = call_args[0][1]
        assert data["type"] == "team"
        assert data["spec_name"] == "test-team"
        assert data["phase"] == "planning"
        assert data["replica_id"] == "replica-1"

    async def test_checkpoint_skips_without_store(self) -> None:
        tr = TeamRunner()
        team_spec = _make_team()
        # Should not raise
        await tr._team_checkpoint(team_spec, "run-1", "msg", "planning")

    async def test_checkpoint_skips_when_disabled(self) -> None:
        tr = TeamRunner()
        tr._checkpoint_store = AsyncMock()
        team_spec = _make_team()
        team_spec.checkpointing_enabled = False
        await tr._team_checkpoint(team_spec, "run-1", "msg", "planning")
        tr._checkpoint_store.save.assert_not_called()

    async def test_delete_checkpoint(self) -> None:
        from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
        from agentic_primitives_gateway.context import set_authenticated_principal

        tr = TeamRunner()
        mock_store = AsyncMock()
        tr._checkpoint_store = mock_store

        principal = AuthenticatedPrincipal(id="alice", type="user", groups=frozenset(), scopes=frozenset())
        set_authenticated_principal(principal)

        await tr._delete_team_checkpoint("run-1")
        mock_store.delete.assert_called_once_with("alice:run-1")


# ── Set stores / set methods tests ───────────────────────────────────


class TestSetters:
    def test_set_stores(self) -> None:
        tr = TeamRunner()
        agent_store = MagicMock()
        team_store = MagicMock()
        agent_runner = MagicMock()
        tr.set_stores(agent_store, team_store, agent_runner)
        assert tr._agent_store is agent_store
        assert tr._team_store is team_store
        assert tr._agent_runner is agent_runner

    def test_set_session_registry(self) -> None:
        tr = TeamRunner()
        reg = MagicMock()
        tr.set_session_registry(reg)
        assert tr._session_registry is reg

    def test_set_checkpoint_store(self) -> None:
        tr = TeamRunner()
        store = MagicMock()
        tr.set_checkpoint_store(store, replica_id="r1")
        assert tr._checkpoint_store is store
        assert tr._replica_id == "r1"
