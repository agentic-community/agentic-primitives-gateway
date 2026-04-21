from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from agentic_primitives_gateway.agents.team_prompts import (
    build_planner_prompt,
    build_replan_prompt,
    build_synthesis_prompt,
    build_task_message,
    build_worker_descriptions,
)
from agentic_primitives_gateway.models.teams import TeamSpec


def _make_team_spec(workers: list[str] | None = None) -> TeamSpec:
    return TeamSpec(
        name="test-team",
        planner="planner",
        synthesizer="synth",
        workers=workers or ["researcher", "coder"],
    )


def _make_task(
    id: str = "t1",
    title: str = "Task 1",
    status: str = "done",
    result: str = "result",
    assigned_to: str = "worker1",
    suggested_worker: str | None = None,
    notes: list | None = None,
    depends_on: list | None = None,
    priority: int = 0,
):
    task = MagicMock()
    task.id = id
    task.title = title
    task.status = status
    task.result = result
    task.assigned_to = assigned_to
    task.suggested_worker = suggested_worker
    task.notes = notes or []
    task.depends_on = depends_on or []
    task.priority = priority
    return task


class TestBuildWorkerDescriptions:
    async def test_with_descriptions_and_primitives(self) -> None:
        store = AsyncMock()
        spec = MagicMock()
        spec.description = "Researches stuff"
        spec.primitives = {"memory": MagicMock(enabled=True), "browser": MagicMock(enabled=True)}
        # Versioned store — worker resolution goes through
        # ``_resolve_team_agent`` which calls ``resolve_qualified``.
        store.resolve_qualified.return_value = spec

        team_spec = _make_team_spec(workers=["researcher"])
        result = await build_worker_descriptions(team_spec, store)
        assert "researcher" in result
        assert "Researches stuff" in result
        assert "capabilities" in result

    async def test_worker_not_found(self) -> None:
        store = AsyncMock()
        store.resolve_qualified.return_value = None

        team_spec = _make_team_spec(workers=["missing"])
        result = await build_worker_descriptions(team_spec, store)
        assert "missing" in result


class TestBuildPlannerPrompt:
    async def test_planner_prompt(self) -> None:
        store = AsyncMock()
        store.resolve_qualified.return_value = None
        team_spec = _make_team_spec()
        result = await build_planner_prompt(team_spec, "build a website", store)
        assert "build a website" in result
        assert "task planner" in result


class TestBuildReplanPrompt:
    async def test_returns_none_when_no_completed_tasks(self) -> None:
        store = AsyncMock()
        with patch("agentic_primitives_gateway.agents.team_prompts.registry") as mock_reg:
            mock_reg.tasks = AsyncMock()
            mock_reg.tasks.list_tasks.return_value = [_make_task(status="pending")]
            result = await build_replan_prompt(_make_team_spec(), "run1", "msg", store)
        assert result is None

    async def test_returns_prompt_with_completed_tasks(self) -> None:
        store = AsyncMock()
        store.resolve_qualified.return_value = None
        with patch("agentic_primitives_gateway.agents.team_prompts.registry") as mock_reg:
            mock_reg.tasks = AsyncMock()
            mock_reg.tasks.list_tasks.return_value = [
                _make_task(status="done", result="found data"),
                _make_task(id="t2", status="pending", result=None, assigned_to=""),
            ]
            result = await build_replan_prompt(_make_team_spec(), "run1", "original msg", store)
        assert result is not None
        assert "found data" in result
        assert "original msg" in result


class TestBuildSynthesisPrompt:
    async def test_synthesis_prompt(self) -> None:
        note = MagicMock()
        note.agent = "worker1"
        note.content = "helpful note"
        with patch("agentic_primitives_gateway.agents.team_prompts.registry") as mock_reg:
            mock_reg.tasks = AsyncMock()
            mock_reg.tasks.list_tasks.return_value = [
                _make_task(status="done", result="code written", notes=[note]),
                _make_task(id="t2", status="failed", result="error occurred"),
            ]
            result = await build_synthesis_prompt("run1", "build stuff")
        assert "code written" in result
        assert "error occurred" in result
        assert "helpful note" in result
        assert "build stuff" in result


class TestBuildTaskMessage:
    def test_with_upstream_context(self) -> None:
        result = build_task_message("Write code", "Implement feature X", "Research says: use Python")
        assert "Write code" in result
        assert "Implement feature X" in result
        assert "Research says" in result

    def test_without_upstream_context(self) -> None:
        result = build_task_message("Review", "Check quality", "")
        assert "Review" in result
        assert "Context from completed" not in result
