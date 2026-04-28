"""Tests for AgentRunner covering run(), run_stream(), session management,
memory hooks, and streaming tool execution.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_primitives_gateway.agents.runner import AgentRunner, _RunContext
from agentic_primitives_gateway.agents.tools import MAX_AGENT_DEPTH
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import set_authenticated_principal
from agentic_primitives_gateway.models.agents import AgentSpec, HooksConfig, PrimitiveConfig

_RUNNER_MOD = "agentic_primitives_gateway.agents.runner"

_TEST_PRINCIPAL = AuthenticatedPrincipal(id="test-user", type="user")


@pytest.fixture(autouse=True)
def _set_test_principal():
    """Ensure all runner tests have an authenticated principal."""
    set_authenticated_principal(_TEST_PRINCIPAL)
    yield
    set_authenticated_principal(None)  # type: ignore[arg-type]


def _make_spec(
    name: str = "test-agent",
    auto_memory: bool = False,
    auto_trace: bool = False,
    primitives: dict[str, PrimitiveConfig] | None = None,
    max_turns: int = 10,
    provider_overrides: dict[str, str] | None = None,
) -> AgentSpec:
    return AgentSpec(
        name=name,
        model="test-model",
        system_prompt="You are a test agent.",
        primitives=primitives or {},
        hooks=HooksConfig(auto_memory=auto_memory, auto_trace=auto_trace),
        max_turns=max_turns,
        provider_overrides=provider_overrides or {},
    )


def _mock_llm_response(content: str = "Hello", stop_reason: str = "end_turn", tool_calls: list | None = None):
    resp: dict[str, Any] = {
        "model": "test-model",
        "content": content,
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    if tool_calls:
        resp["tool_calls"] = tool_calls
    return resp


class TestRunMaxDepth:
    async def test_run_exceeds_max_depth(self) -> None:
        runner = AgentRunner()
        spec = _make_spec()
        result = await runner.run(spec, message="hi", _depth=MAX_AGENT_DEPTH)
        assert "Maximum agent delegation depth" in result.response
        assert result.turns_used == 0

    async def test_run_stream_exceeds_max_depth(self) -> None:
        runner = AgentRunner()
        spec = _make_spec()
        events = []
        async for event in runner.run_stream(spec, message="hi", _depth=MAX_AGENT_DEPTH):
            events.append(event)
        assert events[0]["type"] == "token"
        assert "Maximum agent delegation depth" in events[0]["content"]
        assert events[1]["type"] == "done"


class TestRunBasic:
    async def test_run_simple_response(self) -> None:
        llm_mock = AsyncMock()
        llm_mock.route_request.return_value = _mock_llm_response("I am a bot")

        runner = AgentRunner()
        spec = _make_spec()
        with patch(f"{_RUNNER_MOD}.registry") as mock_reg:
            mock_reg.llm = llm_mock
            mock_reg.memory = AsyncMock()
            result = await runner.run(spec, message="hi")

        assert result.response == "I am a bot"
        assert result.turns_used == 1
        assert result.agent_name == "test-agent"

    async def test_run_with_tool_call(self) -> None:
        llm_mock = AsyncMock()
        llm_mock.route_request.side_effect = [
            _mock_llm_response(
                "Let me check.",
                stop_reason="tool_use",
                tool_calls=[{"id": "tc-1", "name": "memory_search", "input": {"query": "test"}}],
            ),
            _mock_llm_response("Done."),
        ]

        runner = AgentRunner()
        spec = _make_spec(
            primitives={"memory": PrimitiveConfig(enabled=True)},
        )
        with (
            patch(f"{_RUNNER_MOD}.registry") as mock_reg,
            patch(f"{_RUNNER_MOD}.execute_tool", new_callable=AsyncMock) as mock_exec,
        ):
            mock_reg.llm = llm_mock
            mock_reg.memory = AsyncMock()
            mock_reg.memory.list_memories.return_value = []
            mock_exec.return_value = "search results"
            result = await runner.run(spec, message="search for test")

        assert result.response == "Done."
        assert result.turns_used == 2
        assert "memory_search" in result.tools_called

    async def test_run_max_turns_reached(self) -> None:
        llm_mock = AsyncMock()
        llm_mock.route_request.return_value = _mock_llm_response(
            "still going",
            stop_reason="tool_use",
            tool_calls=[{"id": "tc-1", "name": "memory_search", "input": {"query": "q"}}],
        )

        runner = AgentRunner()
        spec = _make_spec(
            max_turns=2,
            primitives={"memory": PrimitiveConfig(enabled=True)},
        )
        with (
            patch(f"{_RUNNER_MOD}.registry") as mock_reg,
            patch(f"{_RUNNER_MOD}.execute_tool", new_callable=AsyncMock) as mock_exec,
        ):
            mock_reg.llm = llm_mock
            mock_reg.memory = AsyncMock()
            mock_reg.memory.list_memories.return_value = []
            mock_exec.return_value = "result"
            result = await runner.run(spec, message="loop")

        assert "maximum number of turns" in result.response.lower()
        assert result.turns_used == 2


class TestRunWithAutoMemory:
    async def test_auto_memory_stores_turn(self) -> None:
        llm_mock = AsyncMock()
        llm_mock.route_request.return_value = _mock_llm_response("response")

        runner = AgentRunner()
        spec = _make_spec(auto_memory=True)
        with patch(f"{_RUNNER_MOD}.registry") as mock_reg:
            mock_reg.llm = llm_mock
            mock_reg.memory = AsyncMock()
            mock_reg.memory.get_last_turns.return_value = []
            mock_reg.memory.list_memories.return_value = []
            mock_reg.memory.list_namespaces.side_effect = NotImplementedError
            await runner.run(spec, message="hi")

        mock_reg.memory.create_event.assert_awaited_once()


class TestRunWithAutoTrace:
    async def test_auto_trace_logs(self) -> None:
        llm_mock = AsyncMock()
        llm_mock.route_request.return_value = _mock_llm_response("response")

        runner = AgentRunner()
        spec = _make_spec(auto_trace=True)
        with patch(f"{_RUNNER_MOD}.registry") as mock_reg:
            mock_reg.llm = llm_mock
            mock_reg.memory = AsyncMock()
            mock_reg.observability = AsyncMock()
            await runner.run(spec, message="hi")

        mock_reg.observability.log_generation.assert_awaited()
        mock_reg.observability.ingest_trace.assert_awaited()


class TestRunStream:
    async def test_stream_simple_response(self) -> None:
        async def mock_stream(req: dict) -> AsyncIterator[dict[str, Any]]:
            yield {"type": "content_delta", "delta": "Hello "}
            yield {"type": "content_delta", "delta": "world"}
            yield {"type": "message_stop", "stop_reason": "end_turn"}

        runner = AgentRunner()
        spec = _make_spec()
        with patch(f"{_RUNNER_MOD}.registry") as mock_reg:
            mock_reg.llm.route_request_stream = mock_stream
            mock_reg.memory = AsyncMock()
            events = []
            async for event in runner.run_stream(spec, message="hi"):
                events.append(event)

        types = [e["type"] for e in events]
        assert "stream_start" in types
        assert "token" in types
        assert "done" in types
        done = next(e for e in events if e["type"] == "done")
        assert done["response"] == "Hello world"

    async def test_stream_with_tool_call(self) -> None:
        call_count = 0

        async def mock_stream(req: dict) -> AsyncIterator[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield {"type": "tool_use_start", "name": "memory_search", "id": "tc-1"}
                yield {"type": "tool_use_complete", "name": "memory_search", "id": "tc-1", "input": {"query": "q"}}
                yield {"type": "message_stop", "stop_reason": "tool_use"}
            else:
                yield {"type": "content_delta", "delta": "Found it"}
                yield {"type": "message_stop", "stop_reason": "end_turn"}

        runner = AgentRunner()
        spec = _make_spec(primitives={"memory": PrimitiveConfig(enabled=True)})
        with (
            patch(f"{_RUNNER_MOD}.registry") as mock_reg,
            patch(f"{_RUNNER_MOD}.execute_tool", new_callable=AsyncMock) as mock_exec,
        ):
            mock_reg.llm.route_request_stream = mock_stream
            mock_reg.memory = AsyncMock()
            mock_reg.memory.list_memories.return_value = []
            mock_exec.return_value = "search result"
            events = []
            async for event in runner.run_stream(spec, message="search"):
                events.append(event)

        types = [e["type"] for e in events]
        assert "tool_call_start" in types
        assert "tool_call_result" in types
        assert "done" in types

    async def test_stream_max_turns(self) -> None:
        async def mock_stream(req: dict) -> AsyncIterator[dict[str, Any]]:
            yield {"type": "tool_use_start", "name": "memory_search", "id": "tc-1"}
            yield {"type": "tool_use_complete", "name": "memory_search", "id": "tc-1", "input": {"query": "q"}}
            yield {"type": "message_stop", "stop_reason": "tool_use"}

        runner = AgentRunner()
        spec = _make_spec(max_turns=1, primitives={"memory": PrimitiveConfig(enabled=True)})
        with (
            patch(f"{_RUNNER_MOD}.registry") as mock_reg,
            patch(f"{_RUNNER_MOD}.execute_tool", new_callable=AsyncMock) as mock_exec,
        ):
            mock_reg.llm.route_request_stream = mock_stream
            mock_reg.memory = AsyncMock()
            mock_reg.memory.list_memories.return_value = []
            mock_exec.return_value = "result"
            events = []
            async for event in runner.run_stream(spec, message="loop"):
                events.append(event)

        done = next(e for e in events if e["type"] == "done")
        assert "maximum number of turns" in done["response"].lower()


def _minimal_ctx() -> _RunContext:
    """Build a minimal _RunContext suitable for session-management tests.

    The session tests only exercise ``ctx.session_ids`` + ``ctx._cv_tokens``
    mutation, so the surrounding fields can all be placeholder values.
    """
    return _RunContext(
        spec=_make_spec(),
        session_id="s",
        actor_id="a",
        trace_id="t",
        memory_ns="ns",
        knowledge_ns="test-corpus",
        depth=0,
        prev_overrides={},
    )


class TestSessionManagement:
    async def test_ensure_session_code_interpreter(self) -> None:
        runner = AgentRunner()
        tool_def = MagicMock()
        tool_def.name = "code_execute"
        tool_def.primitive = "code_interpreter"
        ctx = _minimal_ctx()

        with patch(f"{_RUNNER_MOD}.registry") as mock_reg:
            mock_reg.code_interpreter = AsyncMock()
            mock_reg.code_interpreter.start_session.return_value = {"session_id": "ci-123"}
            await runner._ensure_session("code_execute", [tool_def], ctx)

        assert ctx.session_ids["code_interpreter"] == "ci-123"

    async def test_ensure_session_browser(self) -> None:
        runner = AgentRunner()
        tool_def = MagicMock()
        tool_def.name = "browser_navigate"
        tool_def.primitive = "browser"
        ctx = _minimal_ctx()

        with patch(f"{_RUNNER_MOD}.registry") as mock_reg:
            mock_reg.browser = AsyncMock()
            mock_reg.browser.start_session.return_value = {"session_id": "br-123"}
            await runner._ensure_session("browser_navigate", [tool_def], ctx)

        assert ctx.session_ids["browser"] == "br-123"

    async def test_ensure_session_skips_already_started(self) -> None:
        runner = AgentRunner()
        tool_def = MagicMock()
        tool_def.name = "code_execute"
        tool_def.primitive = "code_interpreter"
        ctx = _minimal_ctx()
        ctx.session_ids["code_interpreter"] = "existing"

        with patch(f"{_RUNNER_MOD}.registry") as mock_reg:
            mock_reg.code_interpreter = AsyncMock()
            await runner._ensure_session("code_execute", [tool_def], ctx)

        mock_reg.code_interpreter.start_session.assert_not_awaited()

    async def test_ensure_session_handles_failure(self) -> None:
        runner = AgentRunner()
        tool_def = MagicMock()
        tool_def.name = "code_execute"
        tool_def.primitive = "code_interpreter"
        ctx = _minimal_ctx()

        with patch(f"{_RUNNER_MOD}.registry") as mock_reg:
            mock_reg.code_interpreter = AsyncMock()
            mock_reg.code_interpreter.start_session.side_effect = RuntimeError("fail")
            await runner._ensure_session("code_execute", [tool_def], ctx)

        # Should still set a fallback session ID
        assert "code_interpreter" in ctx.session_ids

    async def test_cleanup_sessions(self) -> None:
        runner = AgentRunner()
        ctx = _minimal_ctx()
        ctx.session_ids = {"browser": "br-1", "code_interpreter": "ci-1"}

        with patch(f"{_RUNNER_MOD}.registry") as mock_reg:
            mock_reg.browser = AsyncMock()
            mock_reg.code_interpreter = AsyncMock()
            await runner._cleanup_sessions(ctx)

        mock_reg.browser.stop_session.assert_awaited_once_with(session_id="br-1")
        mock_reg.code_interpreter.stop_session.assert_awaited_once_with(session_id="ci-1")

    async def test_cleanup_sessions_handles_failure(self) -> None:
        runner = AgentRunner()
        ctx = _minimal_ctx()
        ctx.session_ids = {"browser": "br-1"}

        with patch(f"{_RUNNER_MOD}.registry") as mock_reg:
            mock_reg.browser = AsyncMock()
            mock_reg.browser.stop_session.side_effect = RuntimeError("fail")
            await runner._cleanup_sessions(ctx)
            # Should not raise


class TestMemoryHelpers:
    async def test_load_memory_context(self) -> None:
        runner = AgentRunner()
        record = MagicMock()
        record.key = "fact1"
        record.content = "The sky is blue"
        record.updated_at = "2024-01-01"

        with patch(f"{_RUNNER_MOD}.registry") as mock_reg:
            mock_reg.memory = AsyncMock()
            mock_reg.memory.list_memories.return_value = [record]
            result = await runner._load_memory_context("ns")

        assert "fact1" in result
        assert "sky is blue" in result

    async def test_load_memory_context_empty(self) -> None:
        runner = AgentRunner()
        with patch(f"{_RUNNER_MOD}.registry") as mock_reg:
            mock_reg.memory = AsyncMock()
            mock_reg.memory.list_memories.return_value = []
            mock_reg.memory.list_namespaces.return_value = []
            result = await runner._load_memory_context("ns")

        assert result == ""

    async def test_load_memory_context_child_namespaces(self) -> None:
        runner = AgentRunner()
        record = MagicMock()
        record.key = "child-fact"
        record.content = "From child"
        record.updated_at = "2024-01-01"

        with patch(f"{_RUNNER_MOD}.registry") as mock_reg:
            mock_reg.memory = AsyncMock()
            mock_reg.memory.list_memories.side_effect = [[], [record]]
            mock_reg.memory.list_namespaces.return_value = ["ns:child1"]
            result = await runner._load_memory_context("ns")

        assert "child-fact" in result

    async def test_load_memory_context_error(self) -> None:
        runner = AgentRunner()
        with patch(f"{_RUNNER_MOD}.registry") as mock_reg:
            mock_reg.memory = AsyncMock()
            mock_reg.memory.list_memories.side_effect = RuntimeError("fail")
            result = await runner._load_memory_context("ns")

        assert result == ""

    async def test_load_history(self) -> None:
        runner = AgentRunner()
        with patch(f"{_RUNNER_MOD}.registry") as mock_reg:
            mock_reg.memory = AsyncMock()
            mock_reg.memory.get_last_turns.return_value = [
                [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "hello"}]
            ]
            messages = await runner._load_history("agent", "sess")

        assert len(messages) == 2
        assert messages[0]["role"] == "user"

    async def test_load_history_not_implemented(self) -> None:
        runner = AgentRunner()
        with patch(f"{_RUNNER_MOD}.registry") as mock_reg:
            mock_reg.memory = AsyncMock()
            mock_reg.memory.get_last_turns.side_effect = NotImplementedError
            messages = await runner._load_history("agent", "sess")

        assert messages == []

    async def test_store_turn(self) -> None:
        runner = AgentRunner()
        with patch(f"{_RUNNER_MOD}.registry") as mock_reg:
            mock_reg.memory = AsyncMock()
            await runner._store_turn("agent", "sess", "user msg", "bot msg")

        mock_reg.memory.create_event.assert_awaited_once()

    async def test_store_turn_failure_silent(self) -> None:
        runner = AgentRunner()
        with patch(f"{_RUNNER_MOD}.registry") as mock_reg:
            mock_reg.memory = AsyncMock()
            mock_reg.memory.create_event.side_effect = NotImplementedError
            await runner._store_turn("agent", "sess", "user msg", "bot msg")
            # Should not raise


class TestTraceHelpers:
    async def test_trace_generation(self) -> None:
        runner = AgentRunner()
        spec = _make_spec()
        with patch(f"{_RUNNER_MOD}.registry") as mock_reg:
            mock_reg.observability = AsyncMock()
            await runner._trace_generation("trace-1", spec, 1, [{"role": "user", "content": "hi"}], {"content": "ok"})

        mock_reg.observability.log_generation.assert_awaited_once()

    async def test_trace_generation_failure_silent(self) -> None:
        runner = AgentRunner()
        spec = _make_spec()
        with patch(f"{_RUNNER_MOD}.registry") as mock_reg:
            mock_reg.observability = AsyncMock()
            mock_reg.observability.log_generation.side_effect = RuntimeError("fail")
            await runner._trace_generation("trace-1", spec, 1, [], {})
            # Should not raise

    async def test_trace_conversation(self) -> None:
        runner = AgentRunner()
        spec = _make_spec()
        with patch(f"{_RUNNER_MOD}.registry") as mock_reg:
            mock_reg.observability = AsyncMock()
            await runner._trace_conversation("trace-1", spec, "sess", "hi", "hello", 1, [])

        mock_reg.observability.ingest_trace.assert_awaited_once()

    async def test_trace_conversation_failure_silent(self) -> None:
        runner = AgentRunner()
        spec = _make_spec()
        with patch(f"{_RUNNER_MOD}.registry") as mock_reg:
            mock_reg.observability = AsyncMock()
            mock_reg.observability.ingest_trace.side_effect = RuntimeError("fail")
            await runner._trace_conversation("trace-1", spec, "sess", "hi", "hello", 1, [])
            # Should not raise


class TestExecuteSingleToolStreaming:
    async def test_regular_tool_execution(self) -> None:
        runner = AgentRunner()
        queue: asyncio.Queue = asyncio.Queue()
        tool_def = MagicMock()
        tool_def.name = "memory_search"
        tool_def.primitive = "memory"

        with patch(f"{_RUNNER_MOD}.execute_tool", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = "search result"
            result = await runner._execute_single_tool_streaming(
                "memory_search", {"query": "test"}, [tool_def], queue, 0
            )

        assert result == "search result"

    async def test_regular_tool_error(self) -> None:
        runner = AgentRunner()
        queue: asyncio.Queue = asyncio.Queue()
        tool_def = MagicMock()
        tool_def.name = "bad_tool"
        tool_def.primitive = "memory"

        with patch(f"{_RUNNER_MOD}.execute_tool", new_callable=AsyncMock) as mock_exec:
            mock_exec.side_effect = RuntimeError("boom")
            result = await runner._execute_single_tool_streaming("bad_tool", {}, [tool_def], queue, 0)

        assert "Error" in result

    async def test_static_agent_delegation(self) -> None:
        store = AsyncMock()
        spec = MagicMock()
        store.get.return_value = spec

        runner = AgentRunner()
        runner._store = store
        queue: asyncio.Queue = asyncio.Queue()
        tool_def = MagicMock()
        tool_def.name = "call_researcher"
        tool_def.primitive = "agents"

        async def mock_run_stream(spec, message="", _depth=0):
            yield {"type": "token", "content": "hi"}
            yield {"type": "done", "response": "sub-agent done"}

        with patch.object(runner, "run_stream", side_effect=mock_run_stream):
            result = await runner._execute_single_tool_streaming(
                "call_researcher", {"message": "do research"}, [tool_def], queue, 0
            )

        assert result == "sub-agent done"
        # Check that sub_agent_token was forwarded
        events = []
        while not queue.empty():
            events.append(await queue.get())
        assert any(e.get("type") == "sub_agent_token" for e in events)

    async def test_dynamic_delegation_delegate_to(self) -> None:
        store = AsyncMock()
        spec = MagicMock()
        store.get.return_value = spec

        runner = AgentRunner()
        runner._store = store
        queue: asyncio.Queue = asyncio.Queue()
        tool_def = MagicMock()
        tool_def.name = "delegate_to"
        tool_def.primitive = "agent_management"

        async def mock_run_stream(spec, message="", _depth=0):
            yield {"type": "done", "response": "delegated result"}

        with patch.object(runner, "run_stream", side_effect=mock_run_stream):
            result = await runner._execute_single_tool_streaming(
                "delegate_to", {"agent_name": "helper", "message": "do it"}, [tool_def], queue, 0
            )

        assert result == "delegated result"


class TestRunSubAgentStreaming:
    async def test_sub_agent_not_found(self) -> None:
        store = AsyncMock()
        store.get.return_value = None
        runner = AgentRunner()
        runner._store = store

        queue: asyncio.Queue = asyncio.Queue()
        result = await runner._run_sub_agent_streaming("call_missing", {}, queue, 0)
        assert "not found" in result

    async def test_sub_agent_with_artifacts(self) -> None:
        store = AsyncMock()
        store.get.return_value = MagicMock()
        runner = AgentRunner()
        runner._store = store

        queue: asyncio.Queue = asyncio.Queue()

        async def mock_stream(spec, message="", _depth=0):
            yield {"type": "token", "content": "working..."}
            yield {"type": "tool_call_start", "name": "code_execute"}
            yield {
                "type": "tool_call_result",
                "name": "code_execute",
                "tool_input": {"code": "x=1", "language": "python"},
                "full_result": "1",
                "result": "1",
            }
            yield {"type": "done", "response": "done coding"}

        with patch.object(runner, "run_stream", side_effect=mock_stream):
            result = await runner._run_sub_agent_streaming("call_coder", {"message": "write code"}, queue, 0)

        assert "code_execute" in result
        assert "x=1" in result
        assert "Output:" in result


class TestExecToolsStreaming:
    async def test_exec_tools_streaming_basic(self) -> None:
        runner = AgentRunner()
        spec = _make_spec(primitives={"memory": PrimitiveConfig(enabled=True)})
        ctx = _RunContext(
            spec=spec,
            session_id="sess",
            actor_id="test-agent",
            trace_id="trace",
            memory_ns="ns",
            knowledge_ns="test-corpus",
            depth=0,
            prev_overrides={},
            tools=[
                MagicMock(
                    name="memory_search",
                    primitive="memory",
                    handler=AsyncMock(return_value="found"),
                )
            ],
        )
        ctx.llm_tools = [{"name": "memory_search"}]

        tool_calls = [{"id": "tc-1", "name": "memory_search", "input": {"query": "q"}}]

        with patch(f"{_RUNNER_MOD}.execute_tool", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = "found"
            events = []
            async for event in runner._exec_tools_streaming(ctx, tool_calls):
                events.append(event)

        assert any(e.get("type") == "tool_call_result" for e in events)
        assert "memory_search" in ctx.tools_called


class TestInitContextMemoryInjection:
    async def test_memory_context_injected(self) -> None:
        runner = AgentRunner()
        spec = _make_spec(primitives={"memory": PrimitiveConfig(enabled=True)})

        with patch(f"{_RUNNER_MOD}.registry") as mock_reg:
            mock_reg.memory = AsyncMock()
            # Return memories so context gets injected
            record = MagicMock()
            record.key = "fact"
            record.content = "important info"
            record.updated_at = "2024-01-01"
            mock_reg.memory.list_memories.return_value = [record]

            ctx = await runner._init_context(spec, "hello", "sess", 0)

        # Should have memory context + assistant ack + user message = 3 messages
        assert len(ctx.messages) == 3
        assert "important info" in ctx.messages[0]["content"]
        assert ctx.messages[1]["role"] == "assistant"
        assert ctx.messages[2]["content"] == "hello"

    async def test_max_tokens_in_build_request(self) -> None:
        spec = _make_spec()
        spec.max_tokens = 500  # type: ignore[assignment]
        ctx = _RunContext(
            spec=spec,
            session_id="s",
            actor_id="test-agent",
            trace_id="t",
            memory_ns="ns",
            knowledge_ns="test-corpus",
            depth=0,
            prev_overrides={},
            llm_tools=[{"name": "tool1"}],
        )
        request = AgentRunner._build_request(ctx)
        assert request["max_tokens"] == 500
        assert request["tools"] == [{"name": "tool1"}]


class TestKnowledgeNamespaceIsolation:
    """Sub-agent runs must not inherit the parent's corpus namespace.

    Each run resolves its own ``knowledge_ns`` from its own spec and
    installs it in the contextvar.  ``_finalize`` restores the parent's
    value via the captured token so the parent's next turn sees its own
    corpus again.  A future refactor that skipped the per-run
    ``set_knowledge_namespace`` call would silently give the child the
    parent's corpus — this test pins the separation.
    """

    async def test_child_resolves_its_own_knowledge_ns(self) -> None:
        from agentic_primitives_gateway.primitives.knowledge.context import get_knowledge_namespace

        runner = AgentRunner()
        parent_spec = _make_spec(
            name="parent",
            primitives={"knowledge": PrimitiveConfig(enabled=True, namespace="parent-corpus")},
        )
        child_spec = _make_spec(name="child")  # no knowledge config; default template

        parent_ctx = await runner._init_context(parent_spec, "hi", "parent-sess", 0)
        parent_ns = get_knowledge_namespace()
        assert parent_ns == "parent-corpus"

        child_ctx = await runner._init_context(child_spec, "hi", "child-sess", 1)
        child_ns = get_knowledge_namespace()
        assert child_ns != parent_ns
        assert child_ns == child_ctx.knowledge_ns
        # Pin the exact default-template resolution so a future change to
        # _DEFAULT_KNOWLEDGE_NS_TEMPLATE surfaces here rather than quietly
        # migrating every default-namespace agent to a new corpus.
        assert child_ns == "knowledge:system:child"

        # Finalize the child — parent's contextvar must come back.
        with patch.object(runner, "_cleanup_sessions", new_callable=AsyncMock):
            await runner._finalize(child_ctx, "hi")
        assert get_knowledge_namespace() == parent_ns

        with patch.object(runner, "_cleanup_sessions", new_callable=AsyncMock):
            await runner._finalize(parent_ctx, "hi")


class TestSerializeArtifacts:
    def test_serialize_artifacts(self) -> None:
        from agentic_primitives_gateway.models.agents import ToolArtifact

        artifacts = [
            ToolArtifact(tool_name="code_execute", tool_input={"code": "x=1", "language": "python"}, output="1"),
            ToolArtifact(tool_name="memory_search", tool_input={"query": "q"}, output="found"),
        ]
        result = AgentRunner._serialize_artifacts(artifacts)
        assert len(result) == 2
        assert result[0]["code"] == "x=1"
        assert result[0]["language"] == "python"
        assert result[1]["code"] == ""  # no code key in input
