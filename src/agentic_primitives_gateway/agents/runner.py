from __future__ import annotations

import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from agentic_primitives_gateway.agents.tools import (
    build_tool_list,
    execute_tool,
    to_gateway_tools,
)
from agentic_primitives_gateway.models.agents import AgentSpec, ChatResponse
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

    async def run(
        self,
        spec: AgentSpec,
        message: str,
        session_id: str | None = None,
    ) -> ChatResponse:
        session_id = session_id or uuid.uuid4().hex[:16]
        trace_id = uuid.uuid4().hex

        # Resolve agent-scoped knowledge namespace (no session_id) for memory tools
        # so memories persist across sessions. Conversation history uses (actor_id, session_id)
        # directly via _load_history/_store_turn.
        knowledge_ns = self._resolve_knowledge_namespace(spec)

        # Session context for code_interpreter / browser (lazily populated)
        session_ctx: dict[str, str] = {}

        # Build tool list — bind memory tools to the agent-scoped namespace
        tools = build_tool_list(spec.primitives, namespace=knowledge_ns, session_ctx=session_ctx)
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

            # Execute each tool call and batch results into one message
            tool_results: list[dict[str, Any]] = []
            for tc in tool_calls:
                tool_name = tc["name"]
                tool_input = tc.get("input", {})
                tool_id = tc.get("id", uuid.uuid4().hex[:8])
                tools_called.append(tool_name)

                # Start sessions lazily for code_interpreter / browser
                prev_ctx_size = len(session_ctx)
                await self._ensure_session(tool_name, tools, session_ctx)

                # Rebuild tool list if a new session was started
                if len(session_ctx) > prev_ctx_size:
                    tools = build_tool_list(spec.primitives, namespace=knowledge_ns, session_ctx=session_ctx)
                    gateway_tools = to_gateway_tools(tools) if tools else None

                logger.info(
                    "Agent[%s] executing tool: %s(%s)",
                    spec.name,
                    tool_name,
                    ", ".join(f"{k}={str(v)[:50]}" for k, v in tool_input.items()),
                )
                try:
                    result = await execute_tool(tool_name, tool_input, tools)
                    logger.info(
                        "Agent[%s] tool %s returned: %d chars",
                        spec.name,
                        tool_name,
                        len(result),
                    )
                except Exception as e:
                    result = f"Error: {type(e).__name__}: {e}"
                    logger.warning("Tool %s failed: %s", tool_name, e)

                tool_results.append({"tool_use_id": tool_id, "content": result})

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
            metadata={"trace_id": trace_id},
        )

    async def run_stream(
        self,
        spec: AgentSpec,
        message: str,
        session_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Streaming variant of run(). Yields SSE-friendly event dicts."""
        session_id = session_id or uuid.uuid4().hex[:16]
        trace_id = uuid.uuid4().hex
        knowledge_ns = self._resolve_knowledge_namespace(spec)
        session_ctx: dict[str, str] = {}

        tools = build_tool_list(spec.primitives, namespace=knowledge_ns, session_ctx=session_ctx)
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

            # Execute tool calls
            tool_results: list[dict[str, Any]] = []
            for tc in turn_tool_calls:
                tool_name = tc["name"]
                tool_input = tc.get("input", {})
                tool_id = tc.get("id", uuid.uuid4().hex[:8])
                tools_called.append(tool_name)

                prev_ctx_size = len(session_ctx)
                await self._ensure_session(tool_name, tools, session_ctx)
                if len(session_ctx) > prev_ctx_size:
                    tools = build_tool_list(spec.primitives, namespace=knowledge_ns, session_ctx=session_ctx)
                    gateway_tools = to_gateway_tools(tools) if tools else None

                try:
                    result = await execute_tool(tool_name, tool_input, tools)
                except Exception as e:
                    result = f"Error: {type(e).__name__}: {e}"

                tool_results.append({"tool_use_id": tool_id, "content": result})
                yield {
                    "type": "tool_call_result",
                    "name": tool_name,
                    "id": tool_id,
                    "result": result[:500],
                }

            messages.append({"role": "user", "tool_results": tool_results})
        else:
            content = (
                f"I've reached the maximum number of turns ({spec.max_turns}). Here's what I have so far: {content}"
            )
            messages.append({"role": "assistant", "content": content})

        await self._cleanup_sessions(session_ctx)

        if spec.hooks.auto_memory:
            await self._store_turn(spec.name, session_id, message, content)
        if spec.hooks.auto_trace:
            await self._trace_conversation(trace_id, spec, session_id, message, content, turns_used, tools_called)

        yield {
            "type": "done",
            "response": content,
            "session_id": session_id,
            "agent_name": spec.name,
            "turns_used": turns_used,
            "tools_called": tools_called,
            "metadata": {"trace_id": trace_id},
        }

    def _resolve_namespace(self, spec: AgentSpec, session_id: str) -> str:
        """Resolve memory namespace with all placeholders, including session_id."""
        mem_config = spec.primitives.get("memory")
        ns = mem_config.namespace if mem_config and mem_config.namespace else "agent:{agent_name}"
        return ns.replace("{agent_name}", spec.name).replace("{session_id}", session_id)

    def _resolve_knowledge_namespace(self, spec: AgentSpec) -> str:
        """Resolve the agent-scoped knowledge namespace (no session_id).

        Memory tools (remember/recall/search) use this namespace so that
        stored facts persist across sessions. The {session_id} placeholder
        is stripped — session scoping is only for conversation history.
        """
        mem_config = spec.primitives.get("memory")
        ns = mem_config.namespace if mem_config and mem_config.namespace else "agent:{agent_name}"
        # Strip the session_id portion: "agent:{agent_name}:{session_id}" → "agent:{agent_name}"
        ns = ns.replace(":{session_id}", "").replace("{session_id}", "")
        return ns.replace("{agent_name}", spec.name).rstrip(":")

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
                except Exception:
                    pass  # list_namespaces is optional

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
        with contextlib.suppress(Exception):
            await registry.observability.log_generation(
                trace_id=trace_id,
                name=f"agent:{spec.name}:turn:{turn}",
                model=spec.model,
                input=messages[-1] if messages else None,
                output=response.get("content", ""),
                usage=response.get("usage"),
            )

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
        with contextlib.suppress(Exception):
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
