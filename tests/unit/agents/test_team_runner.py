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

from agentic_primitives_gateway.agents.file_store import FileAgentStore, FileTeamStore
from agentic_primitives_gateway.agents.runner import AgentRunner
from agentic_primitives_gateway.agents.team_runner import TeamRunner
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import set_authenticated_principal
from agentic_primitives_gateway.models.agents import AgentSpec, HooksConfig, PrimitiveConfig
from agentic_primitives_gateway.models.tasks import TaskStatus
from agentic_primitives_gateway.models.teams import TeamRunPhase, TeamSpec
from agentic_primitives_gateway.registry import registry

_GATEWAY_MOD = "agentic_primitives_gateway.agents.team_agent_loop.registry.llm"

_TEST_PRINCIPAL = AuthenticatedPrincipal(id="test-user", type="user")


@pytest.fixture(autouse=True)
def _set_test_principal():
    """Ensure all team runner tests have an authenticated principal."""
    set_authenticated_principal(_TEST_PRINCIPAL)
    yield
    set_authenticated_principal(None)  # type: ignore[arg-type]


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
        shared_with=["*"],
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
        shared_with=["*"],
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

    async def test_cancel_during_planning_does_not_start_workers_or_synthesis(self, runner: TeamRunner) -> None:
        team = _make_team()

        async def planner(*args: Any, **kwargs: Any):
            yield {"type": "agent_token", "content": "planning"}

        with patch.object(runner, "_run_planner_stream", side_effect=planner):
            events = []
            async for event in runner.run_stream(team, "request"):
                events.append(event)
                if event["type"] == "agent_token":
                    run_id = events[0]["team_run_id"]
                    runner.cancel_run(run_id, event_key=runner._event_keys[run_id])

        assert events[-1] == {"type": "cancelled", "team_run_id": events[0]["team_run_id"]}
        assert not any(event["type"] == "done" or event.get("phase") == "execution" for event in events)


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
        session_ids, cv_tokens = await runner._start_sessions(worker)
        assert "code_interpreter" in session_ids
        # Reset contextvars so we don't leak into other tests
        await runner._stop_sessions(session_ids, cv_tokens)

    async def test_team_session_claims_and_releases_owner(self, runner: TeamRunner) -> None:
        from agentic_primitives_gateway.routes._helpers import code_interpreter_session_owners

        worker = _make_worker("ci-owned", with_code_interpreter=True)
        session_ids, cv_tokens = await runner._start_sessions(worker)
        sid = session_ids["code_interpreter"]
        assert await code_interpreter_session_owners.get_owner(sid) == _TEST_PRINCIPAL.id
        await runner._stop_sessions(session_ids, cv_tokens)
        assert await code_interpreter_session_owners.get_owner(sid) is None

    async def test_start_sessions_no_primitives(self, runner: TeamRunner) -> None:
        worker = _make_worker("plain-worker")
        session_ids, cv_tokens = await runner._start_sessions(worker)
        assert session_ids == {}
        assert cv_tokens == {}

    async def test_stop_sessions_noop(self, runner: TeamRunner) -> None:
        """Stop sessions with empty state doesn't raise."""
        await runner._stop_sessions({}, {})

    async def test_stop_sessions_code_interpreter(self, runner: TeamRunner) -> None:
        """Stop sessions calls the provider."""
        await runner._stop_sessions({"code_interpreter": "fake-sid"}, {})

    async def test_partial_start_failure_releases_prior_session_and_context(self, runner: TeamRunner) -> None:
        from agentic_primitives_gateway.primitives.code_interpreter.context import get_code_interpreter_session_id
        from agentic_primitives_gateway.routes._helpers import code_interpreter_session_owners

        worker = _make_worker("both", with_code_interpreter=True)
        worker.primitives["browser"] = PrimitiveConfig(enabled=True)
        started: list[str] = []
        stopped: list[str] = []

        async def start_code(session_id: str) -> dict[str, str]:
            started.append(session_id)
            return {"session_id": session_id}

        async def stop_code(session_id: str) -> None:
            stopped.append(session_id)

        async def fail_browser(session_id: str) -> dict[str, str]:
            raise RuntimeError("browser unavailable")

        before = get_code_interpreter_session_id()
        with (
            patch.object(registry.code_interpreter, "start_session", side_effect=start_code),
            patch.object(registry.code_interpreter, "stop_session", side_effect=stop_code),
            patch.object(registry.browser, "start_session", side_effect=fail_browser),
            pytest.raises(RuntimeError, match="browser unavailable"),
        ):
            await runner._start_sessions(worker)

        assert stopped == started
        assert await code_interpreter_session_owners.get_owner(started[0]) is None
        assert get_code_interpreter_session_id() == before

    async def test_registration_failure_stops_current_session_and_releases_claim(self, runner: TeamRunner) -> None:
        from agentic_primitives_gateway.routes._helpers import code_interpreter_session_owners

        worker = _make_worker("ci", with_code_interpreter=True)
        runner.set_session_registry(AsyncMock())
        runner._session_registry.register.side_effect = RuntimeError("registry unavailable")
        started: list[str] = []
        stopped: list[str] = []

        async def start(session_id: str) -> dict[str, str]:
            started.append(session_id)
            return {"session_id": session_id}

        async def stop(session_id: str) -> None:
            stopped.append(session_id)

        with (
            patch.object(registry.code_interpreter, "start_session", side_effect=start),
            patch.object(registry.code_interpreter, "stop_session", side_effect=stop),
            pytest.raises(RuntimeError, match="registry unavailable"),
        ):
            await runner._start_sessions(worker)
        assert stopped == started
        assert await code_interpreter_session_owners.get_owner(started[0]) is None

    async def test_stop_failure_retains_owner_and_resets_context(self, runner: TeamRunner) -> None:
        from agentic_primitives_gateway.primitives.code_interpreter.context import get_code_interpreter_session_id
        from agentic_primitives_gateway.routes._helpers import code_interpreter_session_owners

        worker = _make_worker("ci-stop", with_code_interpreter=True)
        before = get_code_interpreter_session_id()
        session_ids, cv_tokens = await runner._start_sessions(worker)
        sid = session_ids["code_interpreter"]
        with patch.object(registry.code_interpreter, "stop_session", side_effect=RuntimeError("stop unavailable")):
            await runner._stop_sessions(session_ids, cv_tokens)
        assert await code_interpreter_session_owners.get_owner(sid) == _TEST_PRINCIPAL.id
        assert get_code_interpreter_session_id() == before
        await runner._stop_sessions(session_ids, {})
        assert await code_interpreter_session_owners.get_owner(sid) is None


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
        tr._event_keys["run-1"] = "team:alice:one:run-1"
        assert not event.is_set()

        tr.cancel_run("run-1", event_key="team:bob:two:run-1")
        assert not event.is_set()
        tr.cancel_run("run-1", event_key="team:alice:one:run-1")
        assert event.is_set()

    def test_cancel_run_no_event(self) -> None:
        tr = TeamRunner()
        # Should not raise when event doesn't exist
        tr.cancel_run("nonexistent")

    def test_cancel_run_idempotent(self) -> None:
        tr = TeamRunner()
        event = asyncio.Event()
        tr._cancel_events["run-1"] = event
        tr._event_keys["run-1"] = "team:alice:one:run-1"

        tr.cancel_run("run-1", event_key="team:alice:one:run-1")
        tr.cancel_run("run-1", event_key="team:alice:one:run-1")  # Second call is fine
        assert event.is_set()

    async def test_stream_collision_preserves_existing_cancel_event(self, runner: TeamRunner) -> None:
        existing = asyncio.Event()
        runner._cancel_events["forced-id"] = existing
        with patch("agentic_primitives_gateway.agents.team_runner.uuid.uuid4") as generated:
            generated.return_value.hex = "forced-id"
            stream = runner.run_stream(_make_team(), "hi")
            try:
                with pytest.raises(ValueError, match="already active"):
                    await anext(stream)
            finally:
                await stream.aclose()
        assert runner._cancel_events["forced-id"] is existing

    async def test_stream_close_joins_in_flight_worker(self, runner: TeamRunner, agent_store: FileAgentStore) -> None:
        """Closing the event stream must wait for a cancelled tool before the board settles."""
        await agent_store.create(_make_worker("worker1"))
        team = _make_team()
        run_id = uuid.uuid4().hex[:16]
        task = await registry.tasks.create_task(run_id, title="Slow tool")
        started = asyncio.Event()
        interrupted = asyncio.Event()
        release = asyncio.Event()
        tool_finished = asyncio.Event()
        runner._cancel_events[run_id] = asyncio.Event()
        runner._remember_event_key(team, run_id)

        async def slow_tool(*args: Any, **kwargs: Any) -> str:
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                interrupted.set()
                await release.wait()
            tool_finished.set()
            return "tool result"

        with patch.object(runner, "_execute_task_streaming", side_effect=slow_tool):
            stream = runner._run_with_replanning_stream(team, run_id, "work")
            try:
                while True:
                    event = await asyncio.wait_for(anext(stream), timeout=2)
                    if event["type"] == "task_claimed":
                        break
                await asyncio.wait_for(started.wait(), timeout=2)
                runner.cancel_run(run_id, event_key=runner._event_keys[run_id])
                closing = asyncio.create_task(stream.aclose())
                await asyncio.wait_for(interrupted.wait(), timeout=2)
                assert not closing.done()
                assert not tool_finished.is_set()
                release.set()
                await asyncio.wait_for(closing, timeout=2)
            finally:
                release.set()
                await stream.aclose()

        settled = await registry.tasks.get_task(run_id, task.id)
        assert settled is not None
        assert settled.status == TaskStatus.FAILED
        assert settled.result == "Run cancelled"
        assert tool_finished.is_set()

    async def test_remote_cancel_during_tool_does_not_complete_task(self, runner: TeamRunner) -> None:
        """A different replica can fail the task while this replica awaits its tool."""
        team = _make_team()
        run_id = uuid.uuid4().hex[:16]
        task = await registry.tasks.create_task(run_id, title="Remote tool")
        claimed = await registry.tasks.claim_task(run_id, task.id, "worker1")
        assert claimed is not None
        runner._cancel_events[run_id] = asyncio.Event()
        entered = asyncio.Event()
        release = asyncio.Event()
        event_store = AsyncMock()
        event_store.get_status = AsyncMock(return_value="running")

        async def slow_tool(*args: Any, **kwargs: Any) -> str:
            entered.set()
            await release.wait()
            return "late result"

        events: list[dict[str, Any]] = []

        async def consume() -> None:
            async for event in runner._execute_tasks_parallel_stream(
                [claimed], _make_worker("worker1"), team, run_id, "worker1"
            ):
                events.append(event)

        with (
            patch.object(runner, "_execute_task_streaming", side_effect=slow_tool),
            patch.object(runner, "_get_event_store", return_value=event_store),
        ):
            consumer = asyncio.create_task(consume())
            try:
                await asyncio.wait_for(entered.wait(), timeout=2)
                await registry.tasks.update_task(run_id, task.id, status=TaskStatus.FAILED, result="Run cancelled")
                event_store.get_status.return_value = "cancelled"
                release.set()
                await asyncio.wait_for(consumer, timeout=2)
            finally:
                release.set()
                if not consumer.done():
                    consumer.cancel()
                await asyncio.gather(consumer, return_exceptions=True)

        settled = await registry.tasks.get_task(run_id, task.id)
        assert settled is not None
        assert settled.status == TaskStatus.FAILED
        assert settled.result == "Run cancelled"
        assert not any(event["type"] == "task_completed" for event in events)

    async def test_remote_cancel_racing_done_update_keeps_failed(self, runner: TeamRunner) -> None:
        """Cancellation between pre-write check and DONE must not resurrect FAILED."""
        team = _make_team()
        run_id = uuid.uuid4().hex[:16]
        task = await registry.tasks.create_task(run_id, title="Racing update")
        claimed = await registry.tasks.claim_task(run_id, task.id, "worker1")
        assert claimed is not None
        runner._cancel_events[run_id] = asyncio.Event()
        task_board = registry.tasks
        original_update = task_board.update_task
        entered_done = asyncio.Event()
        release_done = asyncio.Event()
        event_store = AsyncMock()
        event_store.get_status = AsyncMock(return_value="running")

        async def racing_update(team_run_id: str, task_id: str, **kwargs: Any) -> Any:
            if kwargs.get("status") == TaskStatus.DONE:
                entered_done.set()
                await release_done.wait()
            return await original_update(team_run_id, task_id, **kwargs)

        events: list[dict[str, Any]] = []

        async def consume() -> None:
            async for event in runner._execute_tasks_parallel_stream(
                [claimed], _make_worker("worker1"), team, run_id, "worker1"
            ):
                events.append(event)

        with (
            patch.object(runner, "_execute_task_streaming", new_callable=AsyncMock, return_value="result"),
            patch.object(runner, "_get_event_store", return_value=event_store),
            patch.object(task_board, "update_task", side_effect=racing_update),
        ):
            consumer = asyncio.create_task(consume())
            try:
                await asyncio.wait_for(entered_done.wait(), timeout=2)
                await original_update(run_id, task.id, status=TaskStatus.FAILED, result="Run cancelled")
                event_store.get_status.return_value = "cancelled"
                release_done.set()
                await asyncio.wait_for(consumer, timeout=2)
            finally:
                release_done.set()
                if not consumer.done():
                    consumer.cancel()
                await asyncio.gather(consumer, return_exceptions=True)

        settled = await registry.tasks.get_task(run_id, task.id)
        assert settled is not None
        assert settled.status == TaskStatus.FAILED
        assert settled.result == "Run cancelled"
        assert not any(event["type"] == "task_completed" for event in events)

    async def test_store_exception_does_not_cancel_other_workers(self, runner: TeamRunner) -> None:
        team = _make_team(workers=["worker1", "worker2"])
        run_id = uuid.uuid4().hex[:16]
        bad = await registry.tasks.create_task(run_id, title="Bad batch", suggested_worker="worker1")
        good = await registry.tasks.create_task(run_id, title="Other worker", suggested_worker="worker2")
        bad_claim = await registry.tasks.claim_task(run_id, bad.id, "worker1")
        good_claim = await registry.tasks.claim_task(run_id, good.id, "worker2")
        assert bad_claim is not None and good_claim is not None
        runner._cancel_events[run_id] = asyncio.Event()
        started = asyncio.Event()
        release = asyncio.Event()
        original_update = registry.tasks.update_task

        async def update(team_run_id: str, task_id: str, **kwargs: Any) -> Any:
            if task_id == bad.id and kwargs.get("status") == TaskStatus.IN_PROGRESS:
                await started.wait()
                raise RuntimeError("task store unavailable")
            return await original_update(team_run_id, task_id, **kwargs)

        async def execute(*args: Any, **kwargs: Any) -> str:
            started.set()
            await release.wait()
            return "other result"

        async def consume(claim: Any, worker: str) -> list[dict[str, Any]]:
            return [
                event
                async for event in runner._execute_tasks_parallel_stream(
                    [claim], _make_worker(worker), team, run_id, worker
                )
            ]

        with (
            patch.object(registry.tasks, "update_task", side_effect=update),
            patch.object(runner, "_execute_task_streaming", side_effect=execute),
        ):
            other = asyncio.create_task(consume(good_claim, "worker2"))
            try:
                await asyncio.wait_for(started.wait(), timeout=2)
                failed = await asyncio.wait_for(consume(bad_claim, "worker1"), timeout=2)
                assert any(event["type"] == "task_failed" for event in failed)
                assert not runner._cancel_events[run_id].is_set()
                release.set()
                result = await asyncio.wait_for(other, timeout=2)
            finally:
                release.set()
                if not other.done():
                    other.cancel()
                await asyncio.gather(other, return_exceptions=True)
        assert any(event["type"] == "task_completed" and event["task_id"] == good.id for event in result)
        assert (await registry.tasks.get_task(run_id, good.id)).status == TaskStatus.DONE  # type: ignore[union-attr]

    async def test_store_error_while_draining_batch_does_not_cancel_run(self, runner: TeamRunner) -> None:
        team = _make_team()
        run_id = uuid.uuid4().hex[:16]
        task = await registry.tasks.create_task(run_id, title="Worker in flight")
        claimed = await registry.tasks.claim_task(run_id, task.id, "worker1")
        assert claimed is not None
        runner._cancel_events[run_id] = asyncio.Event()
        entered = asyncio.Event()
        interrupted = asyncio.Event()
        other_started = asyncio.Event()
        other_release = asyncio.Event()
        other_finished = asyncio.Event()

        async def unrelated_worker() -> None:
            other_started.set()
            await other_release.wait()
            if not runner._cancel_events[run_id].is_set():
                other_finished.set()

        async def slow_worker(*args: Any, **kwargs: Any) -> str:
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                interrupted.set()
                raise

        async def store_failure(team_run_id: str) -> None:
            if entered.is_set():
                raise RuntimeError("store unavailable")

        async def consume() -> None:
            async for _ in runner._execute_tasks_parallel_stream(
                [claimed], _make_worker("worker1"), team, run_id, "worker1"
            ):
                pass

        other = asyncio.create_task(unrelated_worker())
        with (
            patch.object(runner, "_execute_task_streaming", side_effect=slow_worker),
            patch.object(runner, "_sync_remote_cancel", side_effect=store_failure),
        ):
            try:
                await asyncio.wait_for(other_started.wait(), timeout=2)
                with pytest.raises(RuntimeError, match="store unavailable"):
                    await asyncio.wait_for(consume(), timeout=2)
                other_release.set()
                await asyncio.wait_for(other, timeout=2)
            finally:
                other_release.set()
                if not other.done():
                    other.cancel()
                await asyncio.gather(other, return_exceptions=True)
        assert interrupted.is_set()
        assert not runner._cancel_events[run_id].is_set()
        assert other_finished.is_set()
        failed = await registry.tasks.get_task(run_id, task.id)
        assert failed is not None and failed.status == TaskStatus.FAILED
        assert failed.result == "Worker batch interrupted"


