from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from agentic_primitives_gateway.agents.namespace import resolve_knowledge_namespace
from agentic_primitives_gateway.agents.store import AgentStore
from agentic_primitives_gateway.agents.tools import (
    MAX_AGENT_DEPTH,
    build_tool_list,
    execute_tool,
    to_gateway_tools,
)
from agentic_primitives_gateway.context import get_provider_override, set_provider_overrides
from agentic_primitives_gateway.models.agents import AgentSpec, ChatResponse, ToolArtifact
from agentic_primitives_gateway.models.enums import Primitive
from agentic_primitives_gateway.registry import registry

logger = logging.getLogger(__name__)


class AgentRunner:
    """Orchestrates the agent tool-call loop.

    Flow:
    1. Load conversation history (if auto_memory enabled)
    2. Build tool list from the agent spec
    3. Loop: call LLM → execute tool calls → repeat until end_turn
    4. Store conversation turn (if auto_memory)
    5. Trace the interaction (if auto_trace)
    """

    def __init__(self) -> None:
        self._store: AgentStore | None = None

    def set_store(self, store: AgentStore) -> None:
        """Set the agent store reference (called during app lifespan)."""
        self._store = store

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

        session_id = session_id or uuid.uuid4().hex[:16]
        trace_id = uuid.uuid4().hex

        # Apply this agent's provider overrides, saving the parent's to restore later
        prev_overrides = self._apply_overrides(spec)

        knowledge_ns = resolve_knowledge_namespace(spec)
        session_ctx: dict[str, str] = {}

        tools = build_tool_list(
            spec.primitives,
            namespace=knowledge_ns,
            session_ctx=session_ctx,
            agent_store=self._store,
            agent_runner=self,
            agent_depth=_depth,
        )
        gateway_tools = to_gateway_tools(tools) if tools else None

        # Build messages with conversation history
        messages: list[dict[str, Any]] = []
        if spec.hooks.auto_memory:
            messages = await self._load_history(spec.name, session_id)

        # Auto-retrieve stored memories and inject as context on first message
        if not messages and "memory" in spec.primitives and spec.primitives["memory"].enabled:
            memory_context = await self._load_memory_context(knowledge_ns)
            if memory_context:
                messages.append({"role": "user", "content": memory_context})
                messages.append(
                    {
                        "role": "assistant",
                        "content": "I've reviewed my stored memories and will use them in our conversation.",
                    }
                )

        messages.append({"role": "user", "content": message})

        # Tool-call loop
        turns_used = 0
        tools_called: list[str] = []
        artifacts: list[ToolArtifact] = []
        content = ""

        while turns_used < spec.max_turns:
            turns_used += 1

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
                "Agent[%s] turn %d: calling LLM (%d messages, %d tools)",
                spec.name,
                turns_used,
                len(messages),
                len(gateway_tools) if gateway_tools else 0,
            )
            response = await registry.gateway.route_request(request_dict)

            stop_reason = response.get("stop_reason", "end_turn")
            tool_calls = response.get("tool_calls")
            # Keep last non-empty content — the LLM may return text alongside
            # tool_use and then an empty end_turn on the next call
            turn_content = response.get("content", "")
            if turn_content:
                content = turn_content
            usage = response.get("usage", {})
            logger.info(
                "Agent[%s] turn %d: LLM returned stop_reason=%s, tool_calls=%d, content=%d chars, usage=%s",
                spec.name,
                turns_used,
                stop_reason,
                len(tool_calls) if tool_calls else 0,
                len(turn_content),
                usage,
            )

            # Trace the LLM call
            if spec.hooks.auto_trace:
                await self._trace_generation(trace_id, spec, turns_used, messages, response)

            # If no tool calls, we're done
            if stop_reason != "tool_use" or not tool_calls:
                messages.append({"role": "assistant", "content": content})
                break

            # Append assistant message with tool calls to history
            messages.append(
                {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls,
                }
            )

            # Ensure sessions are started before parallel execution
            for tc in tool_calls:
                prev_ctx_size = len(session_ctx)
                await self._ensure_session(tc["name"], tools, session_ctx)
                if len(session_ctx) > prev_ctx_size:
                    tools = build_tool_list(
                        spec.primitives,
                        namespace=knowledge_ns,
                        session_ctx=session_ctx,
                        agent_store=self._store,
                        agent_runner=self,
                        agent_depth=_depth,
                    )
                    gateway_tools = to_gateway_tools(tools) if tools else None

            # Execute all tool calls in parallel
            async def _exec_one(
                tc: dict[str, Any], *, _tools: list[Any] = tools
            ) -> tuple[str, str, dict[str, Any], str]:
                t_name = tc["name"]
                t_input = tc.get("input", {})
                t_id = tc.get("id", uuid.uuid4().hex[:8])
                logger.info(
                    "Agent[%s] executing tool: %s(%s)",
                    spec.name,
                    t_name,
                    ", ".join(f"{k}={str(v)[:50]}" for k, v in t_input.items()),
                )
                try:
                    res = await execute_tool(t_name, t_input, _tools)
                    logger.info("Agent[%s] tool %s returned: %d chars", spec.name, t_name, len(res))
                except Exception as e:
                    res = f"Error: {type(e).__name__}: {e}"
                    logger.warning("Tool %s failed: %s", t_name, e)
                return t_id, t_name, t_input, res

            results = await asyncio.gather(*[_exec_one(tc) for tc in tool_calls])

            tool_results: list[dict[str, Any]] = []
            for t_id, t_name, t_input, result in results:
                tools_called.append(t_name)
                artifacts.append(ToolArtifact(tool_name=t_name, tool_input=t_input, output=result))
                tool_results.append({"tool_use_id": t_id, "content": result})

            # Bedrock requires all tool results in a single user message
            messages.append({"role": "user", "tool_results": tool_results})
        else:
            # Max turns exceeded
            content = (
                f"I've reached the maximum number of turns ({spec.max_turns}). Here's what I have so far: {content}"
            )
            messages.append({"role": "assistant", "content": content})

        # Clean up sessions
        await self._cleanup_sessions(session_ctx)

        # Restore parent's provider overrides
        self._restore_overrides(prev_overrides)

        # Auto-memory: store the conversation turn
        if spec.hooks.auto_memory:
            await self._store_turn(spec.name, session_id, message, content)

        # Auto-trace: create overall trace
        if spec.hooks.auto_trace:
            await self._trace_conversation(trace_id, spec, session_id, message, content, turns_used, tools_called)

        return ChatResponse(
            response=content,
            session_id=session_id,
            agent_name=spec.name,
            turns_used=turns_used,
            tools_called=tools_called,
            artifacts=artifacts,
            metadata={"trace_id": trace_id},
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

        session_id = session_id or uuid.uuid4().hex[:16]
        trace_id = uuid.uuid4().hex

        # Apply this agent's provider overrides, saving the parent's to restore later
        prev_overrides = self._apply_overrides(spec)

        knowledge_ns = resolve_knowledge_namespace(spec)
        session_ctx: dict[str, str] = {}

        tools = build_tool_list(
            spec.primitives,
            namespace=knowledge_ns,
            session_ctx=session_ctx,
            agent_store=self._store,
            agent_runner=self,
            agent_depth=_depth,
        )
        gateway_tools = to_gateway_tools(tools) if tools else None

        messages: list[dict[str, Any]] = []
        if spec.hooks.auto_memory:
            messages = await self._load_history(spec.name, session_id)

        if not messages and "memory" in spec.primitives and spec.primitives["memory"].enabled:
            memory_context = await self._load_memory_context(knowledge_ns)
            if memory_context:
                messages.append({"role": "user", "content": memory_context})
                messages.append(
                    {
                        "role": "assistant",
                        "content": "I've reviewed my stored memories and will use them in our conversation.",
                    }
                )

        messages.append({"role": "user", "content": message})

        turns_used = 0
        tools_called: list[str] = []
        artifacts: list[ToolArtifact] = []
        content = ""

        yield {"type": "stream_start", "session_id": session_id}

        while turns_used < spec.max_turns:
            turns_used += 1

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

            # Stream LLM response
            turn_content = ""
            turn_tool_calls: list[dict[str, Any]] = []
            stop_reason = "end_turn"

            async for event in registry.gateway.route_request_stream(request_dict):
                etype = event.get("type")

                if etype == "content_delta":
                    delta = event["delta"]
                    turn_content += delta
                    yield {"type": "token", "content": delta}

                elif etype == "tool_use_start":
                    yield {
                        "type": "tool_call_start",
                        "name": event.get("name", ""),
                        "id": event.get("id", ""),
                    }

                elif etype == "tool_use_complete":
                    turn_tool_calls.append(
                        {
                            "id": event.get("id", ""),
                            "name": event["name"],
                            "input": event.get("input", {}),
                        }
                    )

                elif etype == "message_stop":
                    stop_reason = event.get("stop_reason", "end_turn")

            if turn_content:
                content = turn_content

            # No tool calls — we're done
            if stop_reason != "tool_use" or not turn_tool_calls:
                messages.append({"role": "assistant", "content": content})
                break

            # Append assistant message with tool calls to history
            messages.append(
                {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": turn_tool_calls,
                }
            )

            # Ensure sessions before parallel execution
            for tc in turn_tool_calls:
                prev_ctx_size = len(session_ctx)
                await self._ensure_session(tc["name"], tools, session_ctx)
                if len(session_ctx) > prev_ctx_size:
                    tools = build_tool_list(
                        spec.primitives,
                        namespace=knowledge_ns,
                        session_ctx=session_ctx,
                        agent_store=self._store,
                        agent_runner=self,
                        agent_depth=_depth,
                    )
                    gateway_tools = to_gateway_tools(tools) if tools else None

            # Execute tool calls in parallel, streaming sub-agent events via a queue
            event_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
            tool_result_map: dict[str, tuple[str, dict[str, Any], str]] = {}  # id -> (name, input, result)

            async def _exec_streaming(
                tc: dict[str, Any],
                *,
                _tools: list[Any] = tools,
                _queue: asyncio.Queue[dict[str, Any] | None] = event_queue,
                _result_map: dict[str, tuple[str, dict[str, Any], str]] = tool_result_map,
            ) -> None:
                t_name = tc["name"]
                t_input = tc.get("input", {})
                t_id = tc.get("id", uuid.uuid4().hex[:8])

                is_agent_tool = t_name.startswith("call_") and any(
                    t.primitive == "agents" and t.name == t_name for t in _tools
                )
                if is_agent_tool and self._store is not None:
                    sub_agent_name = t_name.removeprefix("call_")
                    sub_spec = await self._store.get(sub_agent_name)
                    if sub_spec is not None:
                        result = ""
                        sub_artifacts: list[dict[str, Any]] = []
                        async for sub_event in self.run_stream(
                            sub_spec,
                            message=t_input.get("message", ""),
                            _depth=_depth + 1,
                        ):
                            sub_type = sub_event.get("type")
                            if sub_type == "token":
                                await _queue.put(
                                    {
                                        "type": "sub_agent_token",
                                        "agent": sub_agent_name,
                                        "content": sub_event["content"],
                                    }
                                )
                            elif sub_type == "tool_call_start":
                                await _queue.put(
                                    {
                                        "type": "sub_agent_tool",
                                        "agent": sub_agent_name,
                                        "name": sub_event.get("name", ""),
                                    }
                                )
                            elif sub_type == "tool_call_result":
                                sub_artifacts.append(
                                    {
                                        "tool": sub_event.get("name", ""),
                                        "tool_input": sub_event.get("tool_input", {}),
                                        "result": sub_event.get("full_result", sub_event.get("result", "")),
                                    }
                                )
                            elif sub_type == "done":
                                result = sub_event.get("response", "")
                        if sub_artifacts:
                            parts = [result, "\n\n--- Tool Artifacts ---"]
                            for sa in sub_artifacts:
                                parts.append(f"\n[{sa['tool']}]")
                                ti = sa.get("tool_input", {})
                                code = ti.get("code", "")
                                if code:
                                    lang = ti.get("language", "python")
                                    parts.append(f"```{lang}\n{code}\n```")
                                if sa["result"]:
                                    parts.append(f"Output:\n{sa['result']}")
                            result = "\n".join(parts)
                    else:
                        result = f"Agent '{sub_agent_name}' not found."
                else:
                    try:
                        result = await execute_tool(t_name, t_input, _tools)
                    except Exception as e:
                        result = f"Error: {type(e).__name__}: {e}"

                _result_map[t_id] = (t_name, t_input, result)
                await _queue.put(
                    {
                        "type": "tool_call_result",
                        "name": t_name,
                        "id": t_id,
                        "result": result[:500],
                        "full_result": result,
                        "tool_input": t_input,
                    }
                )
                await _queue.put(None)  # signal this task is done

            # Launch all tool calls as concurrent tasks
            tasks = [asyncio.create_task(_exec_streaming(tc)) for tc in turn_tool_calls]

            # Drain the event queue until all tasks complete
            pending = len(tasks)
            while pending > 0:
                event = await event_queue.get()
                if event is None:
                    pending -= 1
                    continue
                # tool_call_result is the last event each task emits before finishing
                yield event

            # Wait for tasks to ensure exceptions propagate
            await asyncio.gather(*tasks)

            # Collect results in original tool call order
            tool_results: list[dict[str, Any]] = []
            for tc in turn_tool_calls:
                t_id = tc.get("id", "")
                if t_id in tool_result_map:
                    t_name, t_input, result = tool_result_map[t_id]
                    tools_called.append(t_name)
                    artifacts.append(ToolArtifact(tool_name=t_name, tool_input=t_input, output=result))
                    tool_results.append({"tool_use_id": t_id, "content": result})

            messages.append({"role": "user", "tool_results": tool_results})
        else:
            content = (
                f"I've reached the maximum number of turns ({spec.max_turns}). Here's what I have so far: {content}"
            )
            messages.append({"role": "assistant", "content": content})

        await self._cleanup_sessions(session_ctx)

        # Restore parent's provider overrides
        self._restore_overrides(prev_overrides)

        if spec.hooks.auto_memory:
            await self._store_turn(spec.name, session_id, message, content)
        if spec.hooks.auto_trace:
            await self._trace_conversation(trace_id, spec, session_id, message, content, turns_used, tools_called)

        # Serialize artifacts for the done event
        done_artifacts = []
        for a in artifacts:
            ti = a.tool_input or {}
            done_artifacts.append(
                {
                    "tool_name": a.tool_name,
                    "code": ti.get("code", ""),
                    "language": ti.get("language", "python"),
                    "output": a.output,
                }
            )

        yield {
            "type": "done",
            "response": content,
            "session_id": session_id,
            "agent_name": spec.name,
            "turns_used": turns_used,
            "tools_called": tools_called,
            "artifacts": done_artifacts,
            "metadata": {"trace_id": trace_id},
        }

    @staticmethod
    def _apply_overrides(spec: AgentSpec) -> dict[str, str]:
        """Apply this agent's provider overrides, returning the previous ones."""
        prev: dict[str, str] = {}
        for prim in Primitive:
            val = get_provider_override(prim)
            if val:
                prev[prim] = val
        if spec.provider_overrides:
            # Merge: agent overrides on top of whatever was already set
            merged = {**prev, **spec.provider_overrides}
            set_provider_overrides(merged)
        return prev

    @staticmethod
    def _restore_overrides(prev: dict[str, str]) -> None:
        """Restore previous provider overrides."""
        set_provider_overrides(prev)

    async def _load_memory_context(self, namespace: str) -> str:
        """Load stored memories from the namespace (and related namespaces) as context."""
        all_records = []
        try:
            # First check the primary (knowledge) namespace
            records = await registry.memory.list_memories(namespace=namespace, limit=20)
            all_records.extend(records)

            # Also search session-scoped sub-namespaces for this agent in case
            # memories were stored before the knowledge/session split.
            # Use "namespace:" prefix to avoid matching other agents whose names
            # share a prefix (e.g. "agent:bot" must not match "agent:bot-2").
            if not all_records:
                try:
                    child_prefix = namespace + ":"
                    all_namespaces = await registry.memory.list_namespaces()
                    for ns in all_namespaces:
                        if ns.startswith(child_prefix):
                            ns_records = await registry.memory.list_memories(namespace=ns, limit=20)
                            all_records.extend(ns_records)
                except NotImplementedError:
                    pass  # list_namespaces is optional for providers
                except Exception:
                    logger.debug("Error searching child namespaces for %s", namespace, exc_info=True)

            if not all_records:
                return ""
            # Dedupe by key, preferring the most recently updated
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

    async def _trace_generation(
        self,
        trace_id: str,
        spec: AgentSpec,
        turn: int,
        messages: list[dict[str, Any]],
        response: dict[str, Any],
    ) -> None:
        """Log a single LLM generation to observability."""
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
        """Create overall conversation trace."""
        try:
            await registry.observability.ingest_trace(
                {
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
            )
        except Exception:
            logger.debug("Failed to trace conversation for agent %s", spec.name, exc_info=True)

    async def _ensure_session(
        self,
        tool_name: str,
        tools: list[Any],
        session_ctx: dict[str, str],
    ) -> None:
        """Lazily start code_interpreter / browser sessions on first use."""
        # Determine which primitive the tool belongs to
        tool_def = next((t for t in tools if t.name == tool_name), None)
        if tool_def is None:
            return

        primitive = tool_def.primitive
        if primitive not in ("code_interpreter", "browser"):
            return
        if primitive in session_ctx:
            return

        try:
            logger.info("Starting %s session...", primitive)
            if primitive == "code_interpreter":
                result = await registry.code_interpreter.start_session()
                session_ctx["code_interpreter"] = result.get("session_id", uuid.uuid4().hex[:16])
            elif primitive == "browser":
                result = await registry.browser.start_session()
                session_ctx["browser"] = result.get("session_id", uuid.uuid4().hex[:16])
            logger.info("Started %s session: %s", primitive, session_ctx[primitive])
        except (NotImplementedError, Exception):
            logger.warning("Failed to start %s session", primitive, exc_info=True)
            session_ctx[primitive] = uuid.uuid4().hex[:16]

    async def _cleanup_sessions(self, session_ctx: dict[str, str]) -> None:
        """Stop any sessions that were started during the run."""
        for primitive, sid in session_ctx.items():
            try:
                if primitive == "browser":
                    await registry.browser.stop_session(session_id=sid)
                    logger.info("Stopped browser session: %s", sid)
                elif primitive == "code_interpreter":
                    await registry.code_interpreter.stop_session(session_id=sid)
                    logger.info("Stopped code_interpreter session: %s", sid)
            except (NotImplementedError, Exception):
                logger.debug("Failed to stop %s session %s", primitive, sid)
