"""Team runner — orchestrates multi-agent team execution.

Three phases:
  1. Planning:   A planner agent decomposes the prompt into tasks.
  2. Execution:  Worker agents concurrently claim and complete tasks.
  3. Synthesis:  A synthesizer agent reads all results and produces a response.
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
from agentic_primitives_gateway.agents.team_store import TeamStore
from agentic_primitives_gateway.agents.tools import ToolDefinition, build_tool_list, execute_tool, to_gateway_tools
from agentic_primitives_gateway.context import get_provider_override, set_provider_overrides
from agentic_primitives_gateway.models.agents import AgentSpec, PrimitiveConfig
from agentic_primitives_gateway.models.enums import Primitive
from agentic_primitives_gateway.models.tasks import TaskStatus
from agentic_primitives_gateway.models.teams import TeamRunPhase, TeamRunResponse, TeamSpec
from agentic_primitives_gateway.registry import registry

logger = logging.getLogger(__name__)

# How long a worker waits before re-checking the board (seconds)
_POLL_INTERVAL = 1.0


class TeamRunner:
    """Orchestrates a team of agents working off a shared task board."""

    def __init__(self) -> None:
        self._agent_store: AgentStore | None = None
        self._team_store: TeamStore | None = None
        self._agent_runner: AgentRunner | None = None

    def set_stores(
        self,
        agent_store: AgentStore,
        team_store: TeamStore,
        agent_runner: AgentRunner,
    ) -> None:
        self._agent_store = agent_store
        self._team_store = team_store
        self._agent_runner = agent_runner

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

        # Phase 1: Initial planning
        logger.info("Team[%s] run=%s phase=planning", team_spec.name, team_run_id)
        await self._run_planner(team_spec, team_run_id, message)

        # Phase 2: Execution with continuous re-planning
        logger.info("Team[%s] run=%s phase=execution", team_spec.name, team_run_id)
        workers_used = await self._run_workers_with_replanning(team_spec, team_run_id, message)

        # Phase 3: Synthesis
        logger.info("Team[%s] run=%s phase=synthesis", team_spec.name, team_run_id)
        response = await self._run_synthesizer(team_spec, team_run_id, message)

        # Collect stats
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

        # Report initial tasks
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
        async for event in self._run_workers_with_replanning_stream(team_spec, team_run_id, message):
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

    async def _run_planner(
        self,
        team_spec: TeamSpec,
        team_run_id: str,
        message: str,
    ) -> None:
        planner_spec = await self._agent_store.get(team_spec.planner)  # type: ignore[union-attr]
        if planner_spec is None:
            raise ValueError(f"Planner agent '{team_spec.planner}' not found")

        planner_prompt = await self._build_planner_prompt(team_spec, message)
        planner_tools = self._build_planner_tools(team_run_id, team_spec.planner)

        await self._run_agent_with_tools(planner_spec, planner_prompt, planner_tools, max_turns=planner_spec.max_turns)

    async def _run_planner_stream(
        self,
        team_spec: TeamSpec,
        team_run_id: str,
        message: str,
    ) -> AsyncIterator[dict[str, Any]]:
        planner_spec = await self._agent_store.get(team_spec.planner)  # type: ignore[union-attr]
        if planner_spec is None:
            raise ValueError(f"Planner agent '{team_spec.planner}' not found")

        planner_prompt = await self._build_planner_prompt(team_spec, message)
        planner_tools = self._build_planner_tools(team_run_id, team_spec.planner)
        logger.info("Planner prompt:\n%s", planner_prompt)

        async for event in self._run_agent_with_tools_stream(
            planner_spec, planner_prompt, planner_tools, "planner", max_turns=planner_spec.max_turns
        ):
            yield event

    async def _build_planner_prompt(self, team_spec: TeamSpec, message: str) -> str:
        worker_descriptions = []
        for w in team_spec.workers:
            spec = await self._agent_store.get(w)  # type: ignore[union-attr]
            desc = f"  - {w}"
            if spec and spec.description:
                desc += f": {spec.description}"
            prims = []
            if spec:
                prims = [p for p, c in spec.primitives.items() if c.enabled]
            if prims:
                desc += f" (capabilities: {', '.join(prims)})"
            worker_descriptions.append(desc)
        worker_list = "\n".join(worker_descriptions)
        return (
            f"You are a task planner. Decompose the following request into concrete, "
            f"actionable tasks that can be worked on by team members.\n\n"
            f"Available team members:\n{worker_list}\n\n"
            f"Guidelines:\n"
            f"- ALWAYS set assigned_to to the name of the best worker for each task\n"
            f"- Only create tasks where you can write a SPECIFIC, actionable description right now\n"
            f"- Do NOT create tasks that depend on unknown information (e.g. 'implement the frameworks' when "
            f"you don't know which frameworks yet). Those tasks will be created automatically by re-planning "
            f"after the research results come back.\n"
            f"- Each task should produce a complete, self-contained deliverable\n"
            f"- Use dependencies (depends_on with task IDs) when a task needs results from another\n"
            f"- Use priority to indicate importance (higher = more important)\n\n"
            f"Use the create_task tool to add each task to the board.\n\n"
            f"Request: {message}"
        )

    def _build_planner_tools(self, team_run_id: str, planner_name: str) -> list[ToolDefinition]:
        """Build tools available to the planner: create_task and list_tasks only."""
        primitives = {"task_board": PrimitiveConfig(enabled=True, tools=["create_task", "list_tasks"])}
        return build_tool_list(
            primitives,
            namespace="__planner__",
            team_run_id=team_run_id,
            agent_name=planner_name,
        )

    # ── Continuous re-planning ─────────────────────────────────────

    async def _build_replan_prompt(self, team_spec: TeamSpec, team_run_id: str, original_message: str) -> str | None:
        """Build a re-planning prompt based on newly completed tasks. Returns None if no re-planning needed."""
        all_tasks = await registry.tasks.list_tasks(team_run_id)
        completed = [t for t in all_tasks if t.status == TaskStatus.DONE]
        pending_or_active = [
            t for t in all_tasks if t.status in (TaskStatus.PENDING, TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS)
        ]

        if not completed:
            return None

        worker_descriptions = []
        for w in set(team_spec.workers):
            spec = await self._agent_store.get(w)  # type: ignore[union-attr]
            desc = f"  - {w}"
            if spec and spec.description:
                desc += f": {spec.description}"
            prims = []
            if spec:
                prims = [p for p, c in spec.primitives.items() if c.enabled]
            if prims:
                desc += f" (capabilities: {', '.join(prims)})"
            worker_descriptions.append(desc)
        worker_list = "\n".join(worker_descriptions)

        completed_summary = []
        for t in completed:
            result_preview = (t.result or "")[:500]
            completed_summary.append(
                f"  - [{t.id}] {t.title} (assigned_to: {t.assigned_to})\n    Result: {result_preview}"
            )

        pending_summary = []
        for t in pending_or_active:
            pending_summary.append(
                f"  - [{t.id}] {t.title} (status: {t.status}, assigned_to: {t.suggested_worker or t.assigned_to or 'unassigned'})"
            )

        return (
            f"You are a task planner reviewing progress on a team request.\n\n"
            f"Original request: {original_message}\n\n"
            f"Available team members:\n{worker_list}\n\n"
            f"Completed tasks:\n"
            + "\n".join(completed_summary)
            + "\n\n"
            + (
                "Pending/active tasks:\n" + "\n".join(pending_summary) + "\n\n"
                if pending_summary
                else "No pending tasks.\n\n"
            )
            + "Based on the completed task results, do any NEW follow-up tasks need to be created?\n\n"
            "Guidelines:\n"
            "- Review the completed results carefully — if they reveal specific items "
            "(e.g., specific framework names, specific topics), create NEW tasks with those specific details\n"
            "- ALWAYS set assigned_to to the appropriate worker\n"
            "- Set depends_on if the new task needs results from a specific completed task\n"
            "- Do NOT recreate tasks that already exist (completed or pending)\n"
            "- If no new tasks are needed, just respond with text explaining why — do NOT call create_task\n"
            "- Think about whether the original request has been fully addressed by existing tasks\n\n"
            "Use the create_task tool ONLY if new tasks are needed."
        )

    async def _run_replanner(
        self,
        team_spec: TeamSpec,
        team_run_id: str,
        original_message: str,
    ) -> int:
        """Run the planner to evaluate completed tasks and create follow-ups. Returns count of new tasks created."""
        planner_spec = await self._agent_store.get(team_spec.planner)  # type: ignore[union-attr]
        if planner_spec is None:
            return 0

        prompt = await self._build_replan_prompt(team_spec, team_run_id, original_message)
        if prompt is None:
            return 0

        tasks_before = await registry.tasks.list_tasks(team_run_id)
        count_before = len(tasks_before)

        planner_tools = self._build_planner_tools(team_run_id, team_spec.planner)
        await self._run_agent_with_tools(planner_spec, prompt, planner_tools, max_turns=planner_spec.max_turns)

        tasks_after = await registry.tasks.list_tasks(team_run_id)
        new_count = len(tasks_after) - count_before
        if new_count > 0:
            logger.info("Re-planner created %d new tasks", new_count)
        return new_count

    async def _run_replanner_stream(
        self,
        team_spec: TeamSpec,
        team_run_id: str,
        original_message: str,
        event_queue: asyncio.Queue[dict[str, Any] | None],
    ) -> int:
        """Streaming re-planner. Puts events on the queue. Returns count of new tasks."""
        planner_spec = await self._agent_store.get(team_spec.planner)  # type: ignore[union-attr]
        if planner_spec is None:
            return 0

        prompt = await self._build_replan_prompt(team_spec, team_run_id, original_message)
        if prompt is None:
            return 0

        tasks_before = await registry.tasks.list_tasks(team_run_id)
        count_before = len(tasks_before)

        planner_tools = self._build_planner_tools(team_run_id, team_spec.planner)
        logger.info("Re-planner evaluating completed tasks...")

        await event_queue.put({"type": "phase_change", "phase": "replanning"})

        async for event in self._run_agent_with_tools_stream(
            planner_spec, prompt, planner_tools, "planner", max_turns=planner_spec.max_turns
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

    async def _run_workers_with_replanning(
        self,
        team_spec: TeamSpec,
        team_run_id: str,
        original_message: str,
    ) -> list[str]:
        """Run workers and re-plan after each wave of completions."""
        max_concurrent = team_spec.max_concurrent or len(team_spec.workers)
        workers = team_spec.workers[:max_concurrent]
        reviewed_tasks: set[str] = set()
        workers_used: list[str] = []

        while True:
            # Run workers until board is idle
            worker_tasks = [self._worker_loop(worker_name, team_spec, team_run_id) for worker_name in workers]
            results = await asyncio.gather(*worker_tasks, return_exceptions=True)
            for name, result in zip(workers, results, strict=False):
                if not isinstance(result, Exception) and name not in workers_used:
                    workers_used.append(name)

            # Check for newly completed tasks
            all_tasks = await registry.tasks.list_tasks(team_run_id)
            newly_completed = [t for t in all_tasks if t.status == TaskStatus.DONE and t.id not in reviewed_tasks]
            if not newly_completed:
                break
            for t in newly_completed:
                reviewed_tasks.add(t.id)

            # Re-plan
            new_count = await self._run_replanner(team_spec, team_run_id, original_message)
            if new_count == 0:
                break
            # Loop back — workers will pick up new tasks

        return workers_used

    async def _run_workers_with_replanning_stream(
        self,
        team_spec: TeamSpec,
        team_run_id: str,
        original_message: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """Streaming execution with continuous re-planning."""
        max_concurrent = team_spec.max_concurrent or len(team_spec.workers)
        workers = team_spec.workers[:max_concurrent]
        reviewed_tasks: set[str] = set()

        while True:
            # Run workers and planner concurrently, merging events
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
                    await _q.put(None)

            worker_tasks = [asyncio.create_task(_worker_wrapper(w)) for w in workers]

            finished = 0
            while finished < active_count:
                event = await queue.get()
                if event is None:
                    finished += 1
                    continue
                yield event

            # All sentinels received — workers are done. Fire-and-forget cleanup.
            for wt in worker_tasks:
                if not wt.done():
                    wt.cancel()

            logger.info("All workers finished, checking for re-planning...")

            # Check for newly completed tasks to review
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

            # Re-plan: run planner to create follow-up tasks
            replan_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
            new_count = await self._run_replanner_stream(team_spec, team_run_id, original_message, replan_queue)

            # Drain re-planner events
            while not replan_queue.empty():
                yield await replan_queue.get()

            logger.info("Re-planner created %d new tasks", new_count)
            if new_count == 0:
                break
            # Loop back — workers will pick up the new tasks
            logger.info("Re-planning created %d tasks, restarting workers", new_count)

    # ── Phase 2: Worker execution ────────────────────────────────────

    async def _run_workers(
        self,
        team_spec: TeamSpec,
        team_run_id: str,
    ) -> list[str]:
        max_concurrent = team_spec.max_concurrent or len(team_spec.workers)
        workers = team_spec.workers[:max_concurrent]

        worker_tasks = [self._worker_loop(worker_name, team_spec, team_run_id) for worker_name in workers]

        results = await asyncio.gather(*worker_tasks, return_exceptions=True)
        workers_used = []
        for name, result in zip(workers, results, strict=False):
            if isinstance(result, Exception):
                logger.error("Team[%s] worker '%s' failed: %s", team_spec.name, name, result)
            else:
                workers_used.append(name)
        return workers_used

    async def _run_workers_stream(
        self,
        team_spec: TeamSpec,
        team_run_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        max_concurrent = team_spec.max_concurrent or len(team_spec.workers)
        workers = team_spec.workers[:max_concurrent]

        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        active_count = len(workers)

        async def _worker_wrapper(worker_name: str) -> None:
            try:
                async for event in self._worker_loop_stream(worker_name, team_spec, team_run_id):
                    await queue.put(event)
            except Exception as e:
                await queue.put({"type": "worker_error", "agent": worker_name, "error": str(e)})
            finally:
                await queue.put(None)  # sentinel

        tasks = [asyncio.create_task(_worker_wrapper(w)) for w in workers]

        finished = 0
        while finished < active_count:
            event = await queue.get()
            if event is None:
                finished += 1
                continue
            yield event

        # Ensure all tasks complete
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _worker_loop(
        self,
        worker_name: str,
        team_spec: TeamSpec,
        team_run_id: str,
    ) -> None:
        worker_spec = await self._agent_store.get(worker_name)  # type: ignore[union-attr]
        if worker_spec is None:
            logger.warning("Worker agent '%s' not found — skipping", worker_name)
            return

        while True:
            available = await registry.tasks.get_available(team_run_id, worker_name=worker_name)
            if not available:
                # Check if the entire board is done
                all_tasks = await registry.tasks.list_tasks(team_run_id)
                incomplete = [
                    t for t in all_tasks if t.status in (TaskStatus.PENDING, TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS)
                ]
                if not incomplete:
                    break
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            # Claim all available tasks and run them in parallel
            claimed_tasks = []
            for task in available:
                claimed = await registry.tasks.claim_task(team_run_id, task.id, worker_name)
                if claimed is not None:
                    claimed_tasks.append(task)

            if not claimed_tasks:
                continue

            async def _run_one(t: Any) -> None:
                logger.info("Team[%s] worker '%s' claimed task '%s': %s", team_spec.name, worker_name, t.id, t.title)
                await registry.tasks.update_task(team_run_id, t.id, status=TaskStatus.IN_PROGRESS)
                try:
                    result = await self._execute_task(worker_spec, team_spec, team_run_id, t.id, t.title, t.description)
                    await registry.tasks.update_task(team_run_id, t.id, status=TaskStatus.DONE, result=result)
                    logger.info("Team[%s] worker '%s' completed task '%s'", team_spec.name, worker_name, t.id)
                except Exception as e:
                    logger.error(
                        "Team[%s] worker '%s' failed task '%s': %s: %s",
                        team_spec.name,
                        worker_name,
                        t.id,
                        type(e).__name__,
                        e,
                    )
                    try:
                        await registry.tasks.update_task(team_run_id, t.id, status=TaskStatus.FAILED, result=str(e))
                    except Exception:
                        logger.error("Failed to mark task '%s' as failed", t.id)

            await asyncio.gather(*[_run_one(t) for t in claimed_tasks], return_exceptions=True)

    async def _worker_loop_stream(
        self,
        worker_name: str,
        team_spec: TeamSpec,
        team_run_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        worker_spec = await self._agent_store.get(worker_name)  # type: ignore[union-attr]
        if worker_spec is None:
            yield {"type": "worker_error", "agent": worker_name, "error": f"Agent '{worker_name}' not found"}
            return

        yield {"type": "worker_start", "agent": worker_name}
        while True:
            available = await registry.tasks.get_available(team_run_id, worker_name=worker_name)
            if not available:
                all_tasks = await registry.tasks.list_tasks(team_run_id)
                incomplete = [
                    t for t in all_tasks if t.status in (TaskStatus.PENDING, TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS)
                ]
                if not incomplete:
                    logger.info("Worker '%s': no incomplete tasks, exiting", worker_name)
                    break
                logger.info(
                    "Worker '%s': waiting — %d incomplete tasks (%s)",
                    worker_name,
                    len(incomplete),
                    ", ".join(f"{t.id}:{t.status}:sw={t.suggested_worker}" for t in incomplete),
                )
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            # Claim all available tasks
            claimed_tasks = []
            for task in available:
                claimed = await registry.tasks.claim_task(team_run_id, task.id, worker_name)
                if claimed is not None:
                    claimed_tasks.append(task)

            if not claimed_tasks:
                continue

            # Run claimed tasks in parallel, merging events via a queue
            event_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

            async def _run_one_stream(t: Any, _q: asyncio.Queue[dict[str, Any] | None] = event_queue) -> None:
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
                    try:
                        await registry.tasks.update_task(team_run_id, t.id, status=TaskStatus.FAILED, result=str(e))
                    except Exception:
                        logger.error("Failed to mark task '%s' as failed", t.id)
                    await _q.put({"type": "task_failed", "agent": worker_name, "task_id": t.id, "error": str(e)})

            parallel_tasks = [asyncio.create_task(_run_one_stream(t)) for t in claimed_tasks]

            # Yield events as they arrive until all parallel tasks finish
            finished = set()
            while len(finished) < len(parallel_tasks):
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=0.5)
                    yield event
                except TimeoutError:
                    pass
                # Check which tasks are done
                for i, pt in enumerate(parallel_tasks):
                    if i not in finished and pt.done():
                        finished.add(i)
                        # If the task raised an exception that wasn't caught, mark it failed
                        exc = pt.exception() if not pt.cancelled() else None
                        if exc is not None:
                            task_obj = claimed_tasks[i]
                            logger.error("Uncaught exception in parallel task '%s': %s", task_obj.id, exc)
                            with contextlib.suppress(Exception):
                                await registry.tasks.update_task(
                                    team_run_id, task_obj.id, status=TaskStatus.FAILED, result=str(exc)
                                )
                            await event_queue.put(
                                {"type": "task_failed", "agent": worker_name, "task_id": task_obj.id, "error": str(exc)}
                            )

            # Drain remaining events
            while not event_queue.empty():
                yield await event_queue.get()

            await asyncio.gather(*parallel_tasks, return_exceptions=True)

        yield {"type": "worker_done", "agent": worker_name}

    @staticmethod
    def _apply_overrides(spec: AgentSpec) -> dict[str, str]:
        """Apply agent's provider overrides, returning previous state."""
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

    async def _execute_task(
        self,
        worker_spec: AgentSpec,
        team_spec: TeamSpec,
        team_run_id: str,
        task_id: str,
        title: str,
        description: str,
    ) -> str:
        """Run a single task using the worker's agent spec and tools."""
        prev_overrides = self._apply_overrides(worker_spec)
        try:
            return await self._execute_task_inner(worker_spec, team_spec, team_run_id, task_id, title, description)
        finally:
            self._restore_overrides(prev_overrides)

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
            except Exception:
                logger.warning("Failed to start %s session", prim_name, exc_info=True)
                session_ctx[prim_name] = uuid.uuid4().hex[:16]
        return session_ctx

    async def _stop_sessions(self, session_ctx: dict[str, str]) -> None:
        """Stop any sessions that were started."""
        for prim_name, sid in session_ctx.items():
            try:
                if prim_name == "browser":
                    await registry.browser.stop_session(session_id=sid)
                elif prim_name == "code_interpreter":
                    await registry.code_interpreter.stop_session(session_id=sid)
            except Exception:
                pass

    def _build_worker_primitives(self, worker_spec: AgentSpec) -> dict[str, PrimitiveConfig]:
        worker_primitives = dict(worker_spec.primitives)
        worker_primitives["task_board"] = PrimitiveConfig(
            enabled=True,
            tools=[
                "list_tasks",
                "get_task",
                "complete_task",
                "fail_task",
                "add_task_note",
                "get_available_tasks",
                "create_task",
            ],
        )
        return worker_primitives

    def _build_task_message(self, title: str, description: str, upstream_context: str) -> str:
        msg = f"You are working as part of a team. Your task:\n\nTitle: {title}\nDescription: {description}\n"
        if upstream_context:
            msg += f"\nContext from completed upstream tasks:\n{upstream_context}\n"
        msg += (
            "\nComplete this task thoroughly using your available tools. "
            "Your final response will be recorded as the task result and shared with "
            "the team, so make it detailed and self-contained. Include all relevant "
            "information, code, findings, or analysis in your response."
        )
        return msg

    async def _execute_task_inner(
        self,
        worker_spec: AgentSpec,
        team_spec: TeamSpec,
        team_run_id: str,
        task_id: str,
        title: str,
        description: str,
    ) -> str:
        """Inner task execution with provider overrides already applied."""
        session_ctx = await self._start_sessions(worker_spec)
        try:
            worker_primitives = self._build_worker_primitives(worker_spec)
            upstream_context = await self._gather_upstream_context(team_run_id, task_id)
            task_message = self._build_task_message(title, description, upstream_context)

            tools = build_tool_list(
                worker_primitives,
                namespace=f"team:{team_spec.name}:{team_run_id}",
                session_ctx=session_ctx,
                team_run_id=team_run_id,
                agent_name=worker_spec.name,
            )

            return await self._run_agent_with_tools(worker_spec, task_message, tools, max_turns=worker_spec.max_turns)
        finally:
            await self._stop_sessions(session_ctx)

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
            worker_primitives = self._build_worker_primitives(worker_spec)
            upstream_context = await self._gather_upstream_context(team_run_id, task_id)
            task_message = self._build_task_message(title, description, upstream_context)

            tools = build_tool_list(
                worker_primitives,
                namespace=f"team:{team_spec.name}:{team_run_id}",
                session_ctx=session_ctx,
                team_run_id=team_run_id,
                agent_name=worker_spec.name,
            )

            # Use streaming agent helper, forwarding token events to the queue
            logger.info("Task '%s' starting streaming execution for '%s'", task_id, title)
            content = ""
            async for event in self._run_agent_with_tools_stream(
                worker_spec, task_message, tools, worker_name, max_turns=worker_spec.max_turns
            ):
                evt_type = event.get("type")
                if evt_type == "agent_token":
                    content += event.get("content", "")
                    # Forward to UI without internal fields
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

    async def _gather_upstream_context(self, team_run_id: str, task_id: str) -> str:
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

    # ── Phase 3: Synthesis ───────────────────────────────────────────

    async def _run_synthesizer(
        self,
        team_spec: TeamSpec,
        team_run_id: str,
        original_message: str,
    ) -> str:
        synth_spec = await self._agent_store.get(team_spec.synthesizer)  # type: ignore[union-attr]
        if synth_spec is None:
            raise ValueError(f"Synthesizer agent '{team_spec.synthesizer}' not found")

        synth_prompt = await self._build_synthesis_prompt(team_run_id, original_message)
        # Synthesizer gets read-only task board access
        synth_tools = self._build_synthesizer_tools(team_run_id, team_spec.synthesizer)

        return await self._run_agent_with_tools(synth_spec, synth_prompt, synth_tools, max_turns=synth_spec.max_turns)

    async def _run_synthesizer_stream(
        self,
        team_spec: TeamSpec,
        team_run_id: str,
        original_message: str,
    ) -> AsyncIterator[dict[str, Any]]:
        synth_spec = await self._agent_store.get(team_spec.synthesizer)  # type: ignore[union-attr]
        if synth_spec is None:
            raise ValueError(f"Synthesizer agent '{team_spec.synthesizer}' not found")

        synth_prompt = await self._build_synthesis_prompt(team_run_id, original_message)
        synth_tools = self._build_synthesizer_tools(team_run_id, team_spec.synthesizer)

        async for event in self._run_agent_with_tools_stream(
            synth_spec, synth_prompt, synth_tools, "synthesizer", max_turns=synth_spec.max_turns
        ):
            yield event

    async def _build_synthesis_prompt(self, team_run_id: str, original_message: str) -> str:
        all_tasks = await registry.tasks.list_tasks(team_run_id)
        task_summary = []
        for t in all_tasks:
            status_icon = {"done": "OK", "failed": "FAILED"}.get(t.status, t.status)
            result_text = f"\n  Result: {t.result}" if t.result else ""
            notes_text = ""
            if t.notes:
                notes_text = "\n  Notes:\n" + "\n".join(f"    [{n.agent}]: {n.content}" for n in t.notes)
            task_summary.append(f"- [{status_icon}] {t.title}{result_text}{notes_text}")

        return (
            f"You are a synthesizer. The team has completed work on the following request:\n\n"
            f"Original request: {original_message}\n\n"
            f"Task results:\n" + "\n".join(task_summary) + "\n\n"
            "Synthesize ALL of these results into a single coherent, comprehensive "
            "response that fully addresses the original request. Include all code, "
            "findings, and details from each task. Do not omit any task results. "
            "If you need more detail on any task, use the get_task tool to retrieve it."
        )

    def _build_synthesizer_tools(self, team_run_id: str, synth_name: str) -> list[ToolDefinition]:
        """Synthesizer gets read-only task board access."""
        primitives = {"task_board": PrimitiveConfig(enabled=True, tools=["list_tasks", "get_task"])}
        return build_tool_list(
            primitives,
            namespace="__synthesizer__",
            team_run_id=team_run_id,
            agent_name=synth_name,
        )

    # ── Agent execution helpers ──────────────────────────────────────

    async def _run_agent_with_tools(
        self,
        spec: AgentSpec,
        message: str,
        tools: list[ToolDefinition],
        max_turns: int = 20,
    ) -> str:
        """Run an agent's LLM loop with a specific set of tools."""
        gateway_tools = to_gateway_tools(tools) if tools else None
        messages: list[dict[str, Any]] = [{"role": "user", "content": message}]
        content = ""

        for _turn in range(max_turns):
            request_dict: dict[str, Any] = {
                "model": spec.model,
                "messages": messages,
                "system": spec.system_prompt,
                "temperature": spec.temperature,
            }
            if spec.max_tokens is not None:
                request_dict["max_tokens"] = spec.max_tokens
            if gateway_tools:
                request_dict["tools"] = gateway_tools

            response = await registry.gateway.route_request(request_dict)
            stop_reason = response.get("stop_reason", "end_turn")
            tool_calls = response.get("tool_calls")
            turn_content = response.get("content", "")
            if turn_content:
                content = turn_content

            logger.info(
                "Agent[%s] turn %d: stop=%s, tool_calls=%d, content_len=%d",
                spec.name,
                _turn + 1,
                stop_reason,
                len(tool_calls) if tool_calls else 0,
                len(turn_content),
            )

            if stop_reason != "tool_use" or not tool_calls:
                messages.append({"role": "assistant", "content": content})
                break

            messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

            # Execute tools
            results = []
            for tc in tool_calls:
                logger.info("Agent[%s] tool call: %s(%s)", spec.name, tc["name"], str(tc.get("input", {}))[:200])
                try:
                    result = await execute_tool(tc["name"], tc.get("input", {}), tools)
                except Exception as e:
                    result = f"Error: {e}"
                    logger.error("Agent[%s] tool error: %s — %s", spec.name, tc["name"], e)
                results.append({"tool_use_id": tc["id"], "content": result})
            messages.append({"role": "user", "tool_results": results})

        return content

    async def _run_agent_with_tools_stream(
        self,
        spec: AgentSpec,
        message: str,
        tools: list[ToolDefinition],
        role_label: str,
        max_turns: int = 20,
    ) -> AsyncIterator[dict[str, Any]]:
        """Streaming version of _run_agent_with_tools. Yields SSE events."""
        gateway_tools = to_gateway_tools(tools) if tools else None
        messages: list[dict[str, Any]] = [{"role": "user", "content": message}]
        content = ""

        for _turn in range(max_turns):
            request_dict: dict[str, Any] = {
                "model": spec.model,
                "messages": messages,
                "system": spec.system_prompt,
                "temperature": spec.temperature,
            }
            if spec.max_tokens is not None:
                request_dict["max_tokens"] = spec.max_tokens
            if gateway_tools:
                request_dict["tools"] = gateway_tools

            logger.info(
                "Agent[%s] stream turn %d: %d messages, %d tools",
                spec.name,
                _turn + 1,
                len(messages),
                len(gateway_tools) if gateway_tools else 0,
            )

            # Stream LLM response
            turn_content = ""
            tool_calls: list[dict[str, Any]] = []
            stop_reason = "end_turn"

            async for chunk in registry.gateway.route_request_stream(request_dict):
                event_type = chunk.get("type", "")
                if event_type == "content_delta":
                    delta = chunk.get("delta", "")
                    turn_content += delta
                    yield {"type": "agent_token", "agent": role_label, "content": delta, "_accumulated": turn_content}
                elif event_type == "tool_use_start":
                    tool_calls.append({"id": chunk["id"], "name": chunk["name"], "input": {}})
                    yield {"type": "agent_tool", "agent": role_label, "name": chunk["name"]}
                elif event_type == "tool_use_delta":
                    if tool_calls:
                        # Accumulate input JSON
                        pass
                elif event_type == "tool_use_complete":
                    if tool_calls:
                        tool_calls[-1]["input"] = chunk.get("input", {})
                elif event_type == "message_stop":
                    stop_reason = chunk.get("stop_reason", "end_turn")

            if turn_content:
                content = turn_content

            logger.info(
                "Agent[%s] stream turn %d done: stop=%s, tool_calls=%d",
                spec.name,
                _turn + 1,
                stop_reason,
                len(tool_calls),
            )

            if stop_reason != "tool_use" or not tool_calls:
                messages.append({"role": "assistant", "content": content})
                break

            messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

            results = []
            for tc in tool_calls:
                logger.info("Agent[%s] stream tool: %s(%s)", spec.name, tc["name"], str(tc.get("input", {}))[:200])
                try:
                    result = await execute_tool(tc["name"], tc.get("input", {}), tools)
                except Exception as e:
                    result = f"Error: {e}"
                    logger.error("Agent[%s] stream tool error: %s — %s", spec.name, tc["name"], e)
                results.append({"tool_use_id": tc["id"], "content": result})
            messages.append({"role": "user", "tool_results": results})

        return