class TestRetryTask:
    async def test_retry_completes_and_unblocks_dependency(
        self, runner: TeamRunner, agent_store: FileAgentStore
    ) -> None:
        await agent_store.create(_make_worker("worker1"))
        team = _make_team()
        run_id = uuid.uuid4().hex[:16]
        failed = await registry.tasks.create_task(run_id, title="Retry me", suggested_worker="worker1")
        dependent = await registry.tasks.create_task(run_id, title="Blocked", depends_on=[failed.id])
        await registry.tasks.claim_task(run_id, failed.id, "worker1")
        await registry.tasks.update_task(run_id, failed.id, status=TaskStatus.FAILED, result="Temporary error")

        async def worker_stream(*args: Any, **kwargs: Any):
            yield {"type": "agent_token", "content": "Recovered"}

        with patch("agentic_primitives_gateway.agents.team_runner.run_agent_with_tools_stream", worker_stream):
            events = [event async for event in runner.retry_task_stream(team, run_id, failed.id)]

        assert [event["type"] for event in events] == ["task_retry", "agent_token", "task_completed", "retry_done"]
        updated = await registry.tasks.get_task(run_id, failed.id)
        assert updated is not None and updated.status == TaskStatus.DONE and updated.result == "Recovered"
        assert await registry.tasks.claim_task(run_id, dependent.id, "worker1") is not None

    async def test_retry_rejects_cancelled_and_competing_retries(
        self, runner: TeamRunner, agent_store: FileAgentStore
    ) -> None:
        await agent_store.create(_make_worker("worker1"))
        team = _make_team()
        run_id = uuid.uuid4().hex[:16]
        cancelled = await registry.tasks.create_task(run_id, title="Cancelled", suggested_worker="worker1")
        await registry.tasks.update_task(run_id, cancelled.id, status=TaskStatus.FAILED, result="Run cancelled")
        events = [event async for event in runner.retry_task_stream(team, run_id, cancelled.id)]
        assert [event["type"] for event in events] == ["error"]
        assert (await registry.tasks.get_task(run_id, cancelled.id)).status == TaskStatus.FAILED  # type: ignore[union-attr]

        failed = await registry.tasks.create_task(run_id, title="Race", suggested_worker="worker1")
        await registry.tasks.update_task(run_id, failed.id, status=TaskStatus.FAILED, result="Transient")
        with patch.object(registry.tasks, "retry_task", new_callable=AsyncMock, return_value=None):
            events = [event async for event in runner.retry_task_stream(team, run_id, failed.id)]
        assert [event["type"] for event in events] == ["error"]

    async def test_retry_start_failure_restores_failed_state(
        self, runner: TeamRunner, agent_store: FileAgentStore
    ) -> None:
        await agent_store.create(_make_worker("worker1", with_code_interpreter=True))
        team = _make_team()
        run_id = uuid.uuid4().hex[:16]
        task = await registry.tasks.create_task(run_id, title="Retry after failure", suggested_worker="worker1")
        await registry.tasks.update_task(run_id, task.id, status=TaskStatus.FAILED, result="Transient")
        with patch.object(runner, "_start_sessions", side_effect=RuntimeError("backend unavailable")):
            events = [event async for event in runner.retry_task_stream(team, run_id, task.id)]
        assert [event["type"] for event in events] == ["task_retry", "task_failed", "retry_done"]
        updated = await registry.tasks.get_task(run_id, task.id)
        assert updated is not None and updated.status == TaskStatus.FAILED
        assert updated.result == "backend unavailable"


