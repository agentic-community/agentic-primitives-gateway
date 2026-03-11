"""Team runner — orchestrates multi-agent team execution.

Phases:
  1. Initial planning:  Planner agent decomposes the prompt into tasks.
  2. Execution:         Worker agents concurrently claim and complete tasks.
     - After each wave, a re-planner evaluates completed results and may
       create follow-up tasks, restarting the worker loop.
  3. Synthesis:         Synthesizer agent reads all results and produces a response.

Prompt builders live in ``team_prompts.py``.  The generic LLM tool-call
loops live in ``team_agent_loop.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from agentic_primitives_gateway.agents.runner import AgentRunner
from agentic_primitives_gateway.agents.store import AgentStore
from agentic_primitives_gateway.agents.team_agent_loop import run_agent_with_tools, run_agent_with_tools_stream
from agentic_primitives_gateway.agents.team_prompts import (
    build_planner_prompt,
    build_replan_prompt,
    build_synthesis_prompt,
    build_task_message,
)
from agentic_primitives_gateway.agents.team_store import TeamStore
from agentic_primitives_gateway.agents.tools import ToolDefinition, build_tool_list
from agentic_primitives_gateway.context import get_provider_override, set_provider_overrides
from agentic_primitives_gateway.models.agents import AgentSpec, PrimitiveConfig
from agentic_primitives_gateway.models.enums import Primitive
from agentic_primitives_gateway.models.tasks import TaskStatus
from agentic_primitives_gateway.models.teams import TeamRunPhase, TeamRunResponse, TeamSpec
from agentic_primitives_gateway.registry import registry

logger = logging.getLogger(__name__)

# How long a worker waits before re-checking the board (seconds)
_POLL_INTERVAL = 1.0

# Tools every worker gets for interacting with the task board
_WORKER_BOARD_TOOLS = [
    "list_tasks",
    "get_task",
    "complete_task",
    "fail_task",
    "add_task_note",
    "get_available_tasks",
    "create_task",
]


class TeamRunner:
    """Orchestrates a team of agents working off a shared task board."""

    def __init__(self) -> None:
        self._agent_store: AgentStore | None = None
        self._team_store: TeamStore | None = None
        self._agent_runner: AgentRunner | None = None
        self._session_registry: Any | None = None

    def set_stores(
        self,
        agent_store: AgentStore,
        team_store: TeamStore,
        agent_runner: AgentRunner,
    ) -> None:
        self._agent_store = agent_store
        self._team_store = team_store
        self._agent_runner = agent_runner

    def set_session_registry(self, registry: Any) -> None:
        self._session_registry = registry

    # ── Public entry points ──────────────────────────────────────────

    async def run(
        self,
        team_spec: TeamSpec,
        message: str,
    ) -> TeamRunResponse:
        """Non-streaming team run. Returns final synthesized response."""
        team_run_id = uuid.uuid4().hex[:16]
        assert self._agent_store is not None
        assert self._agent_runner is not None

        logger.info("Team[%s] run=%s phase=planning", team_spec.name, team_run_id)
        await self._run_planner(team_spec, team_run_id, message)

        logger.info("Team[%s] run=%s phase=execution", team_spec.name, team_run_id)
        workers_used = await self._run_with_replanning(team_spec, team_run_id, message)

        logger.info("Team[%s] run=%s phase=synthesis", team_spec.name, team_run_id)
        response = await self._run_synthesizer(team_spec, team_run_id, message)

        all_tasks = await registry.tasks.list_tasks(team_run_id)
        done_count = sum(1 for t in all_tasks if t.status == TaskStatus.DONE)

        return TeamRunResponse(
            response=response,
            team_run_id=team_run_id,
            team_name=team_spec.name,
            phase=TeamRunPhase.DONE,
            tasks_created=len(all_tasks),
            tasks_completed=done_count,
            workers_used=workers_used,
        )

    async def run_stream(
        self,
        team_spec: TeamSpec,
        message: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """Streaming team run. Yields SSE-friendly event dicts."""
        team_run_id = uuid.uuid4().hex[:16]
        assert self._agent_store is not None
        assert self._agent_runner is not None

        yield {"type": "team_start", "team_run_id": team_run_id, "team_name": team_spec.name}

        # Phase 1: Initial planning
        yield {"type": "phase_change", "phase": "planning"}
        async for event in self._run_planner_stream(team_spec, team_run_id, message):
            yield event

        all_tasks = await registry.tasks.list_tasks(team_run_id)
        yield {
            "type": "tasks_created",
            "count": len(all_tasks),
            "tasks": [
                {"id": t.id, "title": t.title, "priority": t.priority, "suggested_worker": t.suggested_worker}
                for t in all_tasks
            ],
        }

        # Phase 2: Execution with continuous re-planning
        yield {"type": "phase_change", "phase": "execution"}
        workers_used: list[str] = []
        async for event in self._run_with_replanning_stream(team_spec, team_run_id, message):
            if event.get("type") == "worker_done" and event["agent"] not in workers_used:
                workers_used.append(event["agent"])
            yield event

        # Phase 3: Synthesis
        yield {"type": "phase_change", "phase": "synthesis"}
        response = ""
        async for event in self._run_synthesizer_stream(team_spec, team_run_id, message):
            if event.get("type") == "agent_token":
                response += event.get("content", "")
            yield event

        all_tasks = await registry.tasks.list_tasks(team_run_id)
        done_count = sum(1 for t in all_tasks if t.status == TaskStatus.DONE)

        yield {
            "type": "done",
            "response": response,
            "team_run_id": team_run_id,
            "team_name": team_spec.name,
            "phase": "done",
            "tasks_created": len(all_tasks),
            "tasks_completed": done_count,
            "workers_used": workers_used,
        }

    # ── Phase 1: Planning ────────────────────────────────────────────

    async def _run_planner(self, team_spec: TeamSpec, team_run_id: str, message: str) -> None:
        planner_spec = await self._get_agent(team_spec.planner, "Planner")
        prompt = await build_planner_prompt(team_spec, message, self._agent_store)  # type: ignore[arg-type]
        tools = self._planner_tools(team_run_id, team_spec.planner)
        await run_agent_with_tools(planner_spec, prompt, tools, max_turns=planner_spec.max_turns)

    async def _run_planner_stream(
        self, team_spec: TeamSpec, team_run_id: str, message: str
    ) -> AsyncIterator[dict[str, Any]]:
        planner_spec = await self._get_agent(team_spec.planner, "Planner")
        prompt = await build_planner_prompt(team_spec, message, self._agent_store)  # type: ignore[arg-type]
        tools = self._planner_tools(team_run_id, team_spec.planner)
        logger.info("Planner prompt:\n%s", prompt)
        async for event in run_agent_with_tools_stream(
            planner_spec, prompt, tools, "planner", max_turns=planner_spec.max_turns
        ):
            yield event

    def _planner_tools(self, team_run_id: str, planner_name: str) -> list[ToolDefinition]:
        """Planner only gets create_task and list_tasks."""
        primitives = {"task_board": PrimitiveConfig(enabled=True, tools=["create_task", "list_tasks"])}
        return build_tool_list(primitives, namespace="__planner__", team_run_id=team_run_id, agent_name=planner_name)

    # ── Continuous re-planning ───────────────────────────────────────

    async def _run_replanner(self, team_spec: TeamSpec, team_run_id: str, message: str) -> int:
        """Evaluate completed tasks and create follow-ups. Returns new task count."""
        planner_spec = await self._agent_store.get(team_spec.planner)  # type: ignore[union-attr]
        if planner_spec is None:
            return 0
        prompt = await build_replan_prompt(team_spec, team_run_id, message, self._agent_store)  # type: ignore[arg-type]
        if prompt is None:
            return 0

        count_before = len(await registry.tasks.list_tasks(team_run_id))
        tools = self._planner_tools(team_run_id, team_spec.planner)
        await run_agent_with_tools(planner_spec, prompt, tools, max_turns=planner_spec.max_turns)
        count_after = len(await registry.tasks.list_tasks(team_run_id))

        new_count = count_after - count_before
        if new_count > 0:
            logger.info("Re-planner created %d new tasks", new_count)
        return new_count

    async def _run_replanner_stream(
        self,
        team_spec: TeamSpec,
        team_run_id: str,
        message: str,
        event_queue: asyncio.Queue[dict[str, Any] | None],
    ) -> int:
        """Streaming re-planner. Puts events on the queue. Returns new task count."""
        planner_spec = await self._agent_store.get(team_spec.planner)  # type: ignore[union-attr]
        if planner_spec is None:
            return 0
        prompt = await build_replan_prompt(team_spec, team_run_id, message, self._agent_store)  # type: ignore[arg-type]
        if prompt is None:
            return 0

        tasks_before = await registry.tasks.list_tasks(team_run_id)
        count_before = len(tasks_before)
        tools = self._planner_tools(team_run_id, team_spec.planner)

        await event_queue.put({"type": "phase_change", "phase": "replanning"})
        async for event in run_agent_with_tools_stream(
            planner_spec, prompt, tools, "planner", max_turns=planner_spec.max_turns
        ):
            await event_queue.put(event)

        tasks_after = await registry.tasks.list_tasks(team_run_id)
        new_tasks = tasks_after[count_before:]
        new_count = len(new_tasks)
        if new_count > 0:
            logger.info("Re-planner created %d new tasks", new_count)
            await event_queue.put(
                {
                    "type": "tasks_created",
                    "count": new_count,
                    "tasks": [
                        {"id": t.id, "title": t.title, "priority": t.priority, "suggested_worker": t.suggested_worker}
                        for t in new_tasks
                    ],
                }
            )
        return new_count

    # ── Phase 2: Execution with continuous re-planning ───────────────

    async def _run_with_replanning(self, team_spec: TeamSpec, team_run_id: str, message: str) -> list[str]:
        """Run workers, then re-plan. Repeat until no new tasks are created."""
        workers = self._worker_names(team_spec)
        reviewed_tasks: set[str] = set()
        workers_used: list[str] = []

        while True:
            # Run all workers until the board is idle
            worker_coros = [self._worker_loop(w, team_spec, team_run_id) for w in workers]
            results = await asyncio.gather(*worker_coros, return_exceptions=True)
            for name, result in zip(workers, results, strict=False):
                if not isinstance(result, Exception) and name not in workers_used:
                    workers_used.append(name)

            # Check for new completions since last review
            all_tasks = await registry.tasks.list_tasks(team_run_id)
            newly_completed = [t for t in all_tasks if t.status == TaskStatus.DONE and t.id not in reviewed_tasks]
            if not newly_completed:
                break
            for t in newly_completed:
                reviewed_tasks.add(t.id)

            new_count = await self._run_replanner(team_spec, team_run_id, message)
            if new_count == 0:
                break

        return workers_used

    async def _run_with_replanning_stream(
        self, team_spec: TeamSpec, team_run_id: str, message: str
    ) -> AsyncIterator[dict[str, Any]]:
        """Streaming execution with re-planning between worker waves."""
        workers = self._worker_names(team_spec)
        reviewed_tasks: set[str] = set()

        while True:
            # Merge events from all concurrent workers via a shared queue
            queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
            active_count = len(workers)

            async def _worker_wrapper(
                worker_name: str,
                _q: asyncio.Queue[dict[str, Any] | None] = queue,
            ) -> None:
                try:
                    async for event in self._worker_loop_stream(worker_name, team_spec, team_run_id):
                        await _q.put(event)
                except Exception as e:
                    await _q.put({"type": "worker_error", "agent": worker_name, "error": str(e)})
                finally:
                    await _q.put(None)  # sentinel

            worker_tasks = [asyncio.create_task(_worker_wrapper(w)) for w in workers]

            finished = 0
            while finished < active_count:
                event = await queue.get()
                if event is None:
                    finished += 1
                    continue
                yield event

            # Cancel any lingering wrapper tasks (async generator cleanup)
            for wt in worker_tasks:
                if not wt.done():
                    wt.cancel()

            # Re-plan based on newly completed tasks
            logger.info("All workers finished, checking for re-planning...")
            all_tasks = await registry.tasks.list_tasks(team_run_id)
            newly_completed = [t for t in all_tasks if t.status == TaskStatus.DONE and t.id not in reviewed_tasks]
            logger.info(
                "Re-plan check: %d total tasks, %d newly completed, %d already reviewed",
                len(all_tasks),
                len(newly_completed),
                len(reviewed_tasks),
            )
            if not newly_completed:
                break
            for t in newly_completed:
                reviewed_tasks.add(t.id)
                logger.info("  Newly completed: [%s] %s", t.id, t.title)

            replan_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
            new_count = await self._run_replanner_stream(team_spec, team_run_id, message, replan_queue)
            while not replan_queue.empty():
                event = await replan_queue.get()
                if event is not None:
                    yield event

            logger.info("Re-planner created %d new tasks", new_count)
            if new_count == 0:
                break
            logger.info("Restarting workers for new tasks")

    # ── Worker loops ─────────────────────────────────────────────────

    async def _worker_loop(self, worker_name: str, team_spec: TeamSpec, team_run_id: str) -> None:
        """Non-streaming worker: poll board, claim tasks, execute in parallel batches."""
        worker_spec = await self._agent_store.get(worker_name)  # type: ignore[union-attr]
        if worker_spec is None:
            logger.warning("Worker agent '%s' not found — skipping", worker_name)
            return

        while True:
            available = await registry.tasks.get_available(team_run_id, worker_name=worker_name)
            if not available:
                if not await self._has_incomplete_tasks(team_run_id):
                    break
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            claimed = await self._claim_batch(team_run_id, available, worker_name)
            if not claimed:
                continue

            async def _run_one(t: Any) -> None:
                logger.info("Team worker '%s' claimed task '%s': %s", worker_name, t.id, t.title)
                await registry.tasks.update_task(team_run_id, t.id, status=TaskStatus.IN_PROGRESS)
                try:
                    result = await self._execute_task(worker_spec, team_spec, team_run_id, t.id, t.title, t.description)
                    await registry.tasks.update_task(team_run_id, t.id, status=TaskStatus.DONE, result=result)
                    logger.info("Team worker '%s' completed task '%s'", worker_name, t.id)
                except Exception as e:
                    logger.error("Team worker '%s' failed task '%s': %s: %s", worker_name, t.id, type(e).__name__, e)
                    with contextlib.suppress(Exception):
                        await registry.tasks.update_task(team_run_id, t.id, status=TaskStatus.FAILED, result=str(e))

            await asyncio.gather(*[_run_one(t) for t in claimed], return_exceptions=True)

    async def _worker_loop_stream(
        self, worker_name: str, team_spec: TeamSpec, team_run_id: str
    ) -> AsyncIterator[dict[str, Any]]:
        """Streaming worker: same as _worker_loop but yields events."""
        worker_spec = await self._agent_store.get(worker_name)  # type: ignore[union-attr]
        if worker_spec is None:
            yield {"type": "worker_error", "agent": worker_name, "error": f"Agent '{worker_name}' not found"}
            return

        yield {"type": "worker_start", "agent": worker_name}
        while True:
            available = await registry.tasks.get_available(team_run_id, worker_name=worker_name)
            if not available:
                if not await self._has_incomplete_tasks(team_run_id):
                    logger.info("Worker '%s': no incomplete tasks, exiting", worker_name)
                    break
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            claimed = await self._claim_batch(team_run_id, available, worker_name)
            if not claimed:
                continue

            async for event in self._execute_tasks_parallel_stream(
                claimed, worker_spec, team_spec, team_run_id, worker_name
            ):
                yield event

        yield {"type": "worker_done", "agent": worker_name}

    async def _execute_tasks_parallel_stream(
        self,
        claimed: list[Any],
        worker_spec: AgentSpec,
        team_spec: TeamSpec,
        team_run_id: str,
        worker_name: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """Execute claimed tasks in parallel, yielding events as they arrive."""
        event_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def _run_one(t: Any, _q: asyncio.Queue[dict[str, Any] | None] = event_queue) -> None:
            await _q.put({"type": "task_claimed", "agent": worker_name, "task_id": t.id, "title": t.title})
            await registry.tasks.update_task(team_run_id, t.id, status=TaskStatus.IN_PROGRESS)
            try:
                result = await self._execute_task_streaming(
                    worker_spec, team_spec, team_run_id, t.id, t.title, t.description, _q, worker_name
                )
                await registry.tasks.update_task(team_run_id, t.id, status=TaskStatus.DONE, result=result)
                await _q.put({"type": "task_completed", "agent": worker_name, "task_id": t.id, "result": result})
            except Exception as e:
                logger.error("Parallel task '%s' failed: %s: %s", t.id, type(e).__name__, e)
                with contextlib.suppress(Exception):
                    await registry.tasks.update_task(team_run_id, t.id, status=TaskStatus.FAILED, result=str(e))
                await _q.put({"type": "task_failed", "agent": worker_name, "task_id": t.id, "error": str(e)})

        parallel_tasks = [asyncio.create_task(_run_one(t)) for t in claimed]

        finished_ids: set[int] = set()
        while len(finished_ids) < len(parallel_tasks):
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=0.5)
                if event is not None:
                    yield event
            except TimeoutError:
                pass
            for i, pt in enumerate(parallel_tasks):
                if i not in finished_ids and pt.done():
                    finished_ids.add(i)
                    exc = pt.exception() if not pt.cancelled() else None
                    if exc is not None:
                        task_obj = claimed[i]
                        logger.error("Uncaught exception in task '%s': %s", task_obj.id, exc)
                        with contextlib.suppress(Exception):
                            await registry.tasks.update_task(
                                team_run_id, task_obj.id, status=TaskStatus.FAILED, result=str(exc)
                            )
                        await event_queue.put(
                            {"type": "task_failed", "agent": worker_name, "task_id": task_obj.id, "error": str(exc)}
                        )

        while not event_queue.empty():
            event = await event_queue.get()
            if event is not None:
                yield event
        await asyncio.gather(*parallel_tasks, return_exceptions=True)

    # ── Task execution ───────────────────────────────────────────────

    async def _execute_task(
        self, worker_spec: AgentSpec, team_spec: TeamSpec, team_run_id: str, task_id: str, title: str, description: str
    ) -> str:
        """Execute a task with provider overrides and session management."""
        prev_overrides = self._apply_overrides(worker_spec)
        try:
            return await self._execute_task_core(worker_spec, team_spec, team_run_id, task_id, title, description)
        finally:
            self._restore_overrides(prev_overrides)

    async def _execute_task_streaming(
        self,
        worker_spec: AgentSpec,
        team_spec: TeamSpec,
        team_run_id: str,
        task_id: str,
        title: str,
        description: str,
        event_queue: asyncio.Queue[dict[str, Any] | None],
        worker_name: str,
    ) -> str:
        """Like _execute_task but streams agent tokens/tools to event_queue."""
        prev_overrides = self._apply_overrides(worker_spec)
        session_ctx = await self._start_sessions(worker_spec)
        try:
            tools = self._build_worker_tools(worker_spec, team_spec, team_run_id, session_ctx)
            upstream = await self._gather_upstream_context(team_run_id, task_id)
            message = build_task_message(title, description, upstream)

            logger.info("Task '%s' starting streaming execution for '%s'", task_id, title)
            content = ""
            async for event in run_agent_with_tools_stream(
                worker_spec, message, tools, worker_name, max_turns=worker_spec.max_turns
            ):
                evt_type = event.get("type")
                if evt_type == "agent_token":
                    content += event.get("content", "")
                    await event_queue.put(
                        {
                            "type": "agent_token",
                            "agent": worker_name,
                            "task_id": task_id,
                            "content": event.get("content", ""),
                        }
                    )
                elif evt_type == "agent_tool":
                    await event_queue.put(
                        {"type": "agent_tool", "agent": worker_name, "task_id": task_id, "name": event.get("name", "")}
                    )
            logger.info("Task '%s' streaming execution complete, content_len=%d", task_id, len(content))
            return content
        finally:
            await self._stop_sessions(session_ctx)
            self._restore_overrides(prev_overrides)

    async def _execute_task_core(
        self, worker_spec: AgentSpec, team_spec: TeamSpec, team_run_id: str, task_id: str, title: str, description: str
    ) -> str:
        """Inner task execution with provider overrides already applied."""
        session_ctx = await self._start_sessions(worker_spec)
        try:
            tools = self._build_worker_tools(worker_spec, team_spec, team_run_id, session_ctx)
            upstream = await self._gather_upstream_context(team_run_id, task_id)
            message = build_task_message(title, description, upstream)
            return await run_agent_with_tools(worker_spec, message, tools, max_turns=worker_spec.max_turns)
        finally:
            await self._stop_sessions(session_ctx)

    # ── Phase 3: Synthesis ───────────────────────────────────────────

    async def _run_synthesizer(self, team_spec: TeamSpec, team_run_id: str, message: str) -> str:
        synth_spec = await self._get_agent(team_spec.synthesizer, "Synthesizer")
        prompt = await build_synthesis_prompt(team_run_id, message)
        tools = self._synthesizer_tools(team_run_id, team_spec.synthesizer)
        return await run_agent_with_tools(synth_spec, prompt, tools, max_turns=synth_spec.max_turns)

    async def _run_synthesizer_stream(
        self, team_spec: TeamSpec, team_run_id: str, message: str
    ) -> AsyncIterator[dict[str, Any]]:
        synth_spec = await self._get_agent(team_spec.synthesizer, "Synthesizer")
        prompt = await build_synthesis_prompt(team_run_id, message)
        tools = self._synthesizer_tools(team_run_id, team_spec.synthesizer)
        async for event in run_agent_with_tools_stream(
            synth_spec, prompt, tools, "synthesizer", max_turns=synth_spec.max_turns
        ):
            yield event

    def _synthesizer_tools(self, team_run_id: str, synth_name: str) -> list[ToolDefinition]:
        """Synthesizer gets read-only task board access."""
        primitives = {"task_board": PrimitiveConfig(enabled=True, tools=["list_tasks", "get_task"])}
        return build_tool_list(primitives, namespace="__synthesizer__", team_run_id=team_run_id, agent_name=synth_name)

    # ── Helpers ──────────────────────────────────────────────────────

    async def _get_agent(self, name: str, role: str) -> AgentSpec:
        """Load an agent spec by name, raising if not found."""
        spec = await self._agent_store.get(name)  # type: ignore[union-attr]
        if spec is None:
            raise ValueError(f"{role} agent '{name}' not found")
        return spec

    def _worker_names(self, team_spec: TeamSpec) -> list[str]:
        max_concurrent = team_spec.max_concurrent or len(team_spec.workers)
        return team_spec.workers[:max_concurrent]

    @staticmethod
    async def _has_incomplete_tasks(team_run_id: str) -> bool:
        all_tasks = await registry.tasks.list_tasks(team_run_id)
        return any(t.status in (TaskStatus.PENDING, TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS) for t in all_tasks)

    @staticmethod
    async def _claim_batch(team_run_id: str, available: list[Any], worker_name: str) -> list[Any]:
        """Atomically claim all available tasks, returning those successfully claimed."""
        claimed = []
        for task in available:
            result = await registry.tasks.claim_task(team_run_id, task.id, worker_name)
            if result is not None:
                claimed.append(task)
        return claimed

    @staticmethod
    def _apply_overrides(spec: AgentSpec) -> dict[str, str]:
        """Save current provider overrides and apply the agent's overrides."""
        prev: dict[str, str] = {}
        for prim in Primitive:
            val = get_provider_override(prim)
            if val:
                prev[prim] = val
        if spec.provider_overrides:
            merged = {**prev, **spec.provider_overrides}
            set_provider_overrides(merged)
        return prev

    @staticmethod
    def _restore_overrides(prev: dict[str, str]) -> None:
        set_provider_overrides(prev)

    async def _start_sessions(self, worker_spec: AgentSpec) -> dict[str, str]:
        """Start browser/code_interpreter sessions if the worker uses them."""
        session_ctx: dict[str, str] = {}
        for prim_name, config in worker_spec.primitives.items():
            if not config.enabled:
                continue
            try:
                if prim_name == "code_interpreter":
                    result = await registry.code_interpreter.start_session()
                    session_ctx["code_interpreter"] = result.get("session_id", uuid.uuid4().hex[:16])
                elif prim_name == "browser":
                    result = await registry.browser.start_session()
                    session_ctx["browser"] = result.get("session_id", uuid.uuid4().hex[:16])
                else:
                    continue
                logger.info("Started %s session: %s", prim_name, session_ctx[prim_name])
                if self._session_registry:
                    await self._session_registry.register(prim_name, session_ctx[prim_name])
            except Exception:
                logger.warning("Failed to start %s session", prim_name, exc_info=True)
                session_ctx[prim_name] = uuid.uuid4().hex[:16]
        return session_ctx

    async def _stop_sessions(self, session_ctx: dict[str, str]) -> None:
        for prim_name, sid in session_ctx.items():
            with contextlib.suppress(Exception):
                if prim_name == "browser":
                    await registry.browser.stop_session(session_id=sid)
                elif prim_name == "code_interpreter":
                    await registry.code_interpreter.stop_session(session_id=sid)
            if self._session_registry:
                with contextlib.suppress(Exception):
                    await self._session_registry.unregister(prim_name, sid)

    def _build_worker_tools(
        self, worker_spec: AgentSpec, team_spec: TeamSpec, team_run_id: str, session_ctx: dict[str, str]
    ) -> list[ToolDefinition]:
        """Build the full tool list for a worker: its primitives + task board."""
        worker_primitives = dict(worker_spec.primitives)
        worker_primitives["task_board"] = PrimitiveConfig(enabled=True, tools=_WORKER_BOARD_TOOLS)
        return build_tool_list(
            worker_primitives,
            namespace=f"team:{team_spec.name}:{team_run_id}",
            session_ctx=session_ctx,
            team_run_id=team_run_id,
            agent_name=worker_spec.name,
        )

    @staticmethod
    async def _gather_upstream_context(team_run_id: str, task_id: str) -> str:
        """Collect results from tasks that this task depends on."""
        task = await registry.tasks.get_task(team_run_id, task_id)
        if task is None or not task.depends_on:
            return ""
        parts = []
        for dep_id in task.depends_on:
            dep = await registry.tasks.get_task(team_run_id, dep_id)
            if dep and dep.result:
                parts.append(f"[Task: {dep.title}]\n{dep.result}")
        return "\n\n".join(parts)
