from __future__ import annotations

import json
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_primitives_gateway.agents.tools import handlers
from agentic_primitives_gateway.models.memory import MemoryRecord, SearchResult
from agentic_primitives_gateway.models.tasks import Task
from agentic_primitives_gateway.primitives.browser.context import (
    reset_browser_session_id,
    set_browser_session_id,
)
from agentic_primitives_gateway.primitives.code_interpreter.context import (
    reset_code_interpreter_session_id,
    set_code_interpreter_session_id,
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


def _mock_registry(**primitives: AsyncMock):
    """Create a mock that acts like the registry module-level object."""
    mock_reg = MagicMock()
    for name, mock_prim in primitives.items():
        setattr(mock_reg, name, mock_prim)
    return patch.object(handlers, "registry", mock_reg)


# ── Contextvar fixtures — install per-primitive context the runner normally sets ──


@pytest.fixture
def memory_ns() -> Iterator[str]:
    """Install a memory namespace contextvar for the duration of the test."""
    ns = "ns"
    token = set_memory_namespace(ns)
    try:
        yield ns
    finally:
        reset_memory_namespace(token)


@pytest.fixture
def shared_memory_ns() -> Iterator[str]:
    ns = "shared-ns"
    token = set_shared_memory_namespace(ns)
    try:
        yield ns
    finally:
        reset_shared_memory_namespace(token)


@pytest.fixture
def browser_session() -> Iterator[str]:
    sid = "browser-sess-1"
    token = set_browser_session_id(sid)
    try:
        yield sid
    finally:
        reset_browser_session_id(token)


@pytest.fixture
def code_session() -> Iterator[str]:
    sid = "code-sess-1"
    token = set_code_interpreter_session_id(sid)
    try:
        yield sid
    finally:
        reset_code_interpreter_session_id(token)


@pytest.fixture
def team_context() -> Iterator[tuple[str, str]]:
    """Install team_run_id + agent_role contextvars for task-board handler tests."""
    run_id = "run1"
    role = "worker-agent"
    run_token = set_team_run_id(run_id)
    role_token = set_agent_role(role)
    try:
        yield run_id, role
    finally:
        reset_agent_role(role_token)
        reset_team_run_id(run_token)


# ── Memory handlers ──────────────────────────────────────────────────


class TestMemoryStore:
    async def test_store(self, memory_ns: str) -> None:
        mem = AsyncMock()
        with _mock_registry(memory=mem):
            result = await handlers.memory_store("k", "hello", source="test")
            mem.store.assert_awaited_once()
            assert "Stored" in result

    async def test_store_no_source(self, memory_ns: str) -> None:
        mem = AsyncMock()
        with _mock_registry(memory=mem):
            result = await handlers.memory_store("k", "hello")
            assert "Stored" in result

    async def test_store_without_namespace_raises(self) -> None:
        """Handler must fail loud if called outside a run — no silent default."""
        with pytest.raises(RuntimeError, match="memory tool called outside a run"):
            await handlers.memory_store("k", "v")


class TestMemoryRetrieve:
    async def test_retrieve_found(self, memory_ns: str) -> None:
        mem = AsyncMock()
        mem.retrieve.return_value = MemoryRecord(namespace="ns", key="k", content="data", metadata={})
        with _mock_registry(memory=mem):
            result = await handlers.memory_retrieve("k")
            assert result == "data"

    async def test_retrieve_not_found(self, memory_ns: str) -> None:
        mem = AsyncMock()
        mem.retrieve.return_value = None
        with _mock_registry(memory=mem):
            result = await handlers.memory_retrieve("k")
            assert "No memory found" in result


class TestMemorySearch:
    async def test_search_results(self, memory_ns: str) -> None:
        mem = AsyncMock()
        record = MemoryRecord(namespace="ns", key="k", content="found", metadata={})
        mem.search.return_value = [SearchResult(record=record, score=0.95)]
        with _mock_registry(memory=mem):
            result = await handlers.memory_search("query")
            assert "0.95" in result
            assert "found" in result

    async def test_search_empty(self, memory_ns: str) -> None:
        mem = AsyncMock()
        mem.search.return_value = []
        with _mock_registry(memory=mem):
            result = await handlers.memory_search("query")
            assert "No memories found" in result


class TestMemoryDelete:
    async def test_delete(self, memory_ns: str) -> None:
        mem = AsyncMock()
        mem.delete.return_value = True
        with _mock_registry(memory=mem):
            result = await handlers.memory_delete("k")
            assert "True" in result


class TestMemoryList:
    async def test_list_records(self, memory_ns: str) -> None:
        mem = AsyncMock()
        mem.list_memories.return_value = [MemoryRecord(namespace="ns", key="k1", content="c1", metadata={})]
        with _mock_registry(memory=mem):
            result = await handlers.memory_list()
            assert "k1" in result

    async def test_list_empty(self, memory_ns: str) -> None:
        mem = AsyncMock()
        mem.list_memories.return_value = []
        with _mock_registry(memory=mem):
            result = await handlers.memory_list()
            assert "No memories found" in result


# ── Code interpreter handler ─────────────────────────────────────────


class TestCodeExecute:
    async def test_execute(self, code_session: str) -> None:
        ci = AsyncMock()
        ci.execute.return_value = {"output": "42"}
        with _mock_registry(code_interpreter=ci):
            result = await handlers.code_execute("print(42)")
            ci.execute.assert_awaited_once_with(session_id=code_session, code="print(42)", language="python")
            assert "42" in result

    async def test_execute_without_session_raises(self) -> None:
        with pytest.raises(RuntimeError, match="without an active session"):
            await handlers.code_execute("print(1)")


# ── Browser handlers ─────────────────────────────────────────────────


class TestBrowserHandlers:
    async def test_navigate(self, browser_session: str) -> None:
        br = AsyncMock()
        br.navigate.return_value = {"url": "http://example.com"}
        with _mock_registry(browser=br):
            result = await handlers.browser_navigate("http://example.com")
            assert "example.com" in result

    async def test_read_page(self, browser_session: str) -> None:
        br = AsyncMock()
        br.get_page_content.return_value = "Page text"
        with _mock_registry(browser=br):
            result = await handlers.browser_read_page()
            assert result == "Page text"

    async def test_click(self, browser_session: str) -> None:
        br = AsyncMock()
        br.click.return_value = {"clicked": True}
        with _mock_registry(browser=br):
            result = await handlers.browser_click("#btn")
            assert "clicked" in result

    async def test_type(self, browser_session: str) -> None:
        br = AsyncMock()
        br.type_text.return_value = {"typed": True}
        with _mock_registry(browser=br):
            result = await handlers.browser_type("#input", "hello")
            assert "typed" in result

    async def test_screenshot(self, browser_session: str) -> None:
        br = AsyncMock()
        br.screenshot.return_value = "base64data"
        with _mock_registry(browser=br):
            result = await handlers.browser_screenshot()
            assert "Screenshot captured" in result

    async def test_evaluate_js(self, browser_session: str) -> None:
        br = AsyncMock()
        br.evaluate.return_value = {"result": 5}
        with _mock_registry(browser=br):
            result = await handlers.browser_evaluate_js("1+4")
            assert "5" in result

    async def test_without_session_raises(self) -> None:
        with pytest.raises(RuntimeError, match="without an active session"):
            await handlers.browser_read_page()


# ── Tools handlers ───────────────────────────────────────────────────


class TestToolsHandlers:
    async def test_search(self) -> None:
        tl = AsyncMock()
        tl.search_tools.return_value = [{"name": "calc", "description": "calculator"}]
        with _mock_registry(tools=tl):
            result = await handlers.tools_search("calc")
            assert "calc" in result

    async def test_search_empty(self) -> None:
        tl = AsyncMock()
        tl.search_tools.return_value = []
        with _mock_registry(tools=tl):
            result = await handlers.tools_search("nothing")
            assert "No tools found" in result

    async def test_invoke(self) -> None:
        tl = AsyncMock()
        tl.invoke_tool.return_value = {"answer": 42}
        with _mock_registry(tools=tl):
            result = await handlers.tools_invoke("calc", '{"x": 1}')
            assert "42" in result

    async def test_invoke_invalid_json(self) -> None:
        tl = AsyncMock()
        tl.invoke_tool.return_value = {"ok": True}
        with _mock_registry(tools=tl):
            result = await handlers.tools_invoke("tool", "not-json")
            tl.invoke_tool.assert_awaited_once_with(tool_name="tool", params={})
            assert "ok" in result


# ── Identity handlers ────────────────────────────────────────────────


class TestIdentityHandlers:
    async def test_get_token(self) -> None:
        ident = AsyncMock()
        ident.get_token.return_value = {"access_token": "tok123"}
        with _mock_registry(identity=ident):
            result = await handlers.identity_get_token("provider1", "scope1,scope2")
            assert "tok123" in result

    async def test_get_token_no_scopes(self) -> None:
        ident = AsyncMock()
        ident.get_token.return_value = {"access_token": "tok"}
        with _mock_registry(identity=ident):
            result = await handlers.identity_get_token("provider1")
            assert "tok" in result

    async def test_get_api_key(self) -> None:
        ident = AsyncMock()
        ident.get_api_key.return_value = {"api_key": "key123"}
        with _mock_registry(identity=ident):
            result = await handlers.identity_get_api_key("provider1")
            assert "key123" in result


# ── Task handlers ────────────────────────────────────────────────────


def _make_task(**kwargs: object) -> Task:
    defaults = {
        "id": "t1",
        "team_run_id": "run1",
        "title": "Test task",
        "description": "",
        "status": "pending",
        "created_by": "agent",
        "depends_on": [],
        "priority": 0,
    }
    defaults.update(kwargs)
    return Task(**defaults)  # type: ignore[arg-type]


class TestTaskHandlers:
    async def test_task_create(self, team_context: tuple[str, str]) -> None:
        ts = AsyncMock()
        ts.create_task.return_value = _make_task()
        with _mock_registry(tasks=ts):
            result = await handlers.task_create("Test task", priority=1)
            data = json.loads(result)
            assert data["id"] == "t1"

    async def test_task_list(self, team_context: tuple[str, str]) -> None:
        ts = AsyncMock()
        ts.list_tasks.return_value = [_make_task(depends_on=["t0"], assigned_to="worker1")]
        with _mock_registry(tasks=ts):
            result = await handlers.task_list()
            assert "t1" in result
            assert "depends" in result

    async def test_task_list_empty(self, team_context: tuple[str, str]) -> None:
        ts = AsyncMock()
        ts.list_tasks.return_value = []
        with _mock_registry(tasks=ts):
            result = await handlers.task_list()
            assert "No tasks found" in result

    async def test_task_get_found(self, team_context: tuple[str, str]) -> None:
        ts = AsyncMock()
        ts.get_task.return_value = _make_task()
        with _mock_registry(tasks=ts):
            result = await handlers.task_get("t1")
            data = json.loads(result)
            assert data["id"] == "t1"

    async def test_task_get_not_found(self, team_context: tuple[str, str]) -> None:
        ts = AsyncMock()
        ts.get_task.return_value = None
        with _mock_registry(tasks=ts):
            result = await handlers.task_get("t1")
            assert "not found" in result

    async def test_task_claim_ok(self, team_context: tuple[str, str]) -> None:
        ts = AsyncMock()
        ts.claim_task.return_value = _make_task()
        with _mock_registry(tasks=ts):
            result = await handlers.task_claim("t1")
            assert "Claimed" in result

    async def test_task_claim_fail(self, team_context: tuple[str, str]) -> None:
        ts = AsyncMock()
        ts.claim_task.return_value = None
        with _mock_registry(tasks=ts):
            result = await handlers.task_claim("t1")
            assert "Could not claim" in result

    async def test_task_update_ok(self, team_context: tuple[str, str]) -> None:
        ts = AsyncMock()
        ts.update_task.return_value = _make_task(status="done")
        with _mock_registry(tasks=ts):
            result = await handlers.task_update("t1", status="done")
            assert "Updated" in result

    async def test_task_update_not_found(self, team_context: tuple[str, str]) -> None:
        ts = AsyncMock()
        ts.update_task.return_value = None
        with _mock_registry(tasks=ts):
            result = await handlers.task_update("t1")
            assert "not found" in result

    async def test_task_add_note_ok(self, team_context: tuple[str, str]) -> None:
        ts = AsyncMock()
        ts.add_note.return_value = _make_task()
        with _mock_registry(tasks=ts):
            result = await handlers.task_add_note("t1", "my note")
            assert "Added note" in result

    async def test_task_add_note_not_found(self, team_context: tuple[str, str]) -> None:
        ts = AsyncMock()
        ts.add_note.return_value = None
        with _mock_registry(tasks=ts):
            result = await handlers.task_add_note("t1", "note")
            assert "not found" in result

    async def test_task_get_available(self, team_context: tuple[str, str]) -> None:
        ts = AsyncMock()
        ts.get_available.return_value = [_make_task()]
        with _mock_registry(tasks=ts):
            result = await handlers.task_get_available()
            assert "t1" in result

    async def test_task_get_available_empty(self, team_context: tuple[str, str]) -> None:
        ts = AsyncMock()
        ts.get_available.return_value = []
        with _mock_registry(tasks=ts):
            result = await handlers.task_get_available()
            assert "No available tasks" in result

    async def test_without_team_context_raises(self) -> None:
        with pytest.raises(RuntimeError, match="task_board tool called outside a team run"):
            await handlers.task_list()


# ── Agent management handlers ────────────────────────────────────────


class TestAgentManagementHandlers:
    async def test_agent_create(self) -> None:
        store = AsyncMock()
        store.get.return_value = None
        result = await handlers.agent_create(store, "new-agent", "claude", primitives='{"memory": {"enabled": true}}')
        assert "Created agent" in result
        store.create.assert_awaited_once()

    async def test_agent_create_already_exists(self) -> None:
        store = AsyncMock()
        store.get.return_value = MagicMock()
        result = await handlers.agent_create(store, "existing", "claude")
        assert "already exists" in result

    async def test_agent_create_invalid_json(self) -> None:
        store = AsyncMock()
        result = await handlers.agent_create(store, "a", "m", primitives="not-json")
        assert "Error" in result

    async def test_agent_create_bool_primitive(self) -> None:
        store = AsyncMock()
        store.get.return_value = None
        result = await handlers.agent_create(store, "a", "m", primitives='{"memory": true}')
        assert "Created agent" in result

    async def test_agent_list(self) -> None:
        agent = MagicMock()
        agent.name = "a1"
        agent.description = "desc"
        agent.primitives = {"memory": MagicMock(enabled=True)}
        store = AsyncMock()
        store.list.return_value = [agent]
        result = await handlers.agent_list(store)
        assert "a1" in result

    async def test_agent_list_empty(self) -> None:
        store = AsyncMock()
        store.list.return_value = []
        result = await handlers.agent_list(store)
        assert "No agents exist" in result

    async def test_agent_list_primitives(self) -> None:
        result = await handlers.agent_list_primitives()
        assert "Available primitives" in result

    async def test_agent_delete_found(self) -> None:
        store = AsyncMock()
        store.delete.return_value = True
        result = await handlers.agent_delete(store, "a1")
        assert "Deleted" in result

    async def test_agent_delete_not_found(self) -> None:
        store = AsyncMock()
        store.delete.return_value = False
        result = await handlers.agent_delete(store, "a1")
        assert "not found" in result

    async def test_agent_delegate_to(self) -> None:
        store = AsyncMock()
        spec = MagicMock()
        store.get.return_value = spec
        runner = AsyncMock()
        response = MagicMock()
        response.response = "done"
        response.artifacts = []
        runner.run.return_value = response
        result = await handlers.agent_delegate_to(store, runner, 0, "agent1", "do stuff")
        assert result == "done"

    async def test_agent_delegate_to_not_found(self) -> None:
        store = AsyncMock()
        store.get.return_value = None
        runner = AsyncMock()
        result = await handlers.agent_delegate_to(store, runner, 0, "missing", "msg")
        assert "not found" in result

    async def test_agent_delegate_to_with_artifacts(self) -> None:
        store = AsyncMock()
        store.get.return_value = MagicMock()
        runner = AsyncMock()
        artifact = MagicMock()
        artifact.tool_name = "code_execute"
        artifact.tool_input = {"code": "print(1)"}
        artifact.output = "1"
        response = MagicMock()
        response.response = "result"
        response.artifacts = [artifact]
        runner.run.return_value = response
        result = await handlers.agent_delegate_to(store, runner, 0, "a", "msg")
        assert "code_execute" in result
        assert "print(1)" in result

    async def test_agent_delegate_to_error(self) -> None:
        store = AsyncMock()
        store.get.return_value = MagicMock()
        runner = AsyncMock()
        runner.run.side_effect = RuntimeError("boom")
        result = await handlers.agent_delegate_to(store, runner, 0, "a", "msg")
        assert "boom" in result
