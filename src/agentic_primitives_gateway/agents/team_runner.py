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
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

from agentic_primitives_gateway import metrics
from agentic_primitives_gateway.agents.checkpoint_utils import (
    apply_provider_overrides,
    restore_auth_context,
    restore_provider_overrides,
    serialize_auth_context,
)
from agentic_primitives_gateway.agents.runner import AgentRunner, _session_ownership_store
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
from agentic_primitives_gateway.audit.emit import emit_audit_event
from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType
from agentic_primitives_gateway.context import get_authenticated_principal
from agentic_primitives_gateway.models.agents import AgentSpec, PrimitiveConfig
from agentic_primitives_gateway.models.tasks import TaskStatus
from agentic_primitives_gateway.models.teams import TeamRunPhase, TeamRunResponse, TeamSpec
from agentic_primitives_gateway.primitives.browser.context import (
    reset_browser_session_id,
    set_browser_session_id,
)
from agentic_primitives_gateway.primitives.code_interpreter.context import (
    reset_code_interpreter_session_id,
    set_code_interpreter_session_id,
)
from agentic_primitives_gateway.primitives.knowledge.context import (
    reset_knowledge_namespace,
    set_knowledge_namespace,
)
from agentic_primitives_gateway.primitives.memory.context import (
    reset_memory_namespace,
    reset_shared_memory_namespace,
    set_memory_namespace,
    set_shared_memory_namespace,
)
from agentic_primitives_gateway.primitives.tasks.context import (
    reset_agent_role,
    reset_team_run_id,
    set_agent_role,
    set_team_run_id,
)
from agentic_primitives_gateway.registry import registry

logger = logging.getLogger(__name__)


def _emit_team_event(action: str, outcome: AuditOutcome, team_name: str, team_run_id: str) -> None:
    """Shorthand for team run lifecycle emits."""
    emit_audit_event(
        action=action,
        outcome=outcome,
        resource_type=ResourceType.TEAM,
        resource_id=team_name,
        metadata={"team_run_id": team_run_id},
    )


