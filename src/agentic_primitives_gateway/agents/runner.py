from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from agentic_primitives_gateway import metrics
from agentic_primitives_gateway.agents.checkpoint import CheckpointStore
from agentic_primitives_gateway.agents.checkpoint_utils import (
    apply_provider_overrides,
    restore_auth_context,
    restore_provider_overrides,
    serialize_auth_context,
)
from agentic_primitives_gateway.agents.namespace import (
    resolve_actor_id,
    resolve_knowledge_namespace,
    resolve_memory_namespace,
    resolve_shared_pools,
)
from agentic_primitives_gateway.agents.store import AgentStore
from agentic_primitives_gateway.agents.tools import (
    MAX_AGENT_DEPTH,
    ToolDefinition,
    build_tool_list,
    execute_tool,
    to_llm_tools,
)
from agentic_primitives_gateway.agents.tools.context import pop_current_artifact_structured
from agentic_primitives_gateway.audit.emit import emit_audit_event
from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType
from agentic_primitives_gateway.context import get_authenticated_principal
from agentic_primitives_gateway.models.agents import AgentSpec, ChatResponse, PrimitiveConfig, ToolArtifact
from agentic_primitives_gateway.primitives.browser.context import (
    reset_browser_session_id,
    set_browser_session_id,
)
from agentic_primitives_gateway.primitives.code_interpreter.context import (
    reset_code_interpreter_session_id,
    set_code_interpreter_session_id,
)
from agentic_primitives_gateway.primitives.knowledge.context import (
    reset_citation_counter,
    reset_knowledge_inline_citations,
    reset_knowledge_namespace,
    restore_citation_counter,
    set_knowledge_inline_citations,
    set_knowledge_namespace,
)
from agentic_primitives_gateway.primitives.memory.context import (
    reset_memory_namespace,
    reset_memory_pools,
    set_memory_namespace,
    set_memory_pools,
)
from agentic_primitives_gateway.registry import registry

logger = logging.getLogger(__name__)


def _session_ownership_store(primitive: str) -> Any | None:
    """Resolve the per-primitive ``SessionOwnershipStore`` for ownership tagging.

    Lazy import keeps the runner independent of route helpers at module
    load.  Returns ``None`` for primitives that don't have a store (the
    ownership model is a no-op for them).
    """
    from agentic_primitives_gateway.routes._helpers import (
        browser_session_owners,
        code_interpreter_session_owners,
    )

    if primitive == "browser":
        return browser_session_owners
    if primitive == "code_interpreter":
        return code_interpreter_session_owners
    return None


def _emit_run_event(
    action: str,
    outcome: AuditOutcome,
    agent_name: str,
    *,
    session_id: str,
    depth: int,
    turns_used: int | None = None,
    tools_called: int | None = None,
) -> None:
    """Shorthand for agent run lifecycle emits — keeps the runner code terse."""
    metadata: dict[str, Any] = {"session_id": session_id, "depth": depth}
    if turns_used is not None:
        metadata["turns_used"] = turns_used
    if tools_called is not None:
        metadata["tools_called"] = tools_called
    emit_audit_event(
        action=action,
        outcome=outcome,
        resource_type=ResourceType.AGENT,
        resource_id=agent_name,
        metadata=metadata,
    )


# ── Shared mutable state for a single agent run ─────────────────────


@dataclass
class _RunContext:
    """Holds all mutable state shared between run phases.

    Per-primitive context (memory namespace, shared pools, browser /
    code-interpreter session IDs) flows through contextvars set by
    ``_init_context`` — this dataclass only tracks what the *runner*
    itself needs for cleanup and checkpoint serialization:

    - ``memory_ns`` and ``memory_pool_map`` are captured on ``ctx`` so
      they can be re-applied on checkpoint resume.
    - ``session_ids`` tracks live primitive sessions so ``_cleanup``
      can stop them and unregister from the session registry.
    - ``_cv_tokens`` holds the ``ContextVar`` reset tokens from
      ``_init_context`` so ``_finalize`` can restore the prior values
      (important for sub-agent runs where the parent's contextvars must
      come back after the child run completes).
    """

    spec: AgentSpec
    session_id: str
    actor_id: str
    trace_id: str
    memory_ns: str
    knowledge_ns: str
    depth: int
    prev_overrides: dict[str, str]
    memory_pool_map: dict[str, str] | None = None
    session_ids: dict[str, str] = field(default_factory=dict)
    _cv_tokens: dict[str, Any] = field(default_factory=dict)
    tools: list[ToolDefinition] = field(default_factory=list)
    llm_tools: list[dict[str, Any]] | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    turns_used: int = 0
    tools_called: list[str] = field(default_factory=list)
    artifacts: list[ToolArtifact] = field(default_factory=list)
    content: str = ""