# ── Recover partial tokens tests ─────────────────────────────────────


class TestRecoverPartialTokens:
    """Tests for _recover_partial_tokens with and without event store."""

    @pytest.fixture(autouse=True)
    def scoped_event_key(self):
        from agentic_primitives_gateway.routes._background import run_event_key

        self.team_spec = _make_team()
        self.expected_key = run_event_key(
            "team", _TEST_PRINCIPAL.id, self.team_spec.owner_id, self.team_spec.name, "run-1"
        )

    async def test_no_event_store(self) -> None:
        tr = TeamRunner()
        result = await tr._recover_partial_tokens("run-1", "planner", team_spec=self.team_spec)
        assert result == ""

    async def test_empty_events(self) -> None:
        tr = TeamRunner()
        mock_event_store = AsyncMock()
        mock_event_store.get_events = AsyncMock(return_value=[])

        with patch.object(tr, "_get_event_store", return_value=mock_event_store):
            result = await tr._recover_partial_tokens("run-1", "planner", team_spec=self.team_spec)
        assert result == ""
        mock_event_store.get_events.assert_awaited_once_with(self.expected_key)

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
            result = await tr._recover_partial_tokens("run-1", "planner", team_spec=self.team_spec)
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
            result = await tr._recover_partial_tokens("run-1", "planner", team_spec=self.team_spec)
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
            result = await tr._recover_partial_tokens("run-1", "planner", team_spec=self.team_spec)
        assert result == "new tokens"

    async def test_exception_returns_empty(self) -> None:
        tr = TeamRunner()
        mock_event_store = AsyncMock()
        mock_event_store.get_events = AsyncMock(side_effect=RuntimeError("Redis down"))

        with patch.object(tr, "_get_event_store", return_value=mock_event_store):
            result = await tr._recover_partial_tokens("run-1", "planner", team_spec=self.team_spec)
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
            result = await tr._recover_partial_tokens("run-1", "planner", team_spec=self.team_spec)
        assert result == "token"


# ── Worker tool building tests ───────────────────────────────────────


class TestBuildWorkerTools:
    """Tests for _build_worker_tools."""

    def test_builds_tools_with_task_board(self) -> None:
        tr = TeamRunner()
        worker_spec = _make_worker("w1")
        team_spec = _make_team(workers=["w1"])
        tools = tr._build_worker_tools(worker_spec, team_spec)
        tool_names = [t.name for t in tools]
        # Should include task board tools
        assert "list_tasks" in tool_names
        assert "complete_task" in tool_names

    def test_builds_tools_with_code_interpreter(self) -> None:
        tr = TeamRunner()
        worker_spec = _make_worker("w1", with_code_interpreter=True)
        team_spec = _make_team(workers=["w1"])
        tools = tr._build_worker_tools(worker_spec, team_spec)
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

        mock_store.save_if_active.assert_awaited_once()
        call_args = mock_store.save_if_active.call_args
        assert call_args[0][0] == tr._event_key(team_spec, "run-1")
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

        team_spec = _make_team()
        team_spec.checkpointing_enabled = True
        await tr._delete_team_checkpoint("run-1", team_spec)
        mock_store.delete_if_owned.assert_awaited_once_with(tr._event_key(team_spec, "run-1"))


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