async def _resolve_team_agent(store: Any, team_owner_id: str, ref: str) -> Any:
    """Resolve a team-scope agent reference.

    Qualified ``owner:name`` goes straight to ``resolve_qualified``.
    Bare refs resolve in the *team owner's* namespace first, then fall
    back to ``system`` — so the team behaves consistently regardless of
    who ran it.  See ``docs/concepts/team-versioning.md``.

    Applies ``check_access`` against the calling principal to prevent
    cross-tenant escalation through crafted worker references.
    """
    from agentic_primitives_gateway.auth.access import check_access
    from agentic_primitives_gateway.context import get_authenticated_principal

    if ":" in ref:
        owner_id, _, bare = ref.partition(":")
        spec = await store.resolve_qualified(owner_id, bare)
    else:
        spec = await store.resolve_qualified(team_owner_id, ref)
        if spec is None and team_owner_id != "system":
            spec = await store.resolve_qualified("system", ref)

    # Enforce access: the calling user must be allowed to reach this spec
    if spec is not None:
        principal = get_authenticated_principal()
        if principal is None:
            return None
        spec_owner = getattr(spec, "owner_id", "system")
        spec_shared = getattr(spec, "shared_with", [])
        if not check_access(principal, spec_owner, spec_shared):
            return None

    return spec


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
        self._checkpoint_store: Any | None = None
        self._replica_id: str | None = None
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._event_keys: dict[str, str] = {}

    def cancel_run(self, team_run_id: str, *, event_key: str | None = None) -> None:
        """Signal only the local run with the authenticated scoped identity."""
        if event_key is None or self._event_keys.get(team_run_id) != event_key:
            return
        event = self._cancel_events.get(team_run_id)
        if event:
            event.set()
            logger.info("Cancel event set for team run %s", team_run_id)

    async def _sync_remote_cancel(self, team_run_id: str) -> None:
        """Propagate this run's durable cancellation signal to local workers."""
        key = self._event_keys.get(team_run_id)
        if key is None:
            return
        try:
            event_store = self._get_event_store()
            cancelled = bool(
                (self._checkpoint_store and await self._checkpoint_store.is_cancelled(key))
                or (event_store and await event_store.get_status(key) == "cancelled")
            )
            if cancelled:
                event = self._cancel_events.get(team_run_id)
                if event is not None and not event.is_set():
                    event.set()
                    logger.info("Remote cancel detected for team run %s", team_run_id)
        except Exception:
            logger.debug("Failed to check remote cancellation for %s", team_run_id, exc_info=True)

    async def _task_cancelled(self, team_run_id: str, task_id: str) -> bool:
        """Honor run cancellation and never revive a failed task."""
        await self._sync_remote_cancel(team_run_id)
        cancel_evt = self._cancel_events.get(team_run_id)
        task = await registry.tasks.get_task(team_run_id, task_id)
        if cancel_evt and cancel_evt.is_set():
            if task is not None and task.status in (TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS):
                await registry.tasks.update_task(team_run_id, task_id, status=TaskStatus.FAILED, result="Run cancelled")
            return True
        return task is not None and task.status == TaskStatus.FAILED

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

    def set_checkpoint_store(self, store: Any, replica_id: str | None = None) -> None:
        self._checkpoint_store = store
        self._replica_id = replica_id

    # ── Per-role contextvar context manager ──────────────────────────
    #
    # Each of the three team roles (planner / synthesizer / worker)
    # needs a different slice of per-primitive context installed before
    # its agent loop runs and reset on exit.  The runner threads the
    # values in and this context manager does the install/reset.

    @contextlib.asynccontextmanager
    async def _team_role_context(
        self,
        *,
        team_run_id: str,
        agent_role: str,
        memory_namespace: str,
        shared_memory_namespace: str | None = None,
        knowledge_namespace: str | None = None,
    ) -> AsyncIterator[None]:
        """Install per-role contextvars around a single agent-loop run.

        Resets them on exit so the outer team-run phase is unaffected
        and concurrent workers don't see each other's state.
        """
        team_run_token = set_team_run_id(team_run_id)
        role_token = set_agent_role(agent_role)
        mem_token = set_memory_namespace(memory_namespace)
        shared_token = (
            set_shared_memory_namespace(shared_memory_namespace) if shared_memory_namespace is not None else None
        )
        knowledge_token = set_knowledge_namespace(knowledge_namespace)
        try:
            yield
        finally:
            with contextlib.suppress(Exception):
                reset_knowledge_namespace(knowledge_token)
            if shared_token is not None:
                with contextlib.suppress(Exception):
                    reset_shared_memory_namespace(shared_token)
            with contextlib.suppress(Exception):
                reset_memory_namespace(mem_token)
            with contextlib.suppress(Exception):
                reset_agent_role(role_token)
            with contextlib.suppress(Exception):
                reset_team_run_id(team_run_token)

    def _get_event_store(self) -> Any:
        """Best-effort: get the team background manager's event store for resume events."""
        try:
            from agentic_primitives_gateway.routes.teams import _bg as team_bg

            return team_bg._event_store if team_bg else None
        except Exception:
            return None

    @staticmethod
    def _event_key(team_spec: TeamSpec, team_run_id: str) -> str | None:
        from agentic_primitives_gateway.routes._background import run_event_key

        principal = get_authenticated_principal()
        if principal is None:
            return None
        return run_event_key("team", principal.id, team_spec.owner_id, team_spec.name, team_run_id)

    def _cancel_key(self, team_spec: TeamSpec, team_run_id: str) -> str:
        key = self._event_key(team_spec, team_run_id)
        if key is None:
            raise RuntimeError("Cannot cancel a team run without an authenticated principal")
        return key

    def _remember_event_key(self, team_spec: TeamSpec, team_run_id: str) -> None:
        key = self._event_key(team_spec, team_run_id)
        if key is not None:
            self._event_keys[team_run_id] = key

    async def _validate_team_pool(self, team_spec: TeamSpec) -> None:
        if not team_spec.shared_memory_namespace:
            return
        from agentic_primitives_gateway.auth.access import validate_team_pool_reference

        principal = get_authenticated_principal()
        if principal is None or self._agent_store is None or self._team_store is None:
            raise RuntimeError("Cannot validate team pool without a principal and stores")
        await validate_team_pool_reference(team_spec, principal, self._agent_store, self._team_store)

    async def _recover_partial_tokens(
        self, team_run_id: str, role_label: str, *, team_spec: TeamSpec | None = None
    ) -> str:
        """Recover partial tokens for a specific agent from the event store.

        Walks events in reverse to find the last ``invocation_start`` for the
        given role, then collects ``agent_token`` events with the matching
        ``invocation_id``.  This ensures we never feed one agent's tokens to
        a different agent on resume.
        """
        event_store = self._get_event_store()
        if not event_store:
            return ""
        try:
            event_key = self._event_key(team_spec, team_run_id) if team_spec is not None else None
            if event_key is None:
                return ""
            events = await event_store.get_events(event_key)
            if not events:
                return ""

            # Find the last invocation_id for this role
            target_invocation: str | None = None
            for ev in reversed(events):
                if not isinstance(ev, dict):
                    continue
                if ev.get("type") == "invocation_start" and ev.get("agent") == role_label:
                    target_invocation = ev.get("invocation_id")
                    break
            if not target_invocation:
                return ""

            # Collect tokens for that invocation (walk reverse, stop at boundary)
            parts: list[str] = []
            for ev in reversed(events):
                if not isinstance(ev, dict):
                    continue
                if ev.get("invocation_id") != target_invocation:
                    if parts:
                        break  # Passed through all events for this invocation
                    continue
                if ev.get("type") == "agent_token":
                    parts.append(ev.get("content", ""))
                elif ev.get("type") == "invocation_start":
                    break

            if not parts:
                return ""
            parts.reverse()
            partial = "".join(parts)
            if partial:
                logger.info(
                    "Recovered %d chars of partial tokens for team run %s (role=%s)",
                    len(partial),
                    team_run_id,
                    role_label,
                )
            return partial
        except Exception:
            logger.debug("Failed to recover partial tokens for %s", team_run_id, exc_info=True)
            return ""

    async def _team_checkpoint(self, team_spec: TeamSpec, team_run_id: str, message: str, phase: str) -> None:
        """Persist team run state for crash recovery. Task board is already durable."""
        if not team_spec.checkpointing_enabled:
            return
        if not self._checkpoint_store:
            return
        principal = get_authenticated_principal()
        if principal is None:
            raise RuntimeError("Cannot checkpoint without an authenticated principal")
        data: dict[str, Any] = {
            "type": "team",
            "spec_name": team_spec.name,
            "spec_owner": team_spec.owner_id,
            "team_run_id": team_run_id,
            "message": message,
            "phase": phase,
            "replica_id": self._replica_id,
        }
        data.update(serialize_auth_context())
        try:
            key = self._event_key(team_spec, team_run_id)
            if key is not None:
                await self._checkpoint_store.save_if_active(key, data, ttl=86400)
        except Exception:
            logger.debug("Failed to save team checkpoint for %s", team_run_id, exc_info=True)

    async def _delete_team_checkpoint(self, team_run_id: str, team_spec: TeamSpec) -> None:
        if not team_spec.checkpointing_enabled:
            return
        if not self._checkpoint_store:
            return
        principal = get_authenticated_principal()
        if principal is None:
            return
        try:
            key = self._event_key(team_spec, team_run_id)
            if key is not None:
                await self._checkpoint_store.delete_if_owned(key)
        except Exception:
            logger.debug("Failed to delete team checkpoint for %s", team_run_id, exc_info=True)

    async def resume(self, checkpoint_key: str) -> None:
        """Resume a team run from a checkpoint (called during orphan recovery).

        Reconstructs the auth context, loads the team spec, and resumes
        from the checkpointed phase. The task board in Redis provides
        the durable task state — we just need to re-run incomplete phases.
        """
        if not self._checkpoint_store or not self._team_store:
            return

        # Old owner:run checkpoints are ambiguous after scoped-key cutover.
        if not checkpoint_key.startswith("event:"):
            await self._checkpoint_store.delete(checkpoint_key)
            return

        from agentic_primitives_gateway.agents.checkpoint import recovery_lock

        replica_id = self._replica_id or uuid.uuid4().hex[:12]
        async with recovery_lock(self._checkpoint_store, checkpoint_key, replica_id) as owner:
            if owner is None:
                return
            data = await self._checkpoint_store.load(checkpoint_key)
            if data is None or data.get("type") != "team" or await self._checkpoint_store.is_cancelled(checkpoint_key):
                return
            from agentic_primitives_gateway.routes._background import run_event_key

            principal_id = (data.get("principal") or {}).get("id")
            if not isinstance(principal_id, str):
                return
            if checkpoint_key != run_event_key(
                "team",
                principal_id,
                data.get("spec_owner", "system"),
                data.get("spec_name", ""),
                data.get("team_run_id", ""),
            ):
                logger.warning("Ignoring checkpoint with mismatched team identity %s", checkpoint_key)
                return
            data["replica_id"] = replica_id
            if not await self._checkpoint_store.save_recovery(checkpoint_key, owner, data):
                return
            try:
                await self._resume_team_from_data(data)
            except Exception:
                logger.exception("Failed to resume team run from checkpoint %s", checkpoint_key)

    async def _resume_team_from_data(self, data: dict[str, Any]) -> None:
        # Reconstruct principal and credentials from checkpoint
        principal = restore_auth_context(data)

        spec_name = data["spec_name"]
        spec_owner = data.get("spec_owner", "system")
        team_spec = await self._team_store.resolve_qualified(spec_owner, spec_name)  # type: ignore[union-attr]
        if team_spec is None:
            logger.warning("Team '%s:%s' not found during resume — skipping", spec_owner, spec_name)
            return

        # Defense in depth: verify the restored principal still has access
        from agentic_primitives_gateway.auth.access import check_access
        from agentic_primitives_gateway.context import get_authenticated_principal

        resume_principal = get_authenticated_principal()
        if resume_principal is not None and not check_access(
            resume_principal, team_spec.owner_id, team_spec.shared_with
        ):
            logger.warning(
                "Principal '%s' no longer has access to team '%s:%s' — skipping resume",
                resume_principal.id,
                spec_owner,
                spec_name,
            )
            return
        await self._validate_team_pool(team_spec)

        team_run_id = data["team_run_id"]
        message = data.get("message", "")
        phase = data.get("phase", "planning")

        # Register cancel event so cancel_run() works for recovered runs
        cancel_event = asyncio.Event()
        self._cancel_events[team_run_id] = cancel_event
        self._remember_event_key(team_spec, team_run_id)
        await self._sync_remote_cancel(team_run_id)
        if cancel_event.is_set():
            self._cancel_events.pop(team_run_id, None)
            self._event_keys.pop(team_run_id, None)
            return

        assert self._agent_store is not None
        assert self._agent_runner is not None

        logger.info(
            "Resuming team[%s] run=%s from phase=%s (user=%s)",
            spec_name,
            team_run_id,
            phase,
            principal.id,
        )

        # Notify event store so the UI can see the resumed run
        event_store = self._get_event_store()
        event_key = self._event_key(team_spec, team_run_id)
        await self._sync_remote_cancel(team_run_id)
        if cancel_event.is_set():
            self._cancel_events.pop(team_run_id, None)
            self._event_keys.pop(team_run_id, None)
            return
        if event_store and event_key is not None:
            await event_store.set_status(event_key, "running")
            await event_store.append_event(
                event_key,
                {
                    "type": "run_resumed",
                    "team_run_id": team_run_id,
                    "team_name": spec_name,
                    "phase": phase,
                },
            )

        # Resume using streaming variants so events (tokens, task_claimed,
        # worker activity) are emitted to the event store for the UI to replay.
        async def _emit(event: dict[str, Any]) -> None:
            await self._sync_remote_cancel(team_run_id)
            if event_store and event_key is not None and not cancel_event.is_set():
                await event_store.append_event(event_key, event)

        # Reset in-progress tasks to pending so workers can re-claim them.
        # After a crash, tasks may be stuck in "in_progress" from dead workers.
        try:
            await self._sync_remote_cancel(team_run_id)
            if cancel_event.is_set():
                return
            all_tasks = await registry.tasks.list_tasks(team_run_id)
            for t in all_tasks:
                if t.status == "in_progress":
                    await registry.tasks.update_task(team_run_id, t.id, status="pending")
                    logger.info("Reset stuck task %s to pending for run %s", t.id, team_run_id)
        except Exception:
            logger.debug("Failed to reset stuck tasks for %s", team_run_id, exc_info=True)

        # Recover partial tokens from the interrupted phase so the model
        # can continue where it left off instead of regenerating.
        phase_role_map = {"planning": "planner", "synthesis": "synthesizer"}
        phase_role = phase_role_map.get(phase)
        phase_hint: str | None = None
        if phase_role:
            phase_hint = await self._recover_partial_tokens(team_run_id, phase_role, team_spec=team_spec) or None

        try:
            await self._sync_remote_cancel(team_run_id)
            if cancel_event.is_set():
                return
            if phase == "planning":
                await _emit({"type": "phase_change", "phase": "planning"})
                seen_task_ids: set[str] = set()
                async for event in self._run_planner_stream(team_spec, team_run_id, message, resume_hint=phase_hint):
                    await _emit(event)
                    # Poll for newly created tasks after every event
                    new_tasks = await registry.tasks.list_tasks(team_run_id)
                    for t in new_tasks:
                        if t.id not in seen_task_ids:
                            seen_task_ids.add(t.id)
                            await _emit(
                                {
                                    "type": "task_created",
                                    "task": {
                                        "id": t.id,
                                        "title": t.title,
                                        "priority": t.priority,
                                        "suggested_worker": t.suggested_worker,
                                    },
                                }
                            )

                all_tasks = await registry.tasks.list_tasks(team_run_id)
                await _emit(
                    {
                        "type": "tasks_created",
                        "count": len(all_tasks),
                        "tasks": [
                            {
                                "id": t.id,
                                "title": t.title,
                                "priority": t.priority,
                                "suggested_worker": t.suggested_worker,
                            }
                            for t in all_tasks
                        ],
                    }
                )
                phase = "execution"

            await self._sync_remote_cancel(team_run_id)
            if cancel_event.is_set():
                return
            if phase == "execution":
                await self._team_checkpoint(team_spec, team_run_id, message, "execution")
                await _emit({"type": "phase_change", "phase": "execution"})
                async with contextlib.aclosing(
                    self._run_with_replanning_stream(team_spec, team_run_id, message)
                ) as stream:
                    async for event in stream:
                        await _emit(event)
                phase = "synthesis"

            await self._sync_remote_cancel(team_run_id)
            if cancel_event.is_set():
                return
            if phase == "synthesis":
                await self._team_checkpoint(team_spec, team_run_id, message, "synthesis")
                await _emit({"type": "phase_change", "phase": "synthesis"})
                # Re-recover hint for synthesis (may be a direct resume into this phase)
                synth_hint = await self._recover_partial_tokens(team_run_id, "synthesizer", team_spec=team_spec) or None
                response = ""
                async for event in self._run_synthesizer_stream(
                    team_spec, team_run_id, message, resume_hint=synth_hint
                ):
                    if event.get("type") == "agent_token":
                        response += event.get("content", "")
                    await _emit(event)

                all_tasks = await registry.tasks.list_tasks(team_run_id)
                done_count = sum(1 for t in all_tasks if t.status == TaskStatus.DONE)
                await _emit(
                    {
                        "type": "done",
                        "response": response,
                        "team_run_id": team_run_id,
                        "team_name": spec_name,
                        "phase": "done",
                        "tasks_created": len(all_tasks),
                        "tasks_completed": done_count,
                    }
                )
        except Exception:
            logger.exception("Error during team resume phases for run %s", team_run_id)
            await _emit({"type": "error", "detail": f"Resume failed for team run {team_run_id}"})

        await self._sync_remote_cancel(team_run_id)
        if event_store and event_key is not None and not cancel_event.is_set():
            await event_store.set_status(event_key, "idle")

        await self._delete_team_checkpoint(team_run_id, team_spec=team_spec)
        self._cancel_events.pop(team_run_id, None)
        self._event_keys.pop(team_run_id, None)
        logger.info("Team[%s] run=%s resumed and completed", spec_name, team_run_id)

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
        await self._validate_team_pool(team_spec)

        _emit_team_event(AuditAction.TEAM_RUN_START, AuditOutcome.SUCCESS, team_spec.name, team_run_id)
        metrics.TEAM_RUNS.labels(team_name=team_spec.name, status="start").inc()
        run_status = "failed"
        run_action = AuditAction.TEAM_RUN_FAILED
        run_outcome = AuditOutcome.ERROR
        try:
            await self._team_checkpoint(team_spec, team_run_id, message, "planning")
            logger.info("Team[%s] run=%s phase=planning", team_spec.name, team_run_id)
            await self._run_planner(team_spec, team_run_id, message)

            await self._team_checkpoint(team_spec, team_run_id, message, "execution")
            logger.info("Team[%s] run=%s phase=execution", team_spec.name, team_run_id)
            workers_used = await self._run_with_replanning(team_spec, team_run_id, message)

            await self._team_checkpoint(team_spec, team_run_id, message, "synthesis")
            logger.info("Team[%s] run=%s phase=synthesis", team_spec.name, team_run_id)
            response = await self._run_synthesizer(team_spec, team_run_id, message)

            await self._delete_team_checkpoint(team_run_id, team_spec=team_spec)
            all_tasks = await registry.tasks.list_tasks(team_run_id)
            done_count = sum(1 for t in all_tasks if t.status == TaskStatus.DONE)
            run_status = "complete"
            run_action = AuditAction.TEAM_RUN_COMPLETE
            run_outcome = AuditOutcome.SUCCESS

            return TeamRunResponse(
                response=response,
                team_run_id=team_run_id,
                team_name=team_spec.name,
                phase=TeamRunPhase.DONE,
                tasks_created=len(all_tasks),
                tasks_completed=done_count,
                workers_used=workers_used,
            )
        finally:
            _emit_team_event(run_action, run_outcome, team_spec.name, team_run_id)
            metrics.TEAM_RUNS.labels(team_name=team_spec.name, status=run_status).inc()

    async def run_stream(
        self,
        team_spec: TeamSpec,
        message: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """Streaming team run. Yields SSE-friendly event dicts."""
        team_run_id = uuid.uuid4().hex[:16]
        assert self._agent_store is not None
        assert self._agent_runner is not None
        await self._validate_team_pool(team_spec)
        if team_run_id in self._cancel_events:
            raise ValueError(f"Team run ID already active: {team_run_id}")

        cancel_event = asyncio.Event()
        self._cancel_events[team_run_id] = cancel_event
        self._remember_event_key(team_spec, team_run_id)

        _emit_team_event(AuditAction.TEAM_RUN_START, AuditOutcome.SUCCESS, team_spec.name, team_run_id)
        metrics.TEAM_RUNS.labels(team_name=team_spec.name, status="start").inc()
        run_status = "failed"
        run_action = AuditAction.TEAM_RUN_FAILED
        run_outcome = AuditOutcome.ERROR
        try:
            yield {"type": "team_start", "team_run_id": team_run_id, "team_name": team_spec.name}

            # Phase 1: Initial planning
            await self._team_checkpoint(team_spec, team_run_id, message, "planning")
            yield {"type": "phase_change", "phase": "planning"}
            seen_task_ids: set[str] = set()
            async for event in self._run_planner_stream(team_spec, team_run_id, message):
                yield event
                new_tasks = await registry.tasks.list_tasks(team_run_id)
                for t in new_tasks:
                    if t.id not in seen_task_ids:
                        seen_task_ids.add(t.id)
                        yield {
                            "type": "task_created",
                            "task": {
                                "id": t.id,
                                "title": t.title,
                                "priority": t.priority,
                                "suggested_worker": t.suggested_worker,
                            },
                        }

            await self._sync_remote_cancel(team_run_id)
            if cancel_event.is_set():
                run_status = "cancelled"
                run_action = AuditAction.TEAM_RUN_CANCELLED
                run_outcome = AuditOutcome.DENY
                yield {"type": "cancelled", "team_run_id": team_run_id}
                return

            all_tasks = await registry.tasks.list_tasks(team_run_id)
            yield {
                "type": "tasks_created",
                "count": len(all_tasks),
                "tasks": [
                    {"id": t.id, "title": t.title, "priority": t.priority, "suggested_worker": t.suggested_worker}
                    for t in all_tasks
                ],
            }

            await self._sync_remote_cancel(team_run_id)
            if cancel_event.is_set():
                run_status = "cancelled"
                run_action = AuditAction.TEAM_RUN_CANCELLED
                run_outcome = AuditOutcome.DENY
                yield {"type": "cancelled", "team_run_id": team_run_id}
                return

            # Phase 2: Execution with continuous re-planning
            await self._team_checkpoint(team_spec, team_run_id, message, "execution")
            yield {"type": "phase_change", "phase": "execution"}
            workers_used: list[str] = []
            async with contextlib.aclosing(self._run_with_replanning_stream(team_spec, team_run_id, message)) as stream:
                async for event in stream:
                    if event.get("type") == "worker_done" and event["agent"] not in workers_used:
                        workers_used.append(event["agent"])
                    yield event

            await self._sync_remote_cancel(team_run_id)
            if cancel_event.is_set():
                run_status = "cancelled"
                run_action = AuditAction.TEAM_RUN_CANCELLED
                run_outcome = AuditOutcome.DENY
                yield {"type": "cancelled", "team_run_id": team_run_id}
                return

            # Phase 3: Synthesis
            await self._team_checkpoint(team_spec, team_run_id, message, "synthesis")
            yield {"type": "phase_change", "phase": "synthesis"}
            response = ""
            async for event in self._run_synthesizer_stream(team_spec, team_run_id, message):
                if event.get("type") == "agent_token":
                    response += event.get("content", "")
                yield event

            await self._sync_remote_cancel(team_run_id)
            if cancel_event.is_set():
                run_status = "cancelled"
                run_action = AuditAction.TEAM_RUN_CANCELLED
                run_outcome = AuditOutcome.DENY
                yield {"type": "cancelled", "team_run_id": team_run_id}
                return

            await self._delete_team_checkpoint(team_run_id, team_spec=team_spec)
            all_tasks = await registry.tasks.list_tasks(team_run_id)
            done_count = sum(1 for t in all_tasks if t.status == TaskStatus.DONE)

            run_status = "complete"
            run_action = AuditAction.TEAM_RUN_COMPLETE
            run_outcome = AuditOutcome.SUCCESS
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
        finally:
            self._cancel_events.pop(team_run_id, None)
            self._event_keys.pop(team_run_id, None)
            _emit_team_event(run_action, run_outcome, team_spec.name, team_run_id)
            metrics.TEAM_RUNS.labels(team_name=team_spec.name, status=run_status).inc()

    # ── Phase 1: Planning ────────────────────────────────────────────

    @staticmethod
    def _working_memory_ns(team_spec: TeamSpec, team_run_id: str, role: str | None = None) -> str:
        """Private working memory for one team owner, caller and run."""
        from agentic_primitives_gateway.agents.namespace import pool_owner_segment

        principal = get_authenticated_principal()
        if principal is None:
            raise RuntimeError("Cannot use team working memory without an authenticated principal")
        base = f"team-private:{pool_owner_segment(team_spec.owner_id)}:{team_spec.name}:{team_run_id}"
        user = pool_owner_segment(principal.id)
        return f"{base}:{role}:u:{user}" if role else f"{base}:u:{user}"

    def _planner_memory_ns(self, team_spec: TeamSpec, team_run_id: str, role: str) -> str:
        """Separate planner/synthesizer private working memory from worker memory."""
        return self._working_memory_ns(team_spec, team_run_id, role)

    async def _run_planner(self, team_spec: TeamSpec, team_run_id: str, message: str) -> None:
        planner_spec = await self._get_agent(team_spec.planner, "Planner", team_spec.owner_id)
        prompt = await build_planner_prompt(team_spec, message, self._agent_store)  # type: ignore[arg-type]
        tools = self._planner_tools()
        cancel_evt = self._cancel_events.get(team_run_id)
        async with self._team_role_context(
            team_run_id=team_run_id,
            agent_role="planner",
            memory_namespace=self._planner_memory_ns(team_spec, team_run_id, "planner"),
        ):
            await run_agent_with_tools(
                planner_spec,
                prompt,
                tools,
                max_turns=planner_spec.max_turns,
                cancel_event=cancel_evt,
                checkpoint_store=self._checkpoint_store,
                run_id=self._cancel_key(team_spec, team_run_id),
            )

    async def _run_planner_stream(
        self, team_spec: TeamSpec, team_run_id: str, message: str, resume_hint: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        planner_spec = await self._get_agent(team_spec.planner, "Planner", team_spec.owner_id)
        prompt = await build_planner_prompt(team_spec, message, self._agent_store)  # type: ignore[arg-type]
        tools = self._planner_tools()
        logger.info("Planner prompt:\n%s", prompt)
        cancel_evt = self._cancel_events.get(team_run_id)
        async with self._team_role_context(
            team_run_id=team_run_id,
            agent_role="planner",
            memory_namespace=self._planner_memory_ns(team_spec, team_run_id, "planner"),
        ):
            async for event in run_agent_with_tools_stream(
                planner_spec,
                prompt,
                tools,
                "planner",
                max_turns=planner_spec.max_turns,
                resume_hint=resume_hint,
                cancel_event=cancel_evt,
                checkpoint_store=self._checkpoint_store,
                run_id=self._cancel_key(team_spec, team_run_id),
            ):
                yield event

    def _planner_tools(self) -> list[ToolDefinition]:
        """Planner only gets create_task and list_tasks.

        The ``team_run_id`` + ``agent_role`` contextvars are installed by
        ``_team_role_context`` around the actual run — this function
        just produces the tool list itself.
        """
        primitives = {"task_board": PrimitiveConfig(enabled=True, tools=["create_task", "list_tasks"])}
        return build_tool_list(primitives)

    # ── Continuous re-planning ───────────────────────────────────────

    async def _run_replanner(self, team_spec: TeamSpec, team_run_id: str, message: str) -> int:
        """Evaluate completed tasks and create follow-ups. Returns new task count."""
        planner_spec = await _resolve_team_agent(self._agent_store, team_spec.owner_id, team_spec.planner)
        if planner_spec is None:
            return 0
        prompt = await build_replan_prompt(team_spec, team_run_id, message, self._agent_store)  # type: ignore[arg-type]
        if prompt is None:
            return 0

        count_before = len(await registry.tasks.list_tasks(team_run_id))
        tools = self._planner_tools()
        cancel_evt = self._cancel_events.get(team_run_id)
        async with self._team_role_context(
            team_run_id=team_run_id,
            agent_role="planner",
            memory_namespace=self._planner_memory_ns(team_spec, team_run_id, "planner"),
        ):
            await run_agent_with_tools(
                planner_spec,
                prompt,
                tools,
                max_turns=planner_spec.max_turns,
                cancel_event=cancel_evt,
                checkpoint_store=self._checkpoint_store,
                run_id=self._cancel_key(team_spec, team_run_id),
            )
        await self._sync_remote_cancel(team_run_id)
        if cancel_evt and cancel_evt.is_set():
            return 0
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
        planner_spec = await _resolve_team_agent(self._agent_store, team_spec.owner_id, team_spec.planner)
        if planner_spec is None:
            return 0
        prompt = await build_replan_prompt(team_spec, team_run_id, message, self._agent_store)  # type: ignore[arg-type]
        if prompt is None:
            return 0

        tasks_before = await registry.tasks.list_tasks(team_run_id)
        count_before = len(tasks_before)
        seen_task_ids = {t.id for t in tasks_before}
        tools = self._planner_tools()

        await event_queue.put({"type": "phase_change", "phase": "replanning"})
        cancel_evt = self._cancel_events.get(team_run_id)
        async with self._team_role_context(
            team_run_id=team_run_id,
            agent_role="planner",
            memory_namespace=self._planner_memory_ns(team_spec, team_run_id, "planner"),
        ):
            async for event in run_agent_with_tools_stream(
                planner_spec,
                prompt,
                tools,
                "planner",
                max_turns=planner_spec.max_turns,
                cancel_event=cancel_evt,
                checkpoint_store=self._checkpoint_store,
                run_id=self._cancel_key(team_spec, team_run_id),
            ):
                await self._sync_remote_cancel(team_run_id)
                if cancel_evt and cancel_evt.is_set():
                    break
                await event_queue.put(event)
                # Poll for newly created tasks during re-planning
                current_tasks = await registry.tasks.list_tasks(team_run_id)
                for t in current_tasks:
                    if t.id not in seen_task_ids:
                        seen_task_ids.add(t.id)
                        await event_queue.put(
                            {
                                "type": "task_created",
                                "task": {
                                    "id": t.id,
                                    "title": t.title,
                                    "priority": t.priority,
                                    "suggested_worker": t.suggested_worker,
                                },
                            }
                        )

        await self._sync_remote_cancel(team_run_id)
        if cancel_evt and cancel_evt.is_set():
            return 0
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
    ) -> AsyncGenerator[dict[str, Any]]:
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
                stream = self._worker_loop_stream(worker_name, team_spec, team_run_id)
                try:
                    async for event in stream:
                        await _q.put(event)
                except Exception as e:
                    await _q.put({"type": "worker_error", "agent": worker_name, "error": str(e)})
                finally:
                    await stream.aclose()
                    await _q.put(None)  # sentinel

            worker_tasks = [asyncio.create_task(_worker_wrapper(w)) for w in workers]

            try:
                finished = 0
                while finished < active_count:
                    event = await queue.get()
                    if event is None:
                        finished += 1
                        continue
                    yield event
            finally:
                for wt in worker_tasks:
                    if not wt.done():
                        wt.cancel()
                await asyncio.gather(*worker_tasks, return_exceptions=True)

            await self._sync_remote_cancel(team_run_id)
            cancel_evt = self._cancel_events.get(team_run_id)
            if cancel_evt and cancel_evt.is_set():
                break

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
        worker_spec = await _resolve_team_agent(self._agent_store, team_spec.owner_id, worker_name)
        if worker_spec is None:
            logger.warning("Worker agent '%s' not found — skipping", worker_name)
            return

        cancel_evt = self._cancel_events.get(team_run_id)
        while True:
            # Check for cancellation (local event + remote Redis status)
            await self._sync_remote_cancel(team_run_id)
            if cancel_evt and cancel_evt.is_set():
                logger.info("Worker '%s': cancelled, exiting", worker_name)
                break

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
                if await self._task_cancelled(team_run_id, t.id):
                    return
                updated = await registry.tasks.update_task(team_run_id, t.id, status=TaskStatus.IN_PROGRESS)
                if updated is None or await self._task_cancelled(team_run_id, t.id):
                    return
                try:
                    result = await self._execute_task(worker_spec, team_spec, team_run_id, t.id, t.title, t.description)
                    if await self._task_cancelled(team_run_id, t.id):
                        return
                    updated = await registry.tasks.update_task(team_run_id, t.id, status=TaskStatus.DONE, result=result)
                    if (
                        await self._task_cancelled(team_run_id, t.id)
                        or updated is None
                        or updated.status != TaskStatus.DONE
                    ):
                        return
                    logger.info("Team worker '%s' completed task '%s'", worker_name, t.id)
                except Exception as e:
                    logger.error("Team worker '%s' failed task '%s': %s: %s", worker_name, t.id, type(e).__name__, e)
                    with contextlib.suppress(Exception):
                        if not await self._task_cancelled(team_run_id, t.id):
                            await registry.tasks.update_task(team_run_id, t.id, status=TaskStatus.FAILED, result=str(e))

            await asyncio.gather(*[_run_one(t) for t in claimed], return_exceptions=True)

    async def _worker_loop_stream(
        self, worker_name: str, team_spec: TeamSpec, team_run_id: str
    ) -> AsyncGenerator[dict[str, Any]]:
        """Streaming worker: same as _worker_loop but yields events."""
        worker_spec = await _resolve_team_agent(self._agent_store, team_spec.owner_id, worker_name)
        if worker_spec is None:
            yield {"type": "worker_error", "agent": worker_name, "error": f"Agent '{worker_name}' not found"}
            return

        cancel_evt = self._cancel_events.get(team_run_id)

        yield {"type": "worker_start", "agent": worker_name}
        while True:
            # Check for cancellation (local event + remote Redis status)
            await self._sync_remote_cancel(team_run_id)
            if cancel_evt and cancel_evt.is_set():
                logger.info("Worker '%s': cancelled, exiting", worker_name)
                break

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

            stream = self._execute_tasks_parallel_stream(claimed, worker_spec, team_spec, team_run_id, worker_name)
            try:
                async for event in stream:
                    yield event
            finally:
                await stream.aclose()

        yield {"type": "worker_done", "agent": worker_name}

    async def _execute_tasks_parallel_stream(
        self,
        claimed: list[Any],
        worker_spec: AgentSpec,
        team_spec: TeamSpec,
        team_run_id: str,
        worker_name: str,
    ) -> AsyncGenerator[dict[str, Any]]:
        """Execute claimed tasks in parallel, yielding events as they arrive."""
        event_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def _run_one(t: Any) -> None:
            if await self._task_cancelled(team_run_id, t.id):
                return
            await event_queue.put({"type": "task_claimed", "agent": worker_name, "task_id": t.id, "title": t.title})
            try:
                updated = await registry.tasks.update_task(team_run_id, t.id, status=TaskStatus.IN_PROGRESS)
            except Exception as e:
                logger.error("Parallel task '%s' failed to start: %s: %s", t.id, type(e).__name__, e)
                with contextlib.suppress(Exception):
                    if not await self._task_cancelled(team_run_id, t.id):
                        await registry.tasks.update_task(team_run_id, t.id, status=TaskStatus.FAILED, result=str(e))
                await event_queue.put({"type": "task_failed", "agent": worker_name, "task_id": t.id, "error": str(e)})
                return
            if updated is None or await self._task_cancelled(team_run_id, t.id):
                return
            try:
                result = await self._execute_task_streaming(
                    worker_spec, team_spec, team_run_id, t.id, t.title, t.description, event_queue, worker_name
                )
                if await self._task_cancelled(team_run_id, t.id):
                    return
                updated = await registry.tasks.update_task(team_run_id, t.id, status=TaskStatus.DONE, result=result)
                if (
                    await self._task_cancelled(team_run_id, t.id)
                    or updated is None
                    or updated.status != TaskStatus.DONE
                ):
                    return
                await event_queue.put(
                    {"type": "task_completed", "agent": worker_name, "task_id": t.id, "result": result}
                )
            except Exception as e:
                logger.error("Parallel task '%s' failed: %s: %s", t.id, type(e).__name__, e)
                with contextlib.suppress(Exception):
                    if not await self._task_cancelled(team_run_id, t.id):
                        await registry.tasks.update_task(team_run_id, t.id, status=TaskStatus.FAILED, result=str(e))
                await event_queue.put({"type": "task_failed", "agent": worker_name, "task_id": t.id, "error": str(e)})

        parallel_tasks = [asyncio.create_task(_run_one(t)) for t in claimed]
        closing = False
        batch_error = False
        try:
            finished_ids: set[int] = set()
            while len(finished_ids) < len(parallel_tasks):
                await self._sync_remote_cancel(team_run_id)
                cancel_evt = self._cancel_events.get(team_run_id)
                if cancel_evt and cancel_evt.is_set():
                    break
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
                            if not await self._task_cancelled(team_run_id, task_obj.id):
                                with contextlib.suppress(Exception):
                                    await registry.tasks.update_task(
                                        team_run_id, task_obj.id, status=TaskStatus.FAILED, result=str(exc)
                                    )
                            await event_queue.put(
                                {"type": "task_failed", "agent": worker_name, "task_id": task_obj.id, "error": str(exc)}
                            )

            if not (cancel_evt and cancel_evt.is_set()):
                while not event_queue.empty():
                    event = await event_queue.get()
                    if event is not None:
                        yield event
        except Exception:
            batch_error = True
            raise
        except (GeneratorExit, asyncio.CancelledError):
            closing = True
            raise
        finally:
            if any(not pt.done() for pt in parallel_tasks):
                # Stream closure cancels the run; an ordinary store error only
                # stops this batch, not other workers on the same run.
                if closing:
                    cancel_evt = self._cancel_events.get(team_run_id)
                    if cancel_evt:
                        cancel_evt.set()
                for pt in parallel_tasks:
                    if not pt.done():
                        pt.cancel()
            await asyncio.gather(*parallel_tasks, return_exceptions=True)
            if batch_error:
                for task in claimed:
                    with contextlib.suppress(Exception):
                        current = await registry.tasks.get_task(team_run_id, task.id)
                        if current is not None and current.status in (TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS):
                            await registry.tasks.update_task(
                                team_run_id, task.id, status=TaskStatus.FAILED, result="Worker batch interrupted"
                            )
            cancel_evt = self._cancel_events.get(team_run_id)
            if cancel_evt and cancel_evt.is_set():
                for task in claimed:
                    await self._task_cancelled(team_run_id, task.id)

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
        try:
            session_ids, cv_tokens = await self._start_sessions(worker_spec)
        except BaseException:
            self._restore_overrides(prev_overrides)
            raise
        try:
            tools = self._build_worker_tools(worker_spec, team_spec)
            upstream = await self._gather_upstream_context(team_run_id, task_id)
            message = build_task_message(title, description, upstream)

            logger.info("Task '%s' starting streaming execution for '%s'", task_id, title)
            content = ""
            cancel_evt = self._cancel_events.get(team_run_id)
            async with self._team_role_context(
                team_run_id=team_run_id,
                agent_role=worker_name,
                memory_namespace=self._working_memory_ns(team_spec, team_run_id),
                shared_memory_namespace=self._resolve_shared_namespace(team_spec),
                knowledge_namespace=self._resolve_worker_knowledge_ns(worker_spec),
            ):
                async for event in run_agent_with_tools_stream(
                    worker_spec,
                    message,
                    tools,
                    worker_name,
                    max_turns=worker_spec.max_turns,
                    cancel_event=cancel_evt,
                    checkpoint_store=self._checkpoint_store,
                    run_id=self._cancel_key(team_spec, team_run_id),
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
                            {
                                "type": "agent_tool",
                                "agent": worker_name,
                                "task_id": task_id,
                                "name": event.get("name", ""),
                            }
                        )
            logger.info("Task '%s' streaming execution complete, content_len=%d", task_id, len(content))
            return content
        finally:
            try:
                await self._stop_sessions(session_ids, cv_tokens)
            finally:
                self._restore_overrides(prev_overrides)

    async def _execute_task_core(
        self, worker_spec: AgentSpec, team_spec: TeamSpec, team_run_id: str, task_id: str, title: str, description: str
    ) -> str:
        """Inner task execution with provider overrides already applied."""
        session_ids, cv_tokens = await self._start_sessions(worker_spec)
        try:
            tools = self._build_worker_tools(worker_spec, team_spec)
            upstream = await self._gather_upstream_context(team_run_id, task_id)
            message = build_task_message(title, description, upstream)
            cancel_evt = self._cancel_events.get(team_run_id)
            async with self._team_role_context(
                team_run_id=team_run_id,
                agent_role=worker_spec.name,
                memory_namespace=self._working_memory_ns(team_spec, team_run_id),
                shared_memory_namespace=self._resolve_shared_namespace(team_spec),
                knowledge_namespace=self._resolve_worker_knowledge_ns(worker_spec),
            ):
                return await run_agent_with_tools(
                    worker_spec,
                    message,
                    tools,
                    max_turns=worker_spec.max_turns,
                    cancel_event=cancel_evt,
                    checkpoint_store=self._checkpoint_store,
                    run_id=self._cancel_key(team_spec, team_run_id),
                )
        finally:
            await self._stop_sessions(session_ids, cv_tokens)

    # ── Phase 3: Synthesis ───────────────────────────────────────────

    async def _run_synthesizer(self, team_spec: TeamSpec, team_run_id: str, message: str) -> str:
        synth_spec = await self._get_agent(team_spec.synthesizer, "Synthesizer", team_spec.owner_id)
        prompt = await build_synthesis_prompt(team_run_id, message)
        tools = self._synthesizer_tools()
        cancel_evt = self._cancel_events.get(team_run_id)
        async with self._team_role_context(
            team_run_id=team_run_id,
            agent_role="synthesizer",
            memory_namespace=self._planner_memory_ns(team_spec, team_run_id, "synthesizer"),
        ):
            return await run_agent_with_tools(
                synth_spec,
                prompt,
                tools,
                max_turns=synth_spec.max_turns,
                cancel_event=cancel_evt,
                checkpoint_store=self._checkpoint_store,
                run_id=self._cancel_key(team_spec, team_run_id),
            )

    async def _run_synthesizer_stream(
        self, team_spec: TeamSpec, team_run_id: str, message: str, resume_hint: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        synth_spec = await self._get_agent(team_spec.synthesizer, "Synthesizer", team_spec.owner_id)
        prompt = await build_synthesis_prompt(team_run_id, message)
        tools = self._synthesizer_tools()
        cancel_evt = self._cancel_events.get(team_run_id)
        async with self._team_role_context(
            team_run_id=team_run_id,
            agent_role="synthesizer",
            memory_namespace=self._planner_memory_ns(team_spec, team_run_id, "synthesizer"),
        ):
            async for event in run_agent_with_tools_stream(
                synth_spec,
                prompt,
                tools,
                "synthesizer",
                max_turns=synth_spec.max_turns,
                resume_hint=resume_hint,
                cancel_event=cancel_evt,
                checkpoint_store=self._checkpoint_store,
                run_id=self._cancel_key(team_spec, team_run_id),
            ):
                yield event

    def _synthesizer_tools(self) -> list[ToolDefinition]:
        """Synthesizer gets read-only task board access.

        Deliberately excluded from knowledge retrieval: the synthesizer's
        job is to compose a final response from task results that
        workers already produced.  If cross-referencing against the
        corpus is needed, that should happen inside a worker task (which
        can surface retrieved chunks via ``shared_memory`` or its
        ``result``), not re-run during synthesis.
        """
        primitives = {"task_board": PrimitiveConfig(enabled=True, tools=["list_tasks", "get_task"])}
        return build_tool_list(primitives)

    # ── Helpers ──────────────────────────────────────────────────────

    # ── Task retry ─────────────────────────────────────────────────

    async def retry_task_stream(
        self,
        team_spec: TeamSpec,
        team_run_id: str,
        task_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """Retry a single failed task, streaming events.

        Atomically rearms the failed task, recovers partial tokens from the
        event store, and re-runs the assigned worker agent with resume context.
        """
        await self._validate_team_pool(team_spec)
        # Validate task exists and is failed
        task = await registry.tasks.get_task(team_run_id, task_id)
        if task is None:
            yield {"type": "error", "detail": f"Task '{task_id}' not found"}
            return
        if task.status != TaskStatus.FAILED:
            yield {"type": "error", "detail": f"Task '{task_id}' is {task.status}, not failed"}
            return

        worker_name = task.assigned_to or task.suggested_worker
        if not worker_name:
            yield {"type": "error", "detail": f"Task '{task_id}' has no assigned worker"}
            return

        worker_spec = await _resolve_team_agent(self._agent_store, team_spec.owner_id, worker_name)
        if worker_spec is None:
            yield {"type": "error", "detail": f"Worker agent '{worker_name}' not found"}
            return

        # The read above is for validation only. The provider compare-and-set
        # rejects a concurrent retry or cancellation before any worker starts.
        await self._sync_remote_cancel(team_run_id)
        cancel_evt = self._cancel_events.get(team_run_id)
        if cancel_evt and cancel_evt.is_set():
            yield {"type": "error", "detail": f"Run '{team_run_id}' is cancelled"}
            return
        rearmed = await registry.tasks.retry_task(team_run_id, task_id)
        if rearmed is None:
            yield {"type": "error", "detail": f"Task '{task_id}' is no longer retryable"}
            return
        yield {"type": "task_retry", "agent": worker_name, "task_id": task_id, "title": task.title}

        # Recover partial tokens from the previous failed attempt
        resume_hint = await self._recover_task_tokens(team_run_id, task_id, team_spec=team_spec)

        # Execute the task with resume context
        prev_overrides = self._apply_overrides(worker_spec)
        session_ids: dict[str, str] = {}
        cv_tokens: dict[str, Any] = {}
        try:
            session_ids, cv_tokens = await self._start_sessions(worker_spec)
            tools = self._build_worker_tools(worker_spec, team_spec)
            upstream = await self._gather_upstream_context(team_run_id, task_id)
            message = build_task_message(task.title, task.description, upstream)

            content = ""
            cancel_evt = self._cancel_events.get(team_run_id)
            async with self._team_role_context(
                team_run_id=team_run_id,
                agent_role=worker_name,
                memory_namespace=self._working_memory_ns(team_spec, team_run_id),
                shared_memory_namespace=self._resolve_shared_namespace(team_spec),
                knowledge_namespace=self._resolve_worker_knowledge_ns(worker_spec),
            ):
                async for event in run_agent_with_tools_stream(
                    worker_spec,
                    message,
                    tools,
                    worker_name,
                    max_turns=worker_spec.max_turns,
                    resume_hint=resume_hint or None,
                    cancel_event=cancel_evt,
                    checkpoint_store=self._checkpoint_store,
                    run_id=self._cancel_key(team_spec, team_run_id),
                ):
                    evt_type = event.get("type")
                    if evt_type == "agent_token":
                        content += event.get("content", "")
                        yield {
                            "type": "agent_token",
                            "agent": worker_name,
                            "task_id": task_id,
                            "content": event.get("content", ""),
                        }
                    elif evt_type == "agent_tool":
                        yield {
                            "type": "agent_tool",
                            "agent": worker_name,
                            "task_id": task_id,
                            "name": event.get("name", ""),
                        }

            if await self._task_cancelled(team_run_id, task_id):
                return
            updated = await registry.tasks.update_task(team_run_id, task_id, status=TaskStatus.DONE, result=content)
            if await self._task_cancelled(team_run_id, task_id) or updated is None or updated.status != TaskStatus.DONE:
                return
            yield {"type": "task_completed", "agent": worker_name, "task_id": task_id, "result": content}
            logger.info("Task '%s' retry completed, content_len=%d", task_id, len(content))
        except Exception as e:
            logger.error("Task '%s' retry failed: %s: %s", task_id, type(e).__name__, e)
            with contextlib.suppress(Exception):
                await registry.tasks.update_task(team_run_id, task_id, status=TaskStatus.FAILED, result=str(e))
            yield {"type": "task_failed", "agent": worker_name, "task_id": task_id, "error": str(e)}
        finally:
            try:
                await self._stop_sessions(session_ids, cv_tokens)
            finally:
                self._restore_overrides(prev_overrides)

        yield {"type": "retry_done", "task_id": task_id}

    async def _recover_task_tokens(self, team_run_id: str, task_id: str, *, team_spec: TeamSpec | None = None) -> str:
        """Recover partial tokens for a specific task from the event store.

        Walks events to find agent_token events tagged with this task_id,
        collecting the content from the last invocation.
        """
        event_store = self._get_event_store()
        if not event_store:
            return ""
        try:
            event_key = self._event_key(team_spec, team_run_id) if team_spec is not None else None
            if event_key is None:
                return ""
            events = await event_store.get_events(event_key)
            if not events:
                return ""

            # Collect tokens for this task (last attempt only)
            parts: list[str] = []
            for ev in reversed(events):
                if not isinstance(ev, dict):
                    continue
                if ev.get("task_id") != task_id:
                    if parts:
                        break  # Passed through all events for this task
                    continue
                if ev.get("type") == "agent_token":
                    parts.append(ev.get("content", ""))
                elif ev.get("type") in ("task_claimed", "task_retry"):
                    break  # Reached the start of this task's execution

            if not parts:
                return ""
            parts.reverse()
            partial = "".join(parts)
            if partial:
                logger.info(
                    "Recovered %d chars of partial tokens for task %s in run %s",
                    len(partial),
                    task_id,
                    team_run_id,
                )
            return partial
        except Exception:
            logger.debug("Failed to recover task tokens for %s/%s", team_run_id, task_id, exc_info=True)
            return ""

    async def _get_agent(self, name: str, role: str, team_owner_id: str = "system") -> AgentSpec:
        """Load an agent spec by name from the team owner's namespace."""
        spec: AgentSpec | None = await _resolve_team_agent(self._agent_store, team_owner_id, name)
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
        return apply_provider_overrides(spec)

    @staticmethod
    def _restore_overrides(prev: dict[str, str]) -> None:
        restore_provider_overrides(prev)

    async def _start_sessions(self, worker_spec: AgentSpec) -> tuple[dict[str, str], dict[str, Any]]:
        """Start browser/code_interpreter sessions if the worker uses them.

        Returns ``(session_ids, cv_tokens)``:

        - ``session_ids`` — primitive → session-id dict used by
          :py:meth:`_stop_sessions` for cleanup.
        - ``cv_tokens`` — reset tokens for the per-primitive session
          contextvars we just installed.  Handler code reads the
          session ID from those contextvars; the runner never threads
          it through as a param.
        """
        session_ids: dict[str, str] = {}
        cv_tokens: dict[str, Any] = {}
        try:
            for prim_name, config in worker_spec.primitives.items():
                if not config.enabled or prim_name not in ("browser", "code_interpreter"):
                    continue
                principal = get_authenticated_principal()
                if principal is None:
                    raise RuntimeError("Cannot claim a team session without an authenticated principal")
                owner_store = _session_ownership_store(prim_name)
                if owner_store is None:
                    raise RuntimeError(f"No ownership store for {prim_name}")
                requested_id = uuid.uuid4().hex
                if not await owner_store.set_owner(requested_id, principal.id):
                    raise RuntimeError(f"{prim_name} session ID already in use: {requested_id}")
                claimed_id = requested_id
                started_id: str | None = None
                registered = False
                try:
                    if prim_name == "code_interpreter":
                        result = await registry.code_interpreter.start_session(session_id=requested_id)
                    else:
                        result = await registry.browser.start_session(session_id=requested_id)
                    sid = result.get("session_id")
                    if not sid:
                        raise RuntimeError(f"{prim_name} provider returned no session ID")
                    started_id = sid
                    if sid != requested_id:
                        if not await owner_store.set_owner(sid, principal.id):
                            raise RuntimeError(f"{prim_name} session ID already in use: {sid}")
                        claimed_id = sid
                    if self._session_registry:
                        await self._session_registry.register(prim_name, sid, metadata={"user_id": principal.id})
                        registered = True
                    if sid != requested_id:
                        await owner_store.release_owner(requested_id, principal.id)
                except BaseException:
                    stopped = False
                    if started_id is None or claimed_id == started_id:
                        # The provider may have started the requested session
                        # before raising. Never stop a returned ID we failed to
                        # claim (it could belong to another user).
                        try:
                            if prim_name == "browser":
                                await registry.browser.stop_session(session_id=started_id or requested_id)
                            else:
                                await registry.code_interpreter.stop_session(session_id=started_id or requested_id)
                            stopped = True
                        except BaseException:
                            logger.debug("Failed to stop partially started %s session", prim_name, exc_info=True)
                    if stopped:
                        if registered and self._session_registry:
                            with contextlib.suppress(BaseException):
                                await self._session_registry.unregister(prim_name, started_id)
                        with contextlib.suppress(BaseException):
                            await owner_store.release_owner(claimed_id, principal.id)
                    if started_id is not None and requested_id != claimed_id:
                        with contextlib.suppress(BaseException):
                            await owner_store.release_owner(requested_id, principal.id)
                    raise
                logger.info("Started %s session: %s", prim_name, sid)
                session_ids[prim_name] = sid
                if prim_name == "browser":
                    cv_tokens["browser_session"] = set_browser_session_id(sid)
                else:
                    cv_tokens["code_interpreter_session"] = set_code_interpreter_session_id(sid)
        except BaseException:
            try:
                await self._stop_sessions(session_ids, cv_tokens)
            except BaseException:
                logger.debug("Failed to clean up earlier team sessions", exc_info=True)
            raise
        return session_ids, cv_tokens

    async def _stop_sessions(self, session_ids: dict[str, str], cv_tokens: dict[str, Any]) -> None:
        try:
            for prim_name, sid in session_ids.items():
                try:
                    if prim_name == "browser":
                        await registry.browser.stop_session(session_id=sid)
                    elif prim_name == "code_interpreter":
                        await registry.code_interpreter.stop_session(session_id=sid)
                except BaseException:
                    # A failed stop may leave a live remote session. Its owner and
                    # registry entry must remain until the stop is confirmed.
                    logger.debug("Failed to stop %s team session %s", prim_name, sid, exc_info=True)
                    continue
                owner_store = _session_ownership_store(prim_name)
                principal = get_authenticated_principal()
                if owner_store is not None and principal is not None:
                    try:
                        await owner_store.release_owner(sid, principal.id)
                    except Exception:
                        logger.debug("Failed to release %s team session %s", prim_name, sid, exc_info=True)
                if self._session_registry:
                    try:
                        await self._session_registry.unregister(prim_name, sid)
                    except Exception:
                        logger.debug("Failed to unregister %s team session %s", prim_name, sid, exc_info=True)
        finally:
            if "browser_session" in cv_tokens:
                reset_browser_session_id(cv_tokens["browser_session"])
            if "code_interpreter_session" in cv_tokens:
                reset_code_interpreter_session_id(cv_tokens["code_interpreter_session"])

    def _build_worker_tools(self, worker_spec: AgentSpec, team_spec: TeamSpec) -> list[ToolDefinition]:
        """Build the full tool list for a worker: its primitives + task board + shared memory.

        All request-scoped context (memory namespace, shared namespace,
        session IDs, team_run_id, agent_role, knowledge namespace) is
        installed by ``_team_role_context`` + ``_start_sessions`` before
        the agent loop runs — this method only produces the tool list.
        ``build_tool_list`` reads the knowledge contextvar directly to
        decide whether to include ``search_knowledge``, so callers must
        enter ``_team_role_context`` (which sets the contextvar) before
        invoking this.
        """
        worker_primitives = dict(worker_spec.primitives)
        worker_primitives["task_board"] = PrimitiveConfig(enabled=True, tools=_WORKER_BOARD_TOOLS)

        # Enable shared memory tools when the team has a shared namespace.
        # The namespace itself is set via the ``shared_memory_namespace``
        # contextvar by ``_team_role_context``.
        if team_spec.shared_memory_namespace:
            worker_primitives["shared_memory"] = PrimitiveConfig(enabled=True)

        return build_tool_list(worker_primitives)

    @staticmethod
    def _resolve_worker_knowledge_ns(worker_spec: AgentSpec) -> str | None:
        """Resolve a worker's knowledge namespace from its spec + caller principal.

        Wraps :func:`resolve_knowledge_namespace` with the principal
        lookup so the three worker callsites can pass the result
        directly into ``_team_role_context``.  Returns ``None`` only
        when there's no authenticated principal (defensive — worker
        runs always have one); otherwise returns a namespace string.
        """
        from agentic_primitives_gateway.agents.namespace import resolve_knowledge_namespace

        principal = get_authenticated_principal()
        if principal is None:
            return None
        return resolve_knowledge_namespace(worker_spec, principal)

    @staticmethod
    def _resolve_shared_namespace(team_spec: TeamSpec) -> str | None:
        from agentic_primitives_gateway.agents.namespace import resolve_team_shared_namespace

        return resolve_team_shared_namespace(team_spec)

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
