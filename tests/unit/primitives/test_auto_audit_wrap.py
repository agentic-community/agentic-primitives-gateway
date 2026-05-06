"""Intent tests: per-primitive ``__init_subclass__`` auto-wraps every
subclass for operation-specific audit events — backends don't emit them
themselves.

Covers the five primitives called out in issue #27 (memory, tools,
browser, code_interpreter, identity) plus the three that were extended
alongside (policy, evaluations, tasks, observability).  Each case
verifies:

- A fresh subclass defined inside the test emits the specific
  ``AuditAction`` on success.
- The same subclass emits an ERROR-outcome event on exception.

The "specific" part is what distinguishes this from ``provider.call``
— an agent-tool caller that bypasses the REST route still lands the
right action in the audit stream.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.emit import set_audit_router
from agentic_primitives_gateway.audit.models import AuditAction, AuditEvent, AuditOutcome
from agentic_primitives_gateway.audit.router import AuditRouter
from agentic_primitives_gateway.models.knowledge import DocumentInfo, IngestDocument, IngestResult, RetrievedChunk
from agentic_primitives_gateway.models.memory import MemoryRecord, SearchResult
from agentic_primitives_gateway.models.tasks import Task, TaskNote
from agentic_primitives_gateway.primitives.browser.base import BrowserProvider
from agentic_primitives_gateway.primitives.code_interpreter.base import CodeInterpreterProvider
from agentic_primitives_gateway.primitives.evaluations.base import EvaluationsProvider
from agentic_primitives_gateway.primitives.identity.base import IdentityProvider
from agentic_primitives_gateway.primitives.knowledge.base import KnowledgeProvider
from agentic_primitives_gateway.primitives.llm.base import LLMProvider
from agentic_primitives_gateway.primitives.memory.base import MemoryProvider
from agentic_primitives_gateway.primitives.observability.base import ObservabilityProvider
from agentic_primitives_gateway.primitives.policy.base import PolicyProvider
from agentic_primitives_gateway.primitives.tasks.base import TasksProvider
from agentic_primitives_gateway.primitives.tools.base import ToolsProvider


class _CollectorSink(AuditSink):
    def __init__(self) -> None:
        self.name = "collector"
        self.events: list[AuditEvent] = []

    async def emit(self, event: AuditEvent) -> None:
        self.events.append(event)


@pytest.fixture
async def audit_sink() -> AsyncIterator[_CollectorSink]:
    sink = _CollectorSink()
    router = AuditRouter([sink])
    await router.start()
    set_audit_router(router)
    try:
        yield sink
    finally:
        await router.shutdown(timeout=1.0)
        set_audit_router(None)


async def _events_for(sink: _CollectorSink, action: str) -> list[AuditEvent]:
    await asyncio.sleep(0.02)
    return [e for e in sink.events if e.action == action]


# ── Memory ───────────────────────────────────────────────────────────


class _ProbeMemoryProvider(MemoryProvider):
    async def store(
        self,
        namespace: str,
        key: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord:
        return MemoryRecord(namespace=namespace, key=key, content=content, metadata=metadata or {})

    async def retrieve(self, namespace: str, key: str) -> MemoryRecord | None:
        return None

    async def search(
        self,
        namespace: str,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        return []

    async def delete(self, namespace: str, key: str) -> bool:
        return True

    async def list_memories(
        self,
        namespace: str,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        return []

    async def create_event(
        self,
        actor_id: str,
        session_id: str,
        messages: list[tuple[str, str]],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {"event_id": "evt-1"}

    async def create_memory_resource(
        self,
        name: str,
        *,
        strategies: list[dict[str, Any]] | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        return {"memory_id": "mem-1", "name": name}


class _FailingMemoryProvider(_ProbeMemoryProvider):
    async def store(
        self,
        namespace: str,
        key: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord:
        raise RuntimeError("store boom")

    async def delete(self, namespace: str, key: str) -> bool:
        raise RuntimeError("delete boom")


class TestMemoryAuditWrap:
    async def test_store_emits_record_write(self, audit_sink: _CollectorSink) -> None:
        provider = _ProbeMemoryProvider()
        await provider.store("ns", "k1", "hello", {})
        events = await _events_for(audit_sink, AuditAction.MEMORY_RECORD_WRITE)
        assert len(events) == 1
        assert events[0].outcome == AuditOutcome.SUCCESS
        assert events[0].resource_id == "ns/k1"
        assert events[0].metadata["namespace"] == "ns"

    async def test_delete_emits_record_delete(self, audit_sink: _CollectorSink) -> None:
        provider = _ProbeMemoryProvider()
        await provider.delete("ns", "k1")
        events = await _events_for(audit_sink, AuditAction.MEMORY_RECORD_DELETE)
        assert len(events) == 1
        assert events[0].outcome == AuditOutcome.SUCCESS
        assert events[0].metadata["deleted"] is True

    async def test_create_event_emits_event_append(self, audit_sink: _CollectorSink) -> None:
        provider = _ProbeMemoryProvider()
        await provider.create_event("actor-1", "sess-1", [("user", "hi")])
        events = await _events_for(audit_sink, AuditAction.MEMORY_EVENT_APPEND)
        assert len(events) == 1
        assert events[0].metadata["event_id"] == "evt-1"
        assert events[0].metadata["message_count"] == 1

    async def test_create_memory_resource_emits(self, audit_sink: _CollectorSink) -> None:
        provider = _ProbeMemoryProvider()
        await provider.create_memory_resource("mymem")
        events = await _events_for(audit_sink, AuditAction.MEMORY_RESOURCE_CREATE)
        assert len(events) == 1
        assert events[0].metadata["memory_id"] == "mem-1"

    async def test_failure_emits_error(self, audit_sink: _CollectorSink) -> None:
        provider = _FailingMemoryProvider()
        with pytest.raises(RuntimeError):
            await provider.store("ns", "k1", "x")
        with pytest.raises(RuntimeError):
            await provider.delete("ns", "k1")
        await asyncio.sleep(0.02)

        write_errors = [e for e in audit_sink.events if e.action == AuditAction.MEMORY_RECORD_WRITE]
        delete_errors = [e for e in audit_sink.events if e.action == AuditAction.MEMORY_RECORD_DELETE]
        assert write_errors[0].outcome == AuditOutcome.ERROR
        assert write_errors[0].metadata["error_type"] == "RuntimeError"
        assert delete_errors[0].outcome == AuditOutcome.ERROR


# ── Tools ────────────────────────────────────────────────────────────


class _ProbeToolsProvider(ToolsProvider):
    async def register_tool(self, tool_def: dict[str, Any]) -> None:
        return None

    async def list_tools(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return []

    async def invoke_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"result": "ok"}

    async def delete_tool(self, tool_name: str) -> None:
        return None

    async def register_server(self, server_config: dict[str, Any]) -> dict[str, Any]:
        return {"server_id": "srv-1"}


class _FailingToolsProvider(_ProbeToolsProvider):
    async def invoke_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("invoke boom")


class TestToolsAuditWrap:
    async def test_invoke_emits_tool_call(self, audit_sink: _CollectorSink) -> None:
        provider = _ProbeToolsProvider()
        await provider.invoke_tool("search", {"q": "x"})
        events = await _events_for(audit_sink, AuditAction.TOOL_CALL)
        assert len(events) == 1
        assert events[0].outcome == AuditOutcome.SUCCESS
        # Every provider-boundary event must carry ``layer=primitive``
        # so operators can split it from the route-layer ``audit_mutation``
        # event that shares the same action + request_id.
        assert events[0].metadata["layer"] == "primitive"
        assert events[0].metadata["tool_name"] == "search"

    async def test_register_tool_emits(self, audit_sink: _CollectorSink) -> None:
        provider = _ProbeToolsProvider()
        await provider.register_tool({"name": "mytool"})
        events = await _events_for(audit_sink, AuditAction.TOOL_REGISTER)
        assert len(events) == 1
        assert events[0].resource_id == "mytool"

    async def test_register_server_emits(self, audit_sink: _CollectorSink) -> None:
        provider = _ProbeToolsProvider()
        await provider.register_server({"name": "srv-a"})
        events = await _events_for(audit_sink, AuditAction.TOOL_SERVER_REGISTER)
        assert len(events) == 1
        assert events[0].resource_id == "srv-a"

    async def test_failure_emits_error(self, audit_sink: _CollectorSink) -> None:
        provider = _FailingToolsProvider()
        with pytest.raises(RuntimeError):
            await provider.invoke_tool("bad", {})
        events = await _events_for(audit_sink, AuditAction.TOOL_CALL)
        assert len(events) == 1
        assert events[0].outcome == AuditOutcome.ERROR
        assert events[0].metadata["error_type"] == "RuntimeError"


# ── Browser ──────────────────────────────────────────────────────────


class _ProbeBrowserProvider(BrowserProvider):
    async def start_session(
        self,
        session_id: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {"session_id": session_id or "s1"}

    async def stop_session(self, session_id: str) -> None:
        return None

    async def get_session(self, session_id: str) -> dict[str, Any]:
        return {"session_id": session_id}

    async def list_sessions(self, status: str | None = None) -> list[dict[str, Any]]:
        return []

    async def get_live_view_url(self, session_id: str, expires: int = 300) -> str:
        return "https://example.test"

    async def navigate(self, session_id: str, url: str) -> dict[str, Any]:
        return {"ok": True}

    async def click(self, session_id: str, selector: str) -> dict[str, Any]:
        return {"ok": True}

    async def type_text(self, session_id: str, selector: str, text: str) -> dict[str, Any]:
        return {"ok": True}

    async def evaluate(self, session_id: str, expression: str) -> Any:
        return 42


class _FailingBrowserProvider(_ProbeBrowserProvider):
    async def navigate(self, session_id: str, url: str) -> dict[str, Any]:
        raise RuntimeError("nav boom")


class TestBrowserAuditWrap:
    async def test_navigate_emits(self, audit_sink: _CollectorSink) -> None:
        provider = _ProbeBrowserProvider()
        await provider.navigate("s1", "https://example.test")
        events = await _events_for(audit_sink, AuditAction.BROWSER_NAVIGATE)
        assert len(events) == 1
        assert events[0].metadata["url"] == "https://example.test"

    async def test_click_emits(self, audit_sink: _CollectorSink) -> None:
        provider = _ProbeBrowserProvider()
        await provider.click("s1", "#btn")
        events = await _events_for(audit_sink, AuditAction.BROWSER_CLICK)
        assert len(events) == 1
        assert events[0].metadata["selector"] == "#btn"

    async def test_type_emits_length_not_text(self, audit_sink: _CollectorSink) -> None:
        provider = _ProbeBrowserProvider()
        await provider.type_text("s1", "input", "supersecret")
        events = await _events_for(audit_sink, AuditAction.BROWSER_TYPE)
        assert len(events) == 1
        assert events[0].metadata["text_length"] == len("supersecret")
        # Contract: typed text must NEVER appear in metadata.
        for v in events[0].metadata.values():
            assert "supersecret" not in str(v)

    async def test_evaluate_emits_length_not_expression(self, audit_sink: _CollectorSink) -> None:
        provider = _ProbeBrowserProvider()
        await provider.evaluate("s1", "document.cookie")
        events = await _events_for(audit_sink, AuditAction.BROWSER_EVALUATE)
        assert len(events) == 1
        assert events[0].metadata["expression_length"] == len("document.cookie")
        for v in events[0].metadata.values():
            assert "document.cookie" not in str(v)

    async def test_failure_emits_error(self, audit_sink: _CollectorSink) -> None:
        provider = _FailingBrowserProvider()
        with pytest.raises(RuntimeError):
            await provider.navigate("s1", "https://x")
        events = await _events_for(audit_sink, AuditAction.BROWSER_NAVIGATE)
        assert events[0].outcome == AuditOutcome.ERROR


# ── Code interpreter ────────────────────────────────────────────────


class _ProbeCodeInterpreter(CodeInterpreterProvider):
    async def start_session(
        self,
        session_id: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {"session_id": session_id or "s1"}

    async def stop_session(self, session_id: str) -> None:
        return None

    async def execute(
        self,
        session_id: str,
        code: str,
        language: str = "python",
    ) -> dict[str, Any]:
        return {"stdout": "out", "stderr": "", "success": True}

    async def upload_file(
        self,
        session_id: str,
        filename: str,
        content: bytes,
    ) -> dict[str, Any]:
        return {"filename": filename}

    async def download_file(self, session_id: str, filename: str) -> bytes:
        return b"payload"

    async def list_sessions(self, status: str | None = None) -> list[dict[str, Any]]:
        return []


class _FailingCodeInterpreter(_ProbeCodeInterpreter):
    async def execute(
        self,
        session_id: str,
        code: str,
        language: str = "python",
    ) -> dict[str, Any]:
        raise RuntimeError("exec boom")


class TestCodeInterpreterAuditWrap:
    async def test_execute_emits(self, audit_sink: _CollectorSink) -> None:
        provider = _ProbeCodeInterpreter()
        await provider.execute("s1", "print('hi')")
        events = await _events_for(audit_sink, AuditAction.CODE_EXECUTE)
        assert len(events) == 1
        assert events[0].metadata["code_length"] == len("print('hi')")
        assert events[0].metadata["stdout_length"] == 3
        # Code body must NEVER appear.
        for v in events[0].metadata.values():
            assert "print" not in str(v).replace("print_tokens", "")  # avoid false positives in metric labels

    async def test_upload_emits(self, audit_sink: _CollectorSink) -> None:
        provider = _ProbeCodeInterpreter()
        await provider.upload_file("s1", "data.csv", b"payload-of-8")
        events = await _events_for(audit_sink, AuditAction.CODE_FILE_UPLOAD)
        assert len(events) == 1
        assert events[0].metadata["size_bytes"] == len(b"payload-of-8")

    async def test_download_emits(self, audit_sink: _CollectorSink) -> None:
        provider = _ProbeCodeInterpreter()
        await provider.download_file("s1", "data.csv")
        events = await _events_for(audit_sink, AuditAction.CODE_FILE_DOWNLOAD)
        assert len(events) == 1
        assert events[0].metadata["size_bytes"] == len(b"payload")

    async def test_failure_emits_error(self, audit_sink: _CollectorSink) -> None:
        provider = _FailingCodeInterpreter()
        with pytest.raises(RuntimeError):
            await provider.execute("s1", "x")
        events = await _events_for(audit_sink, AuditAction.CODE_EXECUTE)
        assert events[0].outcome == AuditOutcome.ERROR


# ── Identity ─────────────────────────────────────────────────────────


class _ProbeIdentityProvider(IdentityProvider):
    async def get_token(
        self,
        credential_provider: str,
        workload_token: str,
        *,
        auth_flow: str = "M2M",
        scopes: list[str] | None = None,
        callback_url: str | None = None,
        force_auth: bool = False,
        session_uri: str | None = None,
        custom_state: str | None = None,
        custom_parameters: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return {"access_token": "secret-token-should-not-leak", "token_type": "Bearer"}

    async def get_api_key(
        self,
        credential_provider: str,
        workload_token: str,
    ) -> dict[str, Any]:
        return {"api_key": "sk-secret-should-not-leak"}

    async def get_workload_token(
        self,
        workload_name: str,
        *,
        user_token: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        return {"token": "x"}

    async def list_credential_providers(self) -> list[dict[str, Any]]:
        return []

    async def create_credential_provider(
        self,
        name: str,
        provider_type: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        return {"name": name, "provider_type": provider_type}

    async def create_workload_identity(
        self,
        name: str,
        *,
        allowed_return_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        return {"name": name}


class _FailingIdentityProvider(_ProbeIdentityProvider):
    async def get_token(
        self,
        credential_provider: str,
        workload_token: str,
        *,
        auth_flow: str = "M2M",
        scopes: list[str] | None = None,
        callback_url: str | None = None,
        force_auth: bool = False,
        session_uri: str | None = None,
        custom_state: str | None = None,
        custom_parameters: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        raise RuntimeError("token boom")


class TestIdentityAuditWrap:
    async def test_get_token_emits_credential_read_without_token(self, audit_sink: _CollectorSink) -> None:
        provider = _ProbeIdentityProvider()
        await provider.get_token("slack", "workload-jwt")
        events = await _events_for(audit_sink, AuditAction.CREDENTIAL_READ)
        assert len(events) == 1
        assert events[0].metadata["credential_provider"] == "slack"
        assert events[0].metadata["kind"] == "token"
        assert events[0].metadata["result_kind"] == "access_token"
        # Neither the returned token nor the workload JWT must appear.
        for v in events[0].metadata.values():
            assert "secret-token-should-not-leak" not in str(v)
            assert "workload-jwt" not in str(v)

    async def test_get_api_key_emits(self, audit_sink: _CollectorSink) -> None:
        provider = _ProbeIdentityProvider()
        await provider.get_api_key("stripe", "workload-jwt")
        events = await _events_for(audit_sink, AuditAction.CREDENTIAL_READ)
        assert len(events) == 1
        assert events[0].metadata["kind"] == "api_key"
        for v in events[0].metadata.values():
            assert "sk-secret-should-not-leak" not in str(v)

    async def test_create_credential_provider_emits(self, audit_sink: _CollectorSink) -> None:
        provider = _ProbeIdentityProvider()
        await provider.create_credential_provider("slack", "oauth2", {})
        events = await _events_for(audit_sink, AuditAction.IDENTITY_CREDENTIAL_PROVIDER_CREATE)
        assert len(events) == 1
        assert events[0].metadata["provider_type"] == "oauth2"

    async def test_create_workload_identity_emits(self, audit_sink: _CollectorSink) -> None:
        provider = _ProbeIdentityProvider()
        await provider.create_workload_identity("my-agent")
        events = await _events_for(audit_sink, AuditAction.IDENTITY_WORKLOAD_CREATE)
        assert len(events) == 1
        assert events[0].resource_id == "my-agent"

    async def test_failure_emits_error(self, audit_sink: _CollectorSink) -> None:
        provider = _FailingIdentityProvider()
        with pytest.raises(RuntimeError):
            await provider.get_token("slack", "wt")
        events = await _events_for(audit_sink, AuditAction.CREDENTIAL_READ)
        assert events[0].outcome == AuditOutcome.ERROR
        assert events[0].metadata["error_type"] == "RuntimeError"


# ── Policy ───────────────────────────────────────────────────────────


class _ProbePolicyProvider(PolicyProvider):
    async def create_policy_engine(
        self,
        name: str,
        description: str = "",
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {"engine_id": "eng-1", "name": name}

    async def get_policy_engine(self, engine_id: str) -> dict[str, Any]:
        return {"engine_id": engine_id}

    async def delete_policy_engine(self, engine_id: str) -> None:
        return None

    async def list_policy_engines(
        self,
        max_results: int = 100,
        next_token: str | None = None,
    ) -> dict[str, Any]:
        return {"engines": []}

    async def create_policy(
        self,
        engine_id: str,
        policy_body: str,
        description: str = "",
    ) -> dict[str, Any]:
        return {"policy_id": "pol-1"}

    async def get_policy(self, engine_id: str, policy_id: str) -> dict[str, Any]:
        return {}

    async def update_policy(
        self,
        engine_id: str,
        policy_id: str,
        policy_body: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        return {}

    async def delete_policy(self, engine_id: str, policy_id: str) -> None:
        return None

    async def list_policies(
        self,
        engine_id: str,
        max_results: int = 100,
        next_token: str | None = None,
    ) -> dict[str, Any]:
        return {"policies": []}


class TestPolicyAuditWrap:
    async def test_create_policy_emits_with_body_length(self, audit_sink: _CollectorSink) -> None:
        provider = _ProbePolicyProvider()
        body = "permit(principal, action, resource);"
        await provider.create_policy("eng-1", body)
        events = await _events_for(audit_sink, AuditAction.POLICY_CREATE)
        # There should be one event with kind=policy; engine-create is separate.
        policy_events = [e for e in events if e.metadata.get("kind") == "policy"]
        assert len(policy_events) == 1
        assert policy_events[0].metadata["body_length"] == len(body)
        for v in policy_events[0].metadata.values():
            assert "permit" not in str(v)

    async def test_create_engine_emits(self, audit_sink: _CollectorSink) -> None:
        provider = _ProbePolicyProvider()
        await provider.create_policy_engine("myeng")
        events = await _events_for(audit_sink, AuditAction.POLICY_CREATE)
        engine_events = [e for e in events if e.metadata.get("kind") == "engine"]
        assert len(engine_events) == 1
        assert engine_events[0].metadata["engine_id"] == "eng-1"


# ── Evaluations ──────────────────────────────────────────────────────


class _ProbeEvaluationsProvider(EvaluationsProvider):
    async def create_evaluator(
        self,
        name: str,
        evaluator_type: str,
        config: dict[str, Any] | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        return {"evaluator_id": "eval-1", "name": name}

    async def get_evaluator(self, evaluator_id: str) -> dict[str, Any]:
        return {}

    async def update_evaluator(
        self,
        evaluator_id: str,
        config: dict[str, Any] | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        return {}

    async def delete_evaluator(self, evaluator_id: str) -> None:
        return None

    async def list_evaluators(
        self,
        max_results: int = 100,
        next_token: str | None = None,
    ) -> dict[str, Any]:
        return {"evaluators": []}

    async def evaluate(
        self,
        evaluator_id: str,
        target: str | None = None,
        input_data: str | None = None,
        output_data: str | None = None,
        expected_output: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {"score": 0.9}

    async def create_score(
        self,
        *,
        name: str,
        value: float | str,
        trace_id: str | None = None,
        observation_id: str | None = None,
        comment: str | None = None,
        data_type: str | None = None,
        config_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {"score_id": "s-1", "name": name}


class TestEvaluationsAuditWrap:
    async def test_create_evaluator_emits(self, audit_sink: _CollectorSink) -> None:
        provider = _ProbeEvaluationsProvider()
        await provider.create_evaluator("judge", "llm-judge")
        events = await _events_for(audit_sink, AuditAction.EVALUATOR_CREATE)
        assert len(events) == 1
        assert events[0].metadata["evaluator_id"] == "eval-1"

    async def test_create_score_emits(self, audit_sink: _CollectorSink) -> None:
        provider = _ProbeEvaluationsProvider()
        await provider.create_score(name="quality", value=0.8, trace_id="t-1")
        events = await _events_for(audit_sink, AuditAction.SCORE_CREATE)
        assert len(events) == 1
        assert events[0].metadata["trace_id"] == "t-1"


# ── Tasks ────────────────────────────────────────────────────────────


class _ProbeTasksProvider(TasksProvider):
    async def create_task(
        self,
        team_run_id: str,
        title: str,
        *,
        description: str = "",
        created_by: str = "",
        depends_on: list[str] | None = None,
        priority: int = 0,
        suggested_worker: str | None = None,
    ) -> Task:
        return Task(
            id="task-1",
            team_run_id=team_run_id,
            title=title,
            description=description,
            created_by=created_by,
            depends_on=depends_on or [],
            priority=priority,
            suggested_worker=suggested_worker,
        )

    async def get_task(self, team_run_id: str, task_id: str) -> Task | None:
        return None

    async def list_tasks(
        self,
        team_run_id: str,
        *,
        status: str | None = None,
        assigned_to: str | None = None,
    ) -> list[Task]:
        return []

    async def claim_task(
        self,
        team_run_id: str,
        task_id: str,
        agent_name: str,
    ) -> Task | None:
        return Task(id=task_id, team_run_id=team_run_id, title="t", status="in_progress", assigned_to=agent_name)

    async def update_task(
        self,
        team_run_id: str,
        task_id: str,
        *,
        status: str | None = None,
        result: str | None = None,
    ) -> Task | None:
        return Task(id=task_id, team_run_id=team_run_id, title="t", status=status or "done")

    async def add_note(
        self,
        team_run_id: str,
        task_id: str,
        note: TaskNote,
    ) -> Task | None:
        return Task(id=task_id, team_run_id=team_run_id, title="t")


class _LosingClaimTasksProvider(_ProbeTasksProvider):
    async def claim_task(
        self,
        team_run_id: str,
        task_id: str,
        agent_name: str,
    ) -> Task | None:
        return None  # already claimed / missing


class TestTasksAuditWrap:
    async def test_create_task_emits(self, audit_sink: _CollectorSink) -> None:
        provider = _ProbeTasksProvider()
        await provider.create_task("run-1", "do the thing", created_by="worker-a")
        events = await _events_for(audit_sink, AuditAction.TASK_CREATE)
        assert len(events) == 1
        assert events[0].resource_id == "task-1"
        assert events[0].metadata["team_run_id"] == "run-1"

    async def test_claim_success_is_success(self, audit_sink: _CollectorSink) -> None:
        provider = _ProbeTasksProvider()
        await provider.claim_task("run-1", "task-1", "worker-a")
        events = await _events_for(audit_sink, AuditAction.TASK_CLAIM)
        assert len(events) == 1
        assert events[0].outcome == AuditOutcome.SUCCESS
        assert events[0].metadata["claimed"] is True

    async def test_claim_contention_is_failure(self, audit_sink: _CollectorSink) -> None:
        """Losing the race is not an exception — but we still want it
        visible as a failure outcome so dashboards surface contention."""
        provider = _LosingClaimTasksProvider()
        result = await provider.claim_task("run-1", "task-1", "worker-a")
        assert result is None
        events = await _events_for(audit_sink, AuditAction.TASK_CLAIM)
        assert len(events) == 1
        assert events[0].outcome == AuditOutcome.FAILURE
        assert events[0].metadata["claimed"] is False


# ── Observability ────────────────────────────────────────────────────


class _ProbeObservabilityProvider(ObservabilityProvider):
    async def ingest_trace(self, trace: dict[str, Any]) -> None:
        return None

    async def ingest_log(self, log_entry: dict[str, Any]) -> None:
        return None

    async def query_traces(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return []

    async def update_trace(
        self,
        trace_id: str,
        *,
        name: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        input: Any = None,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        return {"trace_id": trace_id}


class TestObservabilityAuditWrap:
    async def test_ingest_trace_emits(self, audit_sink: _CollectorSink) -> None:
        provider = _ProbeObservabilityProvider()
        await provider.ingest_trace({"trace_id": "tr-1"})
        events = await _events_for(audit_sink, AuditAction.TRACE_INGEST)
        assert len(events) == 1
        assert events[0].resource_id == "tr-1"

    async def test_update_trace_emits(self, audit_sink: _CollectorSink) -> None:
        provider = _ProbeObservabilityProvider()
        await provider.update_trace("tr-1", name="renamed")
        events = await _events_for(audit_sink, AuditAction.TRACE_UPDATE)
        assert len(events) == 1
        assert events[0].resource_id == "tr-1"


# ── Cross-cutting contracts ──────────────────────────────────────────


class _PartialMemoryProvider(MemoryProvider):
    """Minimal subclass that never overrides the optional methods —
    exercising the ``NotImplementedError`` passthrough path.
    """

    async def store(
        self,
        namespace: str,
        key: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord:
        return MemoryRecord(namespace=namespace, key=key, content=content, metadata=metadata or {})

    async def retrieve(self, namespace: str, key: str) -> MemoryRecord | None:
        return None

    async def search(
        self,
        namespace: str,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        return []

    async def delete(self, namespace: str, key: str) -> bool:
        return False

    async def list_memories(
        self,
        namespace: str,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        return []


class _WrappedRaisesNotImplementedProvider(_PartialMemoryProvider):
    """Subclass that overrides ``create_event`` (so the wrapper IS
    applied) and then raises ``NotImplementedError`` from within —
    exercising the wrapper's ``except NotImplementedError: raise``
    clause directly.
    """

    async def create_event(
        self,
        actor_id: str,
        session_id: str,
        messages: list[tuple[str, str]],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError("not yet")


class TestCrossCuttingContracts:
    """Contracts that apply to every ABC's ``__init_subclass__`` hook,
    regardless of primitive.  These catch refactors that silently break
    the pattern."""

    async def test_not_implemented_from_unwrapped_method(self, audit_sink: _CollectorSink) -> None:
        """Optional methods that the subclass doesn't override default
        to the ABC's ``NotImplementedError`` — no wrapper runs, so no
        provider-boundary event is emitted.  The route layer converts
        this to HTTP 501.
        """
        provider = _PartialMemoryProvider()
        with pytest.raises(NotImplementedError):
            await provider.create_event("actor", "sess", [("user", "hi")])
        await asyncio.sleep(0.02)
        event_append = [e for e in audit_sink.events if e.action == AuditAction.MEMORY_EVENT_APPEND]
        assert event_append == []

    async def test_not_implemented_passthrough_in_wrapper(self, audit_sink: _CollectorSink) -> None:
        """Contract: when a subclass *overrides* the method AND raises
        ``NotImplementedError``, the wrapper re-raises WITHOUT emitting
        an ERROR event.  This is what ``except NotImplementedError: raise``
        guarantees — a missing ``except`` would turn every 501 into a
        noisy compliance failure event.
        """
        provider = _WrappedRaisesNotImplementedProvider()
        with pytest.raises(NotImplementedError):
            await provider.create_event("actor", "sess", [("user", "hi")])
        await asyncio.sleep(0.02)
        event_append = [e for e in audit_sink.events if e.action == AuditAction.MEMORY_EVENT_APPEND]
        assert event_append == []

    async def test_wrapper_tolerates_nested_subclass(self, audit_sink: _CollectorSink) -> None:
        """A grandchild that doesn't redefine the wrapped method must
        not be double-wrapped.  The ``cls.__dict__`` check guards this
        — one event per call, not two.
        """

        class _GrandchildMemoryProvider(_ProbeMemoryProvider):
            # Deliberately does NOT override ``store``.
            pass

        provider = _GrandchildMemoryProvider()
        await provider.store("ns", "k1", "hello", {})
        events = await _events_for(audit_sink, AuditAction.MEMORY_RECORD_WRITE)
        # Exactly one event — not two from double-wrapping.
        assert len(events) == 1

    async def test_every_event_carries_layer_primitive(self, audit_sink: _CollectorSink) -> None:
        """Operator contract: every provider-boundary event has
        ``metadata.layer == "primitive"`` so route-layer events (which
        carry ``http_method``) can be separated in dashboards.

        Covers all primitives with an ABC-level ``_audit.py`` wrapper:
        memory, tools, browser, code_interpreter, knowledge, llm.
        (Identity / policy / evaluations / tasks / observability share
        the same ``_emit`` helper shape — if the four listed primitives
        carry ``layer`` correctly, the rest do too by construction.)
        """
        memory = _ProbeMemoryProvider()
        tools = _ProbeToolsProvider()
        browser = _ProbeBrowserProvider()
        code = _ProbeCodeInterpreter()
        knowledge = _ProbeKnowledgeProvider()
        llm = _ProbeLLMProvider()

        await memory.store("ns", "k", "v")
        await tools.invoke_tool("t", {})
        await browser.navigate("s", "https://x")
        await code.execute("s", "print(1)")
        await knowledge.retrieve("ns", "q")
        await llm.route_request({"model": "m", "messages": []})
        await asyncio.sleep(0.02)

        provider_boundary = [
            e
            for e in audit_sink.events
            if e.action
            in (
                AuditAction.MEMORY_RECORD_WRITE,
                AuditAction.TOOL_CALL,
                AuditAction.BROWSER_NAVIGATE,
                AuditAction.CODE_EXECUTE,
                AuditAction.KNOWLEDGE_RETRIEVE,
                AuditAction.LLM_GENERATE,
            )
        ]
        assert len(provider_boundary) == 6
        for event in provider_boundary:
            assert event.metadata.get("layer") == "primitive", (
                f"missing layer=primitive on {event.action}: {event.metadata}"
            )


class _ProbeKnowledgeProvider(KnowledgeProvider):
    store_type = "probe"

    async def ingest(self, namespace: str, documents: list[IngestDocument]) -> IngestResult:
        return IngestResult(document_ids=[], ingested=0)

    async def retrieve(
        self,
        namespace: str,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[RetrievedChunk]:
        return []

    async def delete(self, namespace: str, document_id: str) -> bool:
        return True

    async def list_documents(
        self,
        namespace: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DocumentInfo]:
        return []


class _ProbeLLMProvider(LLMProvider):
    async def route_request(self, model_request: dict[str, Any]) -> dict[str, Any]:
        return {"model": "m", "content": "", "usage": {"input_tokens": 1, "output_tokens": 1}}

    async def list_models(self) -> list[dict[str, Any]]:
        return []