class AgentRunner:
    """Orchestrates the agent tool-call loop.

    ``run()`` and ``run_stream()`` share initialization, request building,
    session management, and finalization via ``_RunContext`` + helper methods.
    Only the LLM call and tool execution differ between the two.
    """

    def __init__(self) -> None:
        self._store: AgentStore | None = None
        self._session_registry: Any | None = None
        self._checkpoint_store: CheckpointStore | None = None
        self._replica_id: str | None = None

    def set_store(self, store: AgentStore) -> None:
        """Set the agent store reference (called during app lifespan)."""
        self._store = store

    def set_session_registry(self, registry: Any) -> None:
        self._session_registry = registry

    def set_checkpoint_store(self, store: CheckpointStore, replica_id: str | None = None) -> None:
        self._checkpoint_store = store
        self._replica_id = replica_id

    # ── Public entry points ──────────────────────────────────────────

    async def run(
        self,
        spec: AgentSpec,
        message: str,
        session_id: str | None = None,
        _depth: int = 0,
    ) -> ChatResponse:
        if _depth >= MAX_AGENT_DEPTH:
            return ChatResponse(
                response=f"Maximum agent delegation depth ({MAX_AGENT_DEPTH}) exceeded.",
                session_id=session_id or "",
                agent_name=spec.name,
                turns_used=0,
                tools_called=[],
            )

        ctx = await self._init_context(spec, message, session_id or uuid.uuid4().hex[:16], _depth)
        _emit_run_event(
            AuditAction.AGENT_RUN_START,
            AuditOutcome.SUCCESS,
            spec.name,
            session_id=ctx.session_id,
            depth=_depth,
        )
        metrics.AGENT_RUNS.labels(agent_name=spec.name, status="start").inc()
        run_status = "failed"
        run_action = AuditAction.AGENT_RUN_FAILED
        run_outcome = AuditOutcome.ERROR
        try:
            while ctx.turns_used < spec.max_turns:
                ctx.turns_used += 1
                await self._checkpoint(ctx, message)
                request_dict = self._build_request(ctx)

                logger.info(
                    "Agent[%s] turn %d: calling LLM (%d messages, %d tools)",
                    spec.name,
                    ctx.turns_used,
                    len(ctx.messages),
                    len(ctx.llm_tools) if ctx.llm_tools else 0,
                )
                response = await registry.llm.route_request(request_dict)

                stop_reason = response.get("stop_reason", "end_turn")
                tool_calls = response.get("tool_calls")
                turn_content = response.get("content", "")
                if turn_content:
                    ctx.content = turn_content

                if spec.hooks.auto_trace:
                    await self._trace_generation(ctx.trace_id, spec, ctx.turns_used, ctx.messages, response)

                if stop_reason != "tool_use" or not tool_calls:
                    ctx.messages.append({"role": "assistant", "content": ctx.content})
                    break

                ctx.messages.append({"role": "assistant", "content": ctx.content, "tool_calls": tool_calls})
                await self._exec_tools_parallel(ctx, tool_calls)
            else:
                ctx.content = f"I've reached the maximum number of turns ({spec.max_turns}). Here's what I have so far: {ctx.content}"
                ctx.messages.append({"role": "assistant", "content": ctx.content})
            run_status = "complete"
            run_action = AuditAction.AGENT_RUN_COMPLETE
            run_outcome = AuditOutcome.SUCCESS
        finally:
            await self._finalize(ctx, message)
            _emit_run_event(
                run_action,
                run_outcome,
                spec.name,
                session_id=ctx.session_id,
                depth=_depth,
                turns_used=ctx.turns_used,
                tools_called=len(ctx.tools_called),
            )
            metrics.AGENT_RUNS.labels(agent_name=spec.name, status=run_status).inc()

        return ChatResponse(
            response=ctx.content,
            session_id=ctx.session_id,
            agent_name=spec.name,
            turns_used=ctx.turns_used,
            tools_called=ctx.tools_called,
            artifacts=ctx.artifacts,
            metadata={"trace_id": ctx.trace_id},
        )

    async def run_stream(
        self,
        spec: AgentSpec,
        message: str,
        session_id: str | None = None,
        _depth: int = 0,
    ) -> AsyncIterator[dict[str, Any]]:
        """Streaming variant of run(). Yields SSE-friendly event dicts."""
        if _depth >= MAX_AGENT_DEPTH:
            yield {"type": "token", "content": f"Maximum agent delegation depth ({MAX_AGENT_DEPTH}) exceeded."}
            yield {
                "type": "done",
                "response": "",
                "session_id": "",
                "agent_name": spec.name,
                "turns_used": 0,
                "tools_called": [],
                "metadata": {},
            }
            return

        ctx = await self._init_context(spec, message, session_id or uuid.uuid4().hex[:16], _depth)
        yield {"type": "stream_start", "session_id": ctx.session_id}

        _emit_run_event(
            AuditAction.AGENT_RUN_START,
            AuditOutcome.SUCCESS,
            spec.name,
            session_id=ctx.session_id,
            depth=_depth,
        )
        metrics.AGENT_RUNS.labels(agent_name=spec.name, status="start").inc()
        run_status = "failed"
        run_action = AuditAction.AGENT_RUN_FAILED
        run_outcome = AuditOutcome.ERROR
        try:
            while ctx.turns_used < spec.max_turns:
                ctx.turns_used += 1
                await self._checkpoint(ctx, message)
                request_dict = self._build_request(ctx)

                # Stream LLM response
                turn_content = ""
                turn_tool_calls: list[dict[str, Any]] = []
                stop_reason = "end_turn"
                # Carry (id, name) from ``tool_use_start`` so
                # ``tool_use_complete`` doesn't need to re-include
                # them — some providers only emit them on start.
                pending_tool: tuple[str, str] = ("", "")

                async for event in registry.llm.route_request_stream(request_dict):
                    etype = event.get("type")
                    if etype == "content_delta":
                        turn_content += event["delta"]
                        yield {"type": "token", "content": event["delta"]}
                    elif etype == "tool_use_start":
                        tool_id = event.get("id", "")
                        tool_name = event.get("name", "")
                        pending_tool = (tool_id, tool_name)
                        yield {"type": "tool_call_start", "name": tool_name, "id": tool_id}
                    elif etype == "tool_use_complete":
                        tool_id = event.get("id") or pending_tool[0]
                        tool_name = event.get("name") or pending_tool[1]
                        turn_tool_calls.append({"id": tool_id, "name": tool_name, "input": event.get("input", {})})
                        pending_tool = ("", "")
                    elif etype == "message_stop":
                        stop_reason = event.get("stop_reason", "end_turn")

                if turn_content:
                    ctx.content = turn_content

                if stop_reason != "tool_use" or not turn_tool_calls:
                    ctx.messages.append({"role": "assistant", "content": ctx.content})
                    break

                ctx.messages.append({"role": "assistant", "content": ctx.content, "tool_calls": turn_tool_calls})

                # Execute tools and yield events
                async for tool_event in self._exec_tools_streaming(ctx, turn_tool_calls):
                    yield tool_event
            else:
                ctx.content = f"I've reached the maximum number of turns ({spec.max_turns}). Here's what I have so far: {ctx.content}"
                ctx.messages.append({"role": "assistant", "content": ctx.content})
            run_status = "complete"
            run_action = AuditAction.AGENT_RUN_COMPLETE
            run_outcome = AuditOutcome.SUCCESS
        finally:
            await self._finalize(ctx, message)
            _emit_run_event(
                run_action,
                run_outcome,
                spec.name,
                session_id=ctx.session_id,
                depth=_depth,
                turns_used=ctx.turns_used,
                tools_called=len(ctx.tools_called),
            )
            metrics.AGENT_RUNS.labels(agent_name=spec.name, status=run_status).inc()

        yield {
            "type": "done",
            "response": ctx.content,
            "session_id": ctx.session_id,
            "agent_name": spec.name,
            "turns_used": ctx.turns_used,
            "tools_called": ctx.tools_called,
            "artifacts": self._serialize_artifacts(ctx.artifacts),
            "metadata": {"trace_id": ctx.trace_id},
        }

    # ── Shared initialization ────────────────────────────────────────

    async def _init_context(self, spec: AgentSpec, message: str, session_id: str, depth: int) -> _RunContext:
        """Set up everything needed before the tool-call loop.

        Sets per-primitive contextvars (memory namespace, shared pool
        map) at run start and captures their reset tokens on ``ctx``.
        The tool catalog no longer receives namespaces as params —
        handlers read directly from contextvars inside each primitive.
        """
        prev_overrides = self._apply_overrides(spec)
        principal = get_authenticated_principal()
        if principal is None:
            raise RuntimeError("Cannot run agent without an authenticated principal")
        memory_ns = resolve_memory_namespace(spec, principal)
        knowledge_ns = resolve_knowledge_namespace(spec, principal)  # always a string, like memory_ns
        actor_id = resolve_actor_id(spec, principal)
        pool_map = resolve_shared_pools(spec)

        # Install contextvars before building the tool list so any early
        # reads inside handlers (unusual but possible) see the correct
        # values.  ``_finalize`` resets these via the captured tokens.
        inline_citations = bool(
            spec.primitives.get("knowledge", PrimitiveConfig()).options.get("inline_citations", False)
        )
        cv_tokens: dict[str, Any] = {
            "memory_ns": set_memory_namespace(memory_ns),
            "memory_pools": set_memory_pools(pool_map),
            "knowledge_ns": set_knowledge_namespace(knowledge_ns),
            "knowledge_inline": set_knowledge_inline_citations(inline_citations),
            # Reset the citation counter so each run starts at [0].
            "knowledge_citation_counter": reset_citation_counter(),
        }

        tools = build_tool_list(
            spec.primitives,
            agent_store=self._store,
            agent_runner=self,
            agent_depth=depth,
            parent_owner_id=spec.owner_id,
            pool_names=sorted(pool_map.keys()) if pool_map else None,
        )

        ctx = _RunContext(
            spec=spec,
            session_id=session_id,
            actor_id=actor_id,
            trace_id=uuid.uuid4().hex,
            memory_ns=memory_ns,
            knowledge_ns=knowledge_ns,
            depth=depth,
            prev_overrides=prev_overrides,
            memory_pool_map=pool_map,
            _cv_tokens=cv_tokens,
            tools=tools,
            llm_tools=to_llm_tools(tools) if tools else None,
        )

        # Load conversation history
        if spec.hooks.auto_memory:
            ctx.messages = await self._load_history(ctx.actor_id, session_id)

        # Inject stored memories as context on first message
        if not ctx.messages and "memory" in spec.primitives and spec.primitives["memory"].enabled:
            memory_context = await self._load_memory_context(memory_ns)
            if memory_context:
                ctx.messages.append({"role": "user", "content": memory_context})
                ctx.messages.append(
                    {
                        "role": "assistant",
                        "content": "I've reviewed my stored memories and will use them in our conversation.",
                    }
                )

        ctx.messages.append({"role": "user", "content": message})
        return ctx

    # ── Shared request building ──────────────────────────────────────

    @staticmethod
    def _build_request(ctx: _RunContext) -> dict[str, Any]:
        """Build the LLM request dict from context."""
        request_dict: dict[str, Any] = {
            "model": ctx.spec.model,
            "messages": ctx.messages,
            "system": ctx.spec.system_prompt,
            "temperature": ctx.spec.temperature,
        }
        if ctx.spec.max_tokens is not None:
            request_dict["max_tokens"] = ctx.spec.max_tokens
        if ctx.llm_tools:
            request_dict["tools"] = ctx.llm_tools
        return request_dict

    # ── Session management ───────────────────────────────────────────

    async def _ensure_sessions_for_tools(self, ctx: _RunContext, tool_calls: list[dict[str, Any]]) -> None:
        """Start browser/code_interpreter sessions lazily.

        Sessions are started on first use because not all agents need
        them.  The session ID is written into a per-primitive contextvar
        that handlers read directly — no tool-list rebuild required.
        This must still run sequentially (before parallel execution)
        because session start has a network cost we don't want to
        duplicate across concurrent tool calls.
        """
        for tc in tool_calls:
            await self._ensure_session(tc["name"], ctx.tools, ctx)

    # ── Non-streaming tool execution ─────────────────────────────────

    async def _exec_tools_parallel(self, ctx: _RunContext, tool_calls: list[dict[str, Any]]) -> None:
        """Execute tool calls in parallel via asyncio.gather, append results to messages."""
        await self._ensure_sessions_for_tools(ctx, tool_calls)

        async def _exec_one(
            tc: dict[str, Any], *, _tools: list[ToolDefinition] = ctx.tools
        ) -> tuple[str, str, dict[str, Any], str, dict[str, Any] | None]:
            t_name, t_input = tc["name"], tc.get("input", {})
            t_id = tc.get("id", uuid.uuid4().hex[:8])
            logger.info("Agent[%s] executing tool: %s", ctx.spec.name, t_name)
            structured: dict[str, Any] | None = None
            try:
                res = await execute_tool(t_name, t_input, _tools)
                # Each tool call runs in its own asyncio.Task so contextvars
                # are copied — the structured payload the handler set only
                # lives on this task's copy.  Read + reset it here before
                # the task completes.
                structured = pop_current_artifact_structured()
            except Exception as e:
                res = f"Error: {type(e).__name__}: {e}"
                logger.warning("Tool %s failed: %s", t_name, e)
            return t_id, t_name, t_input, res, structured

        results = await asyncio.gather(*[_exec_one(tc) for tc in tool_calls])

        tool_results: list[dict[str, Any]] = []
        for t_id, t_name, t_input, result, structured in results:
            ctx.tools_called.append(t_name)
            ctx.artifacts.append(
                ToolArtifact(tool_name=t_name, tool_input=t_input, output=result, structured=structured)
            )
            tool_results.append({"tool_use_id": t_id, "content": result})

        ctx.messages.append({"role": "user", "tool_results": tool_results})

    # ── Streaming tool execution ─────────────────────────────────────

    async def _exec_tools_streaming(
        self, ctx: _RunContext, tool_calls: list[dict[str, Any]]
    ) -> AsyncIterator[dict[str, Any]]:
        """Execute tool calls in parallel, yielding SSE events for sub-agents.

        Uses a shared asyncio.Queue to merge events from concurrent tool tasks:
        - Each tool task runs as an asyncio.Task and puts events on the queue
        - Sub-agent delegation tools forward their child stream events
        - Each task puts None as a sentinel when done
        - The main loop counts sentinels to know when all tasks are complete
        - Results are collected in original tool-call order for the LLM

        Default keyword args (_tools, _queue, _rmap) capture the current values
        at task creation time to satisfy ruff's B023 (closure variable) check.
        """
        await self._ensure_sessions_for_tools(ctx, tool_calls)

        event_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        result_map: dict[str, tuple[str, dict[str, Any], str, dict[str, Any] | None]] = {}

        async def _exec_one(
            tc: dict[str, Any],
            *,
            _tools: list[ToolDefinition] = ctx.tools,
            _queue: asyncio.Queue[dict[str, Any] | None] = event_queue,
            _rmap: dict[str, tuple[str, dict[str, Any], str, dict[str, Any] | None]] = result_map,
        ) -> None:
            t_name, t_input = tc["name"], tc.get("input", {})
            t_id = tc.get("id", uuid.uuid4().hex[:8])

            result = await self._execute_single_tool_streaming(t_name, t_input, _tools, _queue, ctx.depth)
            # Same reason as ``_exec_tools_parallel``: the contextvar is
            # per-Task, so we must pop it here while still inside the
            # tool's task.
            structured = pop_current_artifact_structured()

            _rmap[t_id] = (t_name, t_input, result, structured)
            await _queue.put(
                {
                    "type": "tool_call_result",
                    "name": t_name,
                    "id": t_id,
                    "result": result[:500],
                    "full_result": result,
                    "tool_input": t_input,
                    "structured": structured,
                }
            )
            await _queue.put(None)  # signal done

        tasks = [asyncio.create_task(_exec_one(tc)) for tc in tool_calls]

        pending = len(tasks)
        while pending > 0:
            event = await event_queue.get()
            if event is None:
                pending -= 1
                continue
            yield event

        await asyncio.gather(*tasks)

        # Collect results in original order
        tool_results: list[dict[str, Any]] = []
        for tc in tool_calls:
            t_id = tc.get("id", "")
            if t_id in result_map:
                t_name, t_input, result, structured = result_map[t_id]
                ctx.tools_called.append(t_name)
                ctx.artifacts.append(
                    ToolArtifact(tool_name=t_name, tool_input=t_input, output=result, structured=structured)
                )
                tool_results.append({"tool_use_id": t_id, "content": result})

        ctx.messages.append({"role": "user", "tool_results": tool_results})

    async def _execute_single_tool_streaming(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tools: list[ToolDefinition],
        queue: asyncio.Queue[dict[str, Any] | None],
        depth: int,
    ) -> str:
        """Execute one tool, streaming sub-agent events to the queue if applicable.

        Detects two forms of agent delegation:
        - ``call_{name}`` tools from the static ``agents`` primitive
        - ``delegate_to`` tool from ``agent_management`` (dynamic, for meta-agents)
        Both get streamed sub-agent treatment so the UI shows live activity.
        """
        # Static delegation: call_researcher, call_coder, etc.
        is_static_agent = tool_name.startswith("call_") and any(
            t.primitive == "agents" and t.name == tool_name for t in tools
        )
        if is_static_agent and self._store is not None:
            return await self._run_sub_agent_streaming(tool_name, tool_input, queue, depth)

        # Dynamic delegation: delegate_to(agent_name, message) from agent_management
        is_dynamic_delegate = tool_name == "delegate_to" and any(
            t.primitive == "agent_management" and t.name == "delegate_to" for t in tools
        )
        if is_dynamic_delegate and self._store is not None:
            agent_name = tool_input.get("agent_name", "")
            if agent_name:
                # Reuse _run_sub_agent_streaming with a synthetic tool name
                return await self._run_sub_agent_streaming(
                    f"call_{agent_name}",
                    tool_input,
                    queue,
                    depth,
                )

        try:
            return await execute_tool(tool_name, tool_input, tools)
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"

    async def _run_sub_agent_streaming(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        queue: asyncio.Queue[dict[str, Any] | None],
        depth: int,
    ) -> str:
        """Run a sub-agent via run_stream, forwarding events to the parent queue.

        Consumes the child's SSE stream and:
        - Forwards token/tool events as sub_agent_token/sub_agent_tool (UI shows these)
        - Captures tool_call_result events to collect artifacts (code + output)
        - On "done", appends artifacts to the result string so the parent LLM
          receives the full code and output, not just the child's summary text.
        """
        sub_name = tool_name.removeprefix("call_")
        sub_spec = await self._store.get(sub_name)  # type: ignore[union-attr]
        if sub_spec is None:
            return f"Agent '{sub_name}' not found."

        result = ""
        sub_artifacts: list[dict[str, Any]] = []

        async for event in self.run_stream(sub_spec, message=tool_input.get("message", ""), _depth=depth + 1):
            etype = event.get("type")
            if etype == "token":
                await queue.put({"type": "sub_agent_token", "agent": sub_name, "content": event["content"]})
            elif etype == "tool_call_start":
                await queue.put({"type": "sub_agent_tool", "agent": sub_name, "name": event.get("name", "")})
            elif etype == "tool_call_result":
                sub_artifacts.append(
                    {
                        "tool": event.get("name", ""),
                        "tool_input": event.get("tool_input", {}),
                        "result": event.get("full_result", event.get("result", "")),
                    }
                )
            elif etype == "done":
                result = event.get("response", "")

        if sub_artifacts:
            parts = [result, "\n\n--- Tool Artifacts ---"]
            for sa in sub_artifacts:
                parts.append(f"\n[{sa['tool']}]")
                ti = sa.get("tool_input", {})
                code = ti.get("code", "")
                if code:
                    parts.append(f"```{ti.get('language', 'python')}\n{code}\n```")
                if sa["result"]:
                    parts.append(f"Output:\n{sa['result']}")
            result = "\n".join(parts)

        return result

    # ── Shared finalization ──────────────────────────────────────────

    async def _finalize(self, ctx: _RunContext, user_message: str) -> None:
        """Cleanup sessions, restore contextvars/overrides, store turn, trace, delete checkpoint."""
        await self._cleanup_sessions(ctx)
        self._reset_contextvars(ctx)
        self._restore_overrides(ctx.prev_overrides)

        if ctx.spec.hooks.auto_memory:
            await self._store_turn(ctx.actor_id, ctx.session_id, user_message, ctx.content)
        if ctx.spec.hooks.auto_trace:
            await self._trace_conversation(
                ctx.trace_id,
                ctx.spec,
                ctx.session_id,
                user_message,
                ctx.content,
                ctx.turns_used,
                ctx.tools_called,
            )

        await self._delete_checkpoint(ctx)

    @staticmethod
    def _reset_contextvars(ctx: _RunContext) -> None:
        """Reset per-primitive contextvars back to what they were before ``_init_context``.

        Critical for sub-agent runs: when a parent agent delegates to a
        child agent, the child's ``_init_context`` overrides the parent's
        memory namespace + pool map contextvars.  Resetting here
        restores the parent's values so the parent's next turn sees
        *its* namespace, not the child's.
        """
        tokens = ctx._cv_tokens
        if "memory_ns" in tokens:
            with contextlib.suppress(Exception):
                reset_memory_namespace(tokens["memory_ns"])
        if "memory_pools" in tokens:
            with contextlib.suppress(Exception):
                reset_memory_pools(tokens["memory_pools"])
        if "knowledge_ns" in tokens:
            with contextlib.suppress(Exception):
                reset_knowledge_namespace(tokens["knowledge_ns"])
        if "knowledge_inline" in tokens:
            with contextlib.suppress(Exception):
                reset_knowledge_inline_citations(tokens["knowledge_inline"])
        if "knowledge_citation_counter" in tokens:
            # Restore the parent's citation counter (important when a
            # sub-agent delegated in inline mode — the parent's next turn
            # must continue at its pre-delegation count).
            with contextlib.suppress(Exception):
                restore_citation_counter(tokens["knowledge_citation_counter"])
        # Session contextvars are reset inside ``_cleanup_sessions``
        # so the reset happens atomically with the provider-side
        # ``stop_session`` call.

    @staticmethod
    def _serialize_artifacts(artifacts: list[ToolArtifact]) -> list[dict[str, Any]]:
        """Convert ToolArtifacts to dicts for the SSE done event.

        ``structured`` is included only when the handler attached a
        payload so default artifacts stay compact on the wire.
        """
        result = []
        for a in artifacts:
            ti = a.tool_input or {}
            entry: dict[str, Any] = {
                "tool_name": a.tool_name,
                "code": ti.get("code", ""),
                "language": ti.get("language", "python"),
                "output": a.output,
            }
            if a.structured is not None:
                entry["structured"] = a.structured
            result.append(entry)
        return result

    # ── Checkpointing ────────────────────────────────────────────────

    @staticmethod
    def _checkpoint_key(ctx: _RunContext) -> str:
        principal = get_authenticated_principal()
        if principal is None:
            raise RuntimeError("Cannot checkpoint without an authenticated principal")
        return f"{principal.id}:{ctx.session_id}"

    async def _checkpoint(self, ctx: _RunContext, original_message: str) -> None:
        """Persist run state to Redis for crash recovery."""
        if not ctx.spec.checkpointing_enabled:
            return
        if not self._checkpoint_store:
            return
        principal = get_authenticated_principal()
        if principal is None:
            raise RuntimeError("Cannot checkpoint without an authenticated principal")
        data: dict[str, Any] = {
            "spec_name": ctx.spec.name,
            "spec_owner": ctx.spec.owner_id,
            "session_id": ctx.session_id,
            "actor_id": ctx.actor_id,
            "memory_ns": ctx.memory_ns,
            "memory_pool_map": ctx.memory_pool_map,
            "knowledge_ns": ctx.knowledge_ns,
            "trace_id": ctx.trace_id,
            "depth": ctx.depth,
            "prev_overrides": ctx.prev_overrides,
            "session_ids": ctx.session_ids,
            "messages": ctx.messages,
            "turns_used": ctx.turns_used,
            "tools_called": ctx.tools_called,
            "content": ctx.content,
            "original_message": original_message,
            "replica_id": self._replica_id,
        }
        data.update(serialize_auth_context())
        try:
            await self._checkpoint_store.save(self._checkpoint_key(ctx), data, ttl=86400)
        except Exception:
            logger.debug("Failed to save checkpoint for %s", ctx.session_id, exc_info=True)

    async def _delete_checkpoint(self, ctx: _RunContext) -> None:
        """Remove checkpoint after successful finalization."""
        if not ctx.spec.checkpointing_enabled:
            return
        if not self._checkpoint_store:
            return
        try:
            await self._checkpoint_store.delete(self._checkpoint_key(ctx))
        except Exception:
            logger.debug("Failed to delete checkpoint for %s", ctx.session_id, exc_info=True)

    async def resume(self, checkpoint_key: str) -> None:
        """Resume a run from a checkpoint (called during orphan recovery).

        Reconstructs the auth context from the checkpoint, rebuilds tools,
        and continues the LLM loop from the last completed turn.
        """
        if not self._checkpoint_store or not self._store:
            return

        data = await self._checkpoint_store.load(checkpoint_key)
        if data is None:
            return

        # Acquire distributed lock
        replica_id = self._replica_id or uuid.uuid4().hex[:12]
        if not await self._checkpoint_store.acquire_lock(checkpoint_key, replica_id):
            logger.info("Checkpoint %s is being recovered by another replica", checkpoint_key)
            return

        # Update the checkpoint's replica_id so the orphan scanner skips it
        data["replica_id"] = replica_id
        await self._checkpoint_store.save(checkpoint_key, data, ttl=86400)

        try:
            await self._resume_from_data(data)
        except Exception:
            logger.exception("Failed to resume run from checkpoint %s", checkpoint_key)
        finally:
            await self._checkpoint_store.release_lock(checkpoint_key)

    async def _resume_from_data(self, data: dict[str, Any]) -> None:
        """Internal resume logic — separated for testability."""
        # Reconstruct principal and credentials from checkpoint
        principal = restore_auth_context(data)

        # Load spec (current version from store)
        spec_name = data["spec_name"]
        spec_owner = data.get("spec_owner", "system")
        spec = await self._store.resolve_qualified(spec_owner, spec_name)  # type: ignore[union-attr]
        if spec is None:
            logger.warning("Agent '%s:%s' not found during resume — skipping", spec_owner, spec_name)
            return

        # Rebuild tools (handlers can't be serialized) and reinstall
        # the per-primitive contextvars so handlers see the same
        # namespace / session state as before the crash.
        prev_overrides = self._apply_overrides(spec)
        resumed_memory_ns = data["memory_ns"]
        resumed_knowledge_ns = data["knowledge_ns"]
        resumed_pool_map = data.get("memory_pool_map")
        resumed_session_ids = data.get("session_ids") or {}

        cv_tokens: dict[str, Any] = {
            "memory_ns": set_memory_namespace(resumed_memory_ns),
            "memory_pools": set_memory_pools(resumed_pool_map),
            "knowledge_ns": set_knowledge_namespace(resumed_knowledge_ns),
        }
        # Reinstall session contextvars for any sessions the crashed
        # replica had already started.  If the session is dead on the
        # provider side, the next tool call will fail and the loop will
        # recover on the next turn.
        if "browser" in resumed_session_ids:
            cv_tokens["browser_session"] = set_browser_session_id(resumed_session_ids["browser"])
        if "code_interpreter" in resumed_session_ids:
            cv_tokens["code_interpreter_session"] = set_code_interpreter_session_id(
                resumed_session_ids["code_interpreter"]
            )

        tools = build_tool_list(
            spec.primitives,
            agent_store=self._store,
            agent_runner=self,
            agent_depth=data.get("depth", 0),
            parent_owner_id=spec.owner_id,
            pool_names=sorted(resumed_pool_map.keys()) if resumed_pool_map else None,
        )

        ctx = _RunContext(
            spec=spec,
            session_id=data["session_id"],
            actor_id=data["actor_id"],
            trace_id=data.get("trace_id", uuid.uuid4().hex),
            memory_ns=resumed_memory_ns,
            knowledge_ns=resumed_knowledge_ns,
            depth=data.get("depth", 0),
            prev_overrides=prev_overrides,
            memory_pool_map=resumed_pool_map,
            session_ids=resumed_session_ids,
            _cv_tokens=cv_tokens,
            tools=tools,
            llm_tools=to_llm_tools(tools) if tools else None,
            messages=data.get("messages", []),
            turns_used=data.get("turns_used", 0),
            tools_called=data.get("tools_called", []),
            content=data.get("content", ""),
        )

        original_message = data.get("original_message", "")

        logger.info(
            "Resuming agent[%s] session=%s from turn %d (user=%s)",
            spec.name,
            ctx.session_id,
            ctx.turns_used,
            principal.id,
        )

        # Notify event store that the run was resumed (UI can show indicator)

        # Best-effort: find the background manager's event store to record the resume event
        try:
            from agentic_primitives_gateway.routes.agents import _bg as agent_bg

            if agent_bg and agent_bg._event_store:
                await agent_bg._event_store.append_event(
                    ctx.session_id,
                    {
                        "type": "run_resumed",
                        "session_id": ctx.session_id,
                        "agent_name": spec.name,
                        "turns_used": ctx.turns_used,
                    },
                )
                await agent_bg._event_store.set_status(ctx.session_id, "running")
        except Exception:
            logger.debug("Could not record run_resumed event", exc_info=True)

        # Recover partial tokens from the event store so the model can
        # continue from where it left off instead of restarting the turn.
        partial_content = await self._recover_partial_tokens(ctx.session_id)

        # Continue the LLM loop
        resumed_first_turn = True
        try:
            while ctx.turns_used < spec.max_turns:
                # Check for cross-replica cancellation signal
                if self._checkpoint_store and await self._checkpoint_store.is_cancelled(ctx.session_id):
                    logger.info("Agent[%s] session=%s cancelled via Redis signal", spec.name, ctx.session_id)
                    break

                ctx.turns_used += 1
                request_dict = self._build_request(ctx)

                # On the first turn after resume, inject a continuation hint so
                # the model picks up where it left off rather than regenerating.
                if resumed_first_turn and partial_content:
                    hint = (
                        "\n\n[RESUME CONTEXT: Your previous response was interrupted mid-generation. "
                        "Below is the text you had already produced. Continue seamlessly from exactly "
                        "where you left off — do not repeat any of this text:\n"
                        f"{partial_content}]"
                    )
                    request_dict["system"] = (request_dict.get("system") or "") + hint
                    resumed_first_turn = False

                await self._checkpoint(ctx, original_message)

                response = await registry.llm.route_request(request_dict)
                stop_reason = response.get("stop_reason", "end_turn")
                tool_calls = response.get("tool_calls")
                turn_content = response.get("content", "")
                if turn_content:
                    ctx.content = turn_content

                if stop_reason != "tool_use" or not tool_calls:
                    ctx.messages.append({"role": "assistant", "content": ctx.content})
                    break

                ctx.messages.append({"role": "assistant", "content": ctx.content, "tool_calls": tool_calls})
                await self._exec_tools_parallel(ctx, tool_calls)
            else:
                ctx.content = f"I've reached the maximum number of turns ({spec.max_turns}). Here's what I have so far: {ctx.content}"
                ctx.messages.append({"role": "assistant", "content": ctx.content})
        finally:
            await self._finalize(ctx, original_message)

    # ── Partial token recovery ────────────────────────────────────────

    @staticmethod
    async def _recover_partial_tokens(session_id: str) -> str:
        """Read token events from the event store and reconstruct partial content.

        When resuming from a checkpoint, the LLM turn that was interrupted
        may have already streamed tokens to the event store.  This method
        recovers those tokens so the model can continue from where it
        left off (via a system-prompt hint) instead of regenerating.
        """
        try:
            from agentic_primitives_gateway.routes.agents import _bg as agent_bg

            if not agent_bg or not agent_bg._event_store:
                return ""
            events = await agent_bg._event_store.get_events(session_id)
            if not events:
                return ""
            # Walk events in reverse to find the last incomplete turn's tokens.
            # Tokens after the last tool_call_result (or from the start if no
            # tools were called) belong to the interrupted turn.
            partial_parts: list[str] = []
            for ev in reversed(events):
                if not isinstance(ev, dict):
                    continue
                etype = ev.get("type", "")
                if etype in ("token", "sub_agent_token"):
                    partial_parts.append(ev.get("content", ""))
                elif etype in ("tool_call_result", "done", "stream_start", "run_resumed"):
                    # We've reached the boundary before the interrupted turn
                    break
            if not partial_parts:
                return ""
            # Reverse because we walked backwards
            partial_parts.reverse()
            partial = "".join(partial_parts)
            if partial:
                logger.info(
                    "Recovered %d chars of partial tokens for session %s",
                    len(partial),
                    session_id,
                )
            return partial
        except Exception:
            logger.debug("Failed to recover partial tokens for %s", session_id, exc_info=True)
            return ""

    # ── Provider overrides ───────────────────────────────────────────

    @staticmethod
    def _apply_overrides(spec: AgentSpec) -> dict[str, str]:
        return apply_provider_overrides(spec)

    @staticmethod
    def _restore_overrides(prev: dict[str, str]) -> None:
        restore_provider_overrides(prev)

    # ── Memory helpers ───────────────────────────────────────────────

    async def _load_memory_context(self, namespace: str) -> str:
        """Load stored memories and format as an LLM context preamble.

        Searches two places for memories:
        1. The primary memory namespace (e.g. "agent:research-assistant")
        2. If empty, falls back to child namespaces (e.g. "agent:research-assistant:session123")
           which may contain memories stored before the memory/session split.

        The child namespace search uses "namespace:" as a prefix (with trailing colon)
        to prevent "agent:bot" from matching "agent:bot-2" (multi-tenancy safety).

        Dedupes by key, keeping the most recently updated version when the same
        key exists across multiple child namespaces.
        """
        all_records = []
        try:
            records = await registry.memory.list_memories(namespace=namespace, limit=20)
            all_records.extend(records)

            # Fallback: search session-scoped child namespaces for memories
            # stored before the memory/session namespace split was introduced
            if not all_records:
                try:
                    child_prefix = namespace + ":"
                    all_namespaces = await registry.memory.list_namespaces()
                    for ns in all_namespaces:
                        if ns.startswith(child_prefix):
                            all_records.extend(await registry.memory.list_memories(namespace=ns, limit=20))
                except NotImplementedError:
                    pass  # list_namespaces is optional for providers
                except Exception:
                    logger.debug("Error searching child namespaces for %s", namespace, exc_info=True)

            if not all_records:
                return ""

            # Dedupe: same key may exist in multiple child namespaces — keep newest
            seen: dict[str, Any] = {}
            for r in all_records:
                if r.key not in seen or r.updated_at > seen[r.key].updated_at:
                    seen[r.key] = r
            lines = [
                "[System: The following memories were previously stored in your memory. "
                "Use them to maintain continuity across conversations.]",
            ]
            for r in seen.values():
                lines.append(f"- {r.key}: {r.content}")
            return "\n".join(lines)
        except Exception:
            logger.debug("Failed to load memory context for namespace %s", namespace)
            return ""

    async def _load_history(self, agent_name: str, session_id: str) -> list[dict[str, Any]]:
        """Load conversation history from memory primitive."""
        messages: list[dict[str, Any]] = []
        try:
            turns = await registry.memory.get_last_turns(actor_id=agent_name, session_id=session_id, k=10)
            for turn in turns:
                for msg in turn:
                    messages.append({"role": msg.get("role", "user"), "content": msg.get("text", "")})
        except (NotImplementedError, Exception):
            pass
        return messages

    async def _store_turn(self, agent_name: str, session_id: str, user_msg: str, assistant_msg: str) -> None:
        """Store conversation turn in memory primitive."""
        try:
            await registry.memory.create_event(
                actor_id=agent_name,
                session_id=session_id,
                messages=[(user_msg, "user"), (assistant_msg, "assistant")],
            )
        except (NotImplementedError, Exception):
            logger.debug("Auto-memory store failed (provider may not support events)")

    # ── Observability helpers ────────────────────────────────────────

    async def _trace_generation(
        self,
        trace_id: str,
        spec: AgentSpec,
        turn: int,
        messages: list[dict[str, Any]],
        response: dict[str, Any],
    ) -> None:
        try:
            await registry.observability.log_generation(
                trace_id=trace_id,
                name=f"agent:{spec.name}:turn:{turn}",
                model=spec.model,
                input=messages[-1] if messages else None,
                output=response.get("content", ""),
                usage=response.get("usage"),
            )
        except Exception:
            logger.debug("Failed to log generation for agent %s", spec.name, exc_info=True)

    async def _trace_conversation(
        self,
        trace_id: str,
        spec: AgentSpec,
        session_id: str,
        user_msg: str,
        assistant_msg: str,
        turns_used: int,
        tools_called: list[str],
    ) -> None:
        principal = get_authenticated_principal()
        payload: dict[str, Any] = {
            "trace_id": trace_id,
            "name": f"agent:{spec.name}:chat",
            "input": user_msg,
            "output": assistant_msg,
            "metadata": {
                "agent_name": spec.name,
                "session_id": session_id,
                "turns_used": turns_used,
                "tools_called": tools_called,
            },
        }
        if principal is not None:
            payload["user_id"] = principal.id
        try:
            await registry.observability.ingest_trace(payload)
        except Exception:
            logger.debug("Failed to trace conversation for agent %s", spec.name, exc_info=True)

    # ── Session lifecycle ────────────────────────────────────────────

    async def _ensure_session(self, tool_name: str, tools: list[Any], ctx: _RunContext) -> None:
        """Start a browser / code_interpreter session on first use.

        The session ID is written into ``ctx.session_ids`` (for cleanup
        tracking) and into the per-primitive session contextvar
        (``set_browser_session_id`` / ``set_code_interpreter_session_id``)
        so the corresponding tool handlers can read it directly.  The
        contextvar reset token is captured on ``ctx._cv_tokens`` so
        ``_cleanup_sessions`` can restore prior state.
        """
        tool_def = next((t for t in tools if t.name == tool_name), None)
        if tool_def is None:
            return
        primitive = tool_def.primitive
        if primitive not in ("code_interpreter", "browser") or primitive in ctx.session_ids:
            return

        sid: str | None = None
        try:
            logger.info("Starting %s session...", primitive)
            if primitive == "code_interpreter":
                result = await registry.code_interpreter.start_session()
                sid = result.get("session_id", uuid.uuid4().hex[:16])
            elif primitive == "browser":
                result = await registry.browser.start_session()
                sid = result.get("session_id", uuid.uuid4().hex[:16])
            logger.info("Started %s session: %s", primitive, sid)
            principal = get_authenticated_principal()
            # Record session ownership so the HTTP routes' ``require_owner``
            # check can reject cross-user access via a leaked session ID.
            if principal is not None and sid is not None:
                owner_store = _session_ownership_store(primitive)
                if owner_store is not None:
                    with contextlib.suppress(Exception):
                        await owner_store.set_owner(sid, principal.id)
            if self._session_registry and sid is not None:
                if principal is None:
                    raise RuntimeError("Cannot register session without an authenticated principal")
                await self._session_registry.register(primitive, sid, metadata={"user_id": principal.id})
        except (NotImplementedError, Exception):
            logger.warning("Failed to start %s session", primitive, exc_info=True)
            sid = uuid.uuid4().hex[:16]

        if sid is None:
            return
        ctx.session_ids[primitive] = sid
        # Install the session contextvar so downstream handlers pick it up.
        if primitive == "browser":
            ctx._cv_tokens["browser_session"] = set_browser_session_id(sid)
        else:
            ctx._cv_tokens["code_interpreter_session"] = set_code_interpreter_session_id(sid)

    async def _cleanup_sessions(self, ctx: _RunContext) -> None:
        """Stop live sessions, unregister, and reset per-primitive contextvars."""
        for primitive, sid in ctx.session_ids.items():
            try:
                if primitive == "browser":
                    await registry.browser.stop_session(session_id=sid)
                elif primitive == "code_interpreter":
                    await registry.code_interpreter.stop_session(session_id=sid)
            except (NotImplementedError, Exception):
                logger.debug("Failed to stop %s session %s", primitive, sid)
            if self._session_registry:
                with contextlib.suppress(Exception):
                    await self._session_registry.unregister(primitive, sid)
        # Reset contextvars regardless of whether stop_session succeeded.
        if "browser_session" in ctx._cv_tokens:
            with contextlib.suppress(Exception):
                reset_browser_session_id(ctx._cv_tokens["browser_session"])
        if "code_interpreter_session" in ctx._cv_tokens:
            with contextlib.suppress(Exception):
                reset_code_interpreter_session_id(ctx._cv_tokens["code_interpreter_session"])
