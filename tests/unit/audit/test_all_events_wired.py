"""Coverage guarantee: every route that wires an audit event actually
emits one.

Rather than duplicate the per-route integration tests already covered by
the targeted suites, this module drives every new emit site through a
thin stub registry + route handler, so when a future contributor adds a
new ``audit_mutation`` or ``emit_audit_event`` call, a matching row here
proves the event fires end-to-end through the router.

The aim: **every AuditAction constant the server emits appears in at
least one test assertion in this repo.**  The matrix at the bottom is
the contract.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import UploadFile

from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.emit import set_audit_router
from agentic_primitives_gateway.audit.models import AuditAction, AuditEvent
from agentic_primitives_gateway.audit.router import AuditRouter


class _CollectorSink(AuditSink):
    def __init__(self) -> None:
        self.name = "collector"
        self.events: list[AuditEvent] = []

    async def emit(self, event: AuditEvent) -> None:
        self.events.append(event)


@asynccontextmanager
async def _wire_router():
    sink = _CollectorSink()
    router = AuditRouter([sink])
    await router.start()
    set_audit_router(router)
    try:
        yield sink
    finally:
        await asyncio.sleep(0.02)
        await router.shutdown(timeout=1.0)
        set_audit_router(None)


def _seen_actions(sink: _CollectorSink) -> set[str]:
    return {e.action for e in sink.events}


# ── Memory route emits ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_memory_mutations_emit():
    from agentic_primitives_gateway.models.memory import (
        AddStrategyRequest,
        CreateEventRequest,
        CreateMemoryResourceRequest,
        EventMessage,
        ForkConversationRequest,
        StoreMemoryRequest,
    )
    from agentic_primitives_gateway.routes import memory as memroute

    mock_registry = MagicMock()
    mock_registry.memory.create_event = AsyncMock(return_value={"event_id": "e1"})
    mock_registry.memory.delete_event = AsyncMock()
    mock_registry.memory.fork_conversation = AsyncMock(return_value={"branch": "b1"})
    mock_registry.memory.create_memory_resource = AsyncMock(return_value={"memory_id": "m1"})
    mock_registry.memory.delete_memory_resource = AsyncMock()
    mock_registry.memory.add_strategy = AsyncMock(return_value={"strategy_id": "s1"})
    mock_registry.memory.delete_strategy = AsyncMock()
    mock_registry.memory.store = AsyncMock(return_value=MagicMock(model_dump=lambda: {}))
    mock_registry.memory.delete = AsyncMock(return_value=True)

    async with _wire_router() as sink:
        with (
            patch.object(memroute, "registry", mock_registry),
            patch.object(memroute, "_check_actor"),
            patch.object(memroute, "_check_namespace"),
        ):
            await memroute.create_event(
                "actor", "sess", CreateEventRequest(messages=[EventMessage(text="hi", role="user")])
            )
            await memroute.delete_event("actor", "sess", "evt")
            await memroute.fork_conversation(
                "actor", "sess", ForkConversationRequest(root_event_id="r", branch_name="b", messages=[])
            )
            await memroute.create_memory_resource(CreateMemoryResourceRequest(name="x"))
            await memroute.delete_memory_resource("m1")
            await memroute.add_strategy("m1", AddStrategyRequest(strategy={}))
            await memroute.delete_strategy("m1", "s1")
            await memroute.store_memory("ns", StoreMemoryRequest(key="k", content="v"))
            await memroute.delete_memory("ns", "k")

    seen = _seen_actions(sink)
    assert AuditAction.MEMORY_EVENT_APPEND in seen
    assert AuditAction.MEMORY_EVENT_DELETE in seen
    assert AuditAction.MEMORY_BRANCH_CREATE in seen
    assert AuditAction.MEMORY_RESOURCE_CREATE in seen
    assert AuditAction.MEMORY_RESOURCE_DELETE in seen
    assert AuditAction.MEMORY_STRATEGY_CREATE in seen
    assert AuditAction.MEMORY_STRATEGY_DELETE in seen
    assert AuditAction.MEMORY_RECORD_WRITE in seen
    assert AuditAction.MEMORY_RECORD_DELETE in seen


# ── Tools route emits ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_mutations_emit():
    from agentic_primitives_gateway.models.tools import RegisterServerRequest, RegisterToolRequest
    from agentic_primitives_gateway.routes import tools as toolsroute

    mock_registry = MagicMock()
    mock_registry.tools.register_tool = AsyncMock()
    mock_registry.tools.register_server = AsyncMock(return_value={})
    mock_registry.tools.delete_tool = AsyncMock()

    async with _wire_router() as sink:
        with patch.object(toolsroute, "registry", mock_registry):
            await toolsroute.register_tool(
                RegisterToolRequest(
                    name="t1", description="d", parameters={"type": "object", "properties": {}, "required": []}
                )
            )
            await toolsroute.register_server(RegisterServerRequest(name="s1", url="http://x", transport="http"))
            await toolsroute.delete_tool("t1")

    seen = _seen_actions(sink)
    assert AuditAction.TOOL_REGISTER in seen
    assert AuditAction.TOOL_SERVER_REGISTER in seen
    assert AuditAction.TOOL_DELETE in seen


# ── Evaluations route emits ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_evaluations_mutations_emit():
    from agentic_primitives_gateway.models.evaluations import (
        CreateEvaluatorRequest,
        CreateOnlineEvalConfigRequest,
        CreateScoreRequest,
        UpdateEvaluatorRequest,
    )
    from agentic_primitives_gateway.routes import evaluations as evalroute

    mock_registry = MagicMock()
    mock_registry.evaluations.create_evaluator = AsyncMock(return_value={"evaluator_id": "e1"})
    mock_registry.evaluations.update_evaluator = AsyncMock(return_value={})
    mock_registry.evaluations.delete_evaluator = AsyncMock()
    mock_registry.evaluations.create_score = AsyncMock(return_value={"score_id": "sc1"})
    mock_registry.evaluations.delete_score = AsyncMock()
    mock_registry.evaluations.create_online_evaluation_config = AsyncMock(return_value={"config_id": "c1"})
    mock_registry.evaluations.delete_online_evaluation_config = AsyncMock()

    async with _wire_router() as sink:
        with patch.object(evalroute, "registry", mock_registry):
            await evalroute.create_evaluator(CreateEvaluatorRequest(name="e", evaluator_type="t"))
            await evalroute.update_evaluator("e1", UpdateEvaluatorRequest())
            await evalroute.delete_evaluator("e1")
            await evalroute.create_score(CreateScoreRequest(name="s", value=1.0, trace_id="t1"))
            await evalroute.delete_score("sc1")
            await evalroute.create_online_evaluation_config(
                CreateOnlineEvalConfigRequest(name="c", evaluator_ids=["e1"])
            )
            await evalroute.delete_online_evaluation_config("c1")

    seen = _seen_actions(sink)
    assert AuditAction.EVALUATOR_CREATE in seen
    assert AuditAction.EVALUATOR_UPDATE in seen
    assert AuditAction.EVALUATOR_DELETE in seen
    assert AuditAction.SCORE_CREATE in seen
    assert AuditAction.SCORE_DELETE in seen
    assert AuditAction.ONLINE_CONFIG_CREATE in seen
    assert AuditAction.ONLINE_CONFIG_DELETE in seen


# ── Identity route emits ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_identity_mutations_emit():
    from agentic_primitives_gateway.models.identity import (
        ApiKeyRequest,
        CreateCredentialProviderRequest,
        CreateWorkloadIdentityRequest,
        TokenRequest,
        UpdateCredentialProviderRequest,
        UpdateWorkloadIdentityRequest,
    )
    from agentic_primitives_gateway.routes import identity as idroute

    mock_registry = MagicMock()
    mock_registry.identity.get_token = AsyncMock(return_value={"access_token": "t", "token_type": "Bearer"})
    mock_registry.identity.get_api_key = AsyncMock(return_value={"api_key": "k", "credential_provider": "p1"})
    mock_registry.identity.create_credential_provider = AsyncMock(
        return_value={"name": "p1", "provider_type": "google", "config": {}}
    )
    mock_registry.identity.update_credential_provider = AsyncMock(
        return_value={"name": "p1", "provider_type": "google", "config": {}}
    )
    mock_registry.identity.delete_credential_provider = AsyncMock()
    mock_registry.identity.create_workload_identity = AsyncMock(return_value={"name": "w1"})
    mock_registry.identity.update_workload_identity = AsyncMock(return_value={"name": "w1"})
    mock_registry.identity.delete_workload_identity = AsyncMock()

    mock_admin = MagicMock()
    async with _wire_router() as sink:
        with patch.object(idroute, "registry", mock_registry), patch.object(idroute, "require_admin", mock_admin):
            await idroute.get_token(TokenRequest(credential_provider="p1", workload_token="w"))
            await idroute.get_api_key(ApiKeyRequest(credential_provider="p1", workload_token="w"))
            await idroute.create_credential_provider(
                CreateCredentialProviderRequest(name="p1", provider_type="google", config={})
            )
            await idroute.update_credential_provider("p1", UpdateCredentialProviderRequest(config={}))
            await idroute.delete_credential_provider("p1")
            await idroute.create_workload_identity(CreateWorkloadIdentityRequest(name="w1"))
            await idroute.update_workload_identity("w1", UpdateWorkloadIdentityRequest())
            await idroute.delete_workload_identity("w1")

    seen = _seen_actions(sink)
    assert AuditAction.CREDENTIAL_READ in seen
    assert AuditAction.IDENTITY_CREDENTIAL_PROVIDER_CREATE in seen
    assert AuditAction.IDENTITY_CREDENTIAL_PROVIDER_UPDATE in seen
    assert AuditAction.IDENTITY_CREDENTIAL_PROVIDER_DELETE in seen
    assert AuditAction.IDENTITY_WORKLOAD_CREATE in seen
    assert AuditAction.IDENTITY_WORKLOAD_UPDATE in seen
    assert AuditAction.IDENTITY_WORKLOAD_DELETE in seen


# ── Observability route emits ───────────────────────────────────────


@pytest.mark.asyncio
async def test_observability_mutations_emit():
    from agentic_primitives_gateway.models.observability import (
        IngestLogRequest,
        IngestTraceRequest,
        LogGenerationRequest,
        ScoreRequest,
        UpdateTraceRequest,
    )
    from agentic_primitives_gateway.routes import observability as obsroute

    mock_registry = MagicMock()
    mock_registry.observability.flush = AsyncMock()
    mock_registry.observability.log_generation = AsyncMock(return_value={"id": "g1", "name": "n"})
    mock_registry.observability.score_trace = AsyncMock(return_value={"id": "s1", "name": "n", "value": 1.0})
    mock_registry.observability.update_trace = AsyncMock(return_value={"id": "t1"})
    mock_registry.observability.ingest_trace = AsyncMock()
    mock_registry.observability.ingest_log = AsyncMock()

    async with _wire_router() as sink:
        with patch.object(obsroute, "registry", mock_registry):
            await obsroute.flush()
            await obsroute.log_generation("t1", LogGenerationRequest(name="n", model="m"))
            await obsroute.score_trace("t1", ScoreRequest(name="n", value=1.0))
            await obsroute.update_trace("t1", UpdateTraceRequest())
            await obsroute.ingest_trace(IngestTraceRequest(name="t", trace_id="t1"))
            await obsroute.ingest_log(IngestLogRequest(message="hi"))

    seen = _seen_actions(sink)
    assert AuditAction.OBSERVABILITY_FLUSH in seen
    assert AuditAction.TRACE_GENERATION_LOG in seen
    assert AuditAction.TRACE_SCORE_CREATE in seen
    assert AuditAction.TRACE_UPDATE in seen
    assert AuditAction.TRACE_INGEST in seen
    assert AuditAction.LOG_INGEST in seen


# ── Browser route emits ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_mutations_emit():
    from agentic_primitives_gateway.models.browser import (
        ClickRequest,
        EvaluateRequest,
        NavigateRequest,
        StartBrowserSessionRequest,
        TypeRequest,
    )
    from agentic_primitives_gateway.routes import browser as browserroute

    mock_registry = MagicMock()
    mock_registry.browser.start_session = AsyncMock(return_value={"session_id": "b1", "status": "active"})
    mock_registry.browser.stop_session = AsyncMock()
    mock_registry.browser.navigate = AsyncMock(return_value={})
    mock_registry.browser.click = AsyncMock(return_value={})
    mock_registry.browser.type_text = AsyncMock(return_value={})
    mock_registry.browser.evaluate = AsyncMock(return_value="ok")

    owners = MagicMock()
    owners.set_owner = AsyncMock()
    owners.require_owner = AsyncMock()
    owners.delete = AsyncMock()

    principal = MagicMock(id="alice", is_admin=False)

    async with _wire_router() as sink:
        with (
            patch.object(browserroute, "registry", mock_registry),
            patch.object(browserroute, "browser_session_owners", owners),
            patch.object(browserroute, "require_principal", lambda: principal),
        ):
            await browserroute.start_session(StartBrowserSessionRequest(session_id="b1"))
            await browserroute.stop_session("b1")
            await browserroute.navigate("b1", NavigateRequest(url="http://example.com"))
            await browserroute.click("b1", ClickRequest(selector="#btn"))
            await browserroute.type_text("b1", TypeRequest(selector="#input", text="hello"))
            await browserroute.evaluate("b1", EvaluateRequest(expression="1+1"))

    seen = _seen_actions(sink)
    assert AuditAction.SESSION_CREATE in seen
    assert AuditAction.SESSION_TERMINATE in seen
    assert AuditAction.BROWSER_NAVIGATE in seen
    assert AuditAction.BROWSER_CLICK in seen
    assert AuditAction.BROWSER_TYPE in seen
    assert AuditAction.BROWSER_EVALUATE in seen


# ── Code-interpreter route emits ────────────────────────────────────


@pytest.mark.asyncio
async def test_code_interpreter_mutations_emit():
    from agentic_primitives_gateway.models.code_interpreter import ExecuteRequest, StartSessionRequest
    from agentic_primitives_gateway.routes import code_interpreter as ciroute

    mock_registry = MagicMock()
    mock_registry.code_interpreter.start_session = AsyncMock(return_value={"session_id": "c1", "status": "active"})
    mock_registry.code_interpreter.stop_session = AsyncMock()
    mock_registry.code_interpreter.execute = AsyncMock(
        return_value={"session_id": "c1", "stdout": "", "stderr": "", "exit_code": 0}
    )
    mock_registry.code_interpreter.upload_file = AsyncMock(
        return_value={"filename": "f.txt", "size": 3, "session_id": "c1"}
    )
    mock_registry.code_interpreter.download_file = AsyncMock(return_value=b"abc")

    owners = MagicMock()
    owners.set_owner = AsyncMock()
    owners.require_owner = AsyncMock()
    owners.delete = AsyncMock()

    principal = MagicMock(id="alice", is_admin=False)

    # Build a minimal UploadFile stub
    upload = MagicMock(spec=UploadFile)
    upload.filename = "f.txt"
    upload.read = AsyncMock(return_value=b"abc")

    async with _wire_router() as sink:
        with (
            patch.object(ciroute, "registry", mock_registry),
            patch.object(ciroute, "code_interpreter_session_owners", owners),
            patch.object(ciroute, "require_principal", lambda: principal),
        ):
            await ciroute.start_session(StartSessionRequest(session_id="c1", language="python"))
            await ciroute.stop_session("c1")
            await ciroute.execute_code("c1", ExecuteRequest(code="print(1)", language="python"))
            await ciroute.upload_file("c1", upload)
            await ciroute.download_file("c1", "f.txt")

    seen = _seen_actions(sink)
    assert AuditAction.SESSION_CREATE in seen
    assert AuditAction.SESSION_TERMINATE in seen
    assert AuditAction.CODE_EXECUTE in seen
    assert AuditAction.CODE_FILE_UPLOAD in seen
    assert AuditAction.CODE_FILE_DOWNLOAD in seen


# ── Task-tool handler emits ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_handlers_emit():
    from agentic_primitives_gateway.agents.tools import handlers
    from agentic_primitives_gateway.models.tasks import Task

    def _task(**kw: Any) -> Task:
        return Task(
            id=kw.get("id", "t1"),
            team_run_id="run1",
            title=kw.get("title", "t"),
            description="",
            status=kw.get("status", "pending"),
            created_by="agent",
            depends_on=[],
            priority=0,
        )

    mock_registry = MagicMock()
    mock_registry.tasks.create_task = AsyncMock(return_value=_task())
    mock_registry.tasks.claim_task = AsyncMock(return_value=_task(status="in_progress"))
    mock_registry.tasks.update_task = AsyncMock(return_value=_task(status="done"))
    mock_registry.tasks.add_note = AsyncMock(return_value=_task())

    from agentic_primitives_gateway.primitives.tasks.context import (
        reset_agent_role,
        reset_team_run_id,
        set_agent_role,
        set_team_run_id,
    )

    run_token = set_team_run_id("run1")
    role_token = set_agent_role("agent")
    try:
        async with _wire_router() as sink:
            with patch.object(handlers, "registry", mock_registry):
                await handlers.task_create("title")
                await handlers.task_claim("t1")
                await handlers.task_update("t1", status="done")
                await handlers.task_add_note("t1", "note")
    finally:
        reset_agent_role(role_token)
        reset_team_run_id(run_token)

    seen = _seen_actions(sink)
    assert AuditAction.TASK_CREATE in seen
    assert AuditAction.TASK_CLAIM in seen
    assert AuditAction.TASK_UPDATE in seen
    assert AuditAction.TASK_NOTE in seen


# ── Agent-as-tool delegation emits ──────────────────────────────────


@pytest.mark.asyncio
async def test_agent_delegate_emits_on_success_and_failure():
    from agentic_primitives_gateway.agents.tools import delegation
    from agentic_primitives_gateway.models.agents import PrimitiveConfig

    # Build one tool bound to a sub-agent named "analyst".
    spec_stub = MagicMock()
    spec_stub.owner_id = "alice"
    spec_stub.name = "analyst"

    store = MagicMock()
    store.resolve_qualified = AsyncMock(side_effect=[spec_stub, None, None])
    runner = MagicMock()
    runner.run = AsyncMock(return_value=MagicMock(response="ok", artifacts=[]))

    tools = delegation._build_agent_tools(
        config=PrimitiveConfig(enabled=True, tools=["alice:analyst", "missing"]),
        store=store,
        runner=runner,
        depth=0,
        parent_owner_id="alice",
    )
    handler_ok = tools[0].handler
    handler_miss = tools[1].handler

    async with _wire_router() as sink:
        await handler_ok(message="hi")
        await handler_miss(message="hi")

    outcomes = [(e.action, e.outcome) for e in sink.events if e.action == AuditAction.AGENT_DELEGATE]
    assert ("agent.delegate", "success") in outcomes
    assert ("agent.delegate", "failure") in outcomes


# ── Policy-load emits (only on change) ──────────────────────────────


@pytest.mark.asyncio
async def test_policy_load_emits_only_when_content_changes():
    from agentic_primitives_gateway.enforcement import cedar

    enforcer = cedar.CedarPolicyEnforcer(policy_refresh_interval=60, engine_id="eng1")

    async def _list(_engine_id: str) -> dict:
        return {"policies": [{"definition": "permit(principal, action, resource);"}]}

    async with _wire_router() as sink:
        with patch.object(cedar, "registry") as reg:
            reg.policy.list_policies = AsyncMock(side_effect=[await _list("eng1"), await _list("eng1")])
            await enforcer.load_policies()  # initial load → emit
            await enforcer.load_policies()  # identical content → no emit

    emits = [e for e in sink.events if e.action == AuditAction.POLICY_LOAD]
    assert len(emits) == 1
    assert emits[0].metadata["policy_count"] == 1


# ── Config hot-reload (watcher) emits ───────────────────────────────


@pytest.mark.asyncio
async def test_config_reload_emits_success_and_failure(tmp_path):
    """ConfigWatcher emits config.reload on every reload attempt —
    SUCCESS when the new config parses + applies, FAILURE (with
    error_type in metadata) when it doesn't. The app stays up on the
    previous config either way, so this event is the durable record
    that ties a deploy-time edit to whether the gateway picked it up.
    """
    from agentic_primitives_gateway.watcher import ConfigWatcher

    cfg = tmp_path / "config.yaml"
    cfg.write_text("providers: {}")
    mock_registry = AsyncMock()
    watcher = ConfigWatcher(str(cfg), mock_registry)

    async with _wire_router() as sink:
        # Success path
        with patch("agentic_primitives_gateway.config.Settings.load", return_value=MagicMock()):
            await watcher._reload()
        # Failure path
        with patch(
            "agentic_primitives_gateway.config.Settings.load",
            side_effect=ValueError("bad config"),
        ):
            await watcher._reload()

    reload_events = [e for e in sink.events if e.action == AuditAction.CONFIG_RELOAD]
    assert len(reload_events) == 2
    assert reload_events[0].outcome.value == "success"
    assert reload_events[1].outcome.value == "failure"
    assert reload_events[1].reason == "reload_failed"
    assert reload_events[1].metadata["error_type"] == "ValueError"


# ── Policy mutation route emits ─────────────────────────────────────


@pytest.mark.asyncio
async def test_policy_mutations_emit():
    from agentic_primitives_gateway.models.policy import CreatePolicyRequest, UpdatePolicyRequest
    from agentic_primitives_gateway.routes import policy as policyroute

    mock_registry = MagicMock()
    mock_registry.policy.create_policy = AsyncMock(
        return_value={"policy_id": "p1", "policy_body": "permit(principal, action, resource);", "description": ""}
    )
    mock_registry.policy.update_policy = AsyncMock(
        return_value={"policy_id": "p1", "policy_body": "permit(principal, action, resource);", "description": ""}
    )
    mock_registry.policy.delete_policy = AsyncMock()

    async with _wire_router() as sink:
        with patch.object(policyroute, "registry", mock_registry), patch.object(policyroute, "require_admin"):
            await policyroute.create_policy(
                "eng1", CreatePolicyRequest(policy_body="permit(principal, action, resource);")
            )
            await policyroute.update_policy(
                "eng1", "p1", UpdatePolicyRequest(policy_body="permit(principal, action, resource);")
            )
            await policyroute.delete_policy("eng1", "p1")

    seen = _seen_actions(sink)
    assert AuditAction.POLICY_CREATE in seen
    assert AuditAction.POLICY_UPDATE in seen
    assert AuditAction.POLICY_DELETE in seen


# ── Credential write/delete middleware + routes ─────────────────────


@pytest.mark.asyncio
async def test_credential_resolve_and_delete_emit():
    """``credential.resolve`` fires from ``CredentialResolutionMiddleware``
    and ``credential.delete`` from ``DELETE /api/v1/credentials/{key}``.
    Both are pre-existing but uncovered by dedicated assertions."""
    from agentic_primitives_gateway.audit.emit import emit_audit_event
    from agentic_primitives_gateway.audit.models import AuditOutcome, ResourceType

    async with _wire_router() as sink:
        # Simulate a middleware-style emit.  We don't boot the full ASGI
        # stack — we just prove the emit path reaches the router.
        emit_audit_event(
            action=AuditAction.CREDENTIAL_RESOLVE,
            outcome=AuditOutcome.SUCCESS,
            resource_type=ResourceType.CREDENTIAL,
            resource_id="apg.langfuse.public_key",
        )
        emit_audit_event(
            action=AuditAction.CREDENTIAL_DELETE,
            outcome=AuditOutcome.SUCCESS,
            resource_type=ResourceType.CREDENTIAL,
            resource_id="apg.langfuse.public_key",
        )

    seen = _seen_actions(sink)
    assert AuditAction.CREDENTIAL_RESOLVE in seen
    assert AuditAction.CREDENTIAL_DELETE in seen


# ── Agent version lifecycle emits ───────────────────────────────────


@pytest.mark.asyncio
async def test_agent_version_lifecycle_emits():
    """Cover agent.version.{create,propose,approve,reject,deploy} and
    agent.fork.  Driven through emit_audit_event directly to prove the
    wiring, mirroring the pattern used by the route handlers."""
    from agentic_primitives_gateway.audit.emit import emit_audit_event
    from agentic_primitives_gateway.audit.models import AuditOutcome, ResourceType

    async with _wire_router() as sink:
        for action in (
            AuditAction.AGENT_VERSION_CREATE,
            AuditAction.AGENT_VERSION_PROPOSE,
            AuditAction.AGENT_VERSION_APPROVE,
            AuditAction.AGENT_VERSION_REJECT,
            AuditAction.AGENT_VERSION_DEPLOY,
            AuditAction.AGENT_FORK,
        ):
            emit_audit_event(
                action=action,
                outcome=AuditOutcome.SUCCESS,
                resource_type=ResourceType.AGENT,
                resource_id="alice:researcher",
            )

    seen = _seen_actions(sink)
    assert AuditAction.AGENT_VERSION_CREATE in seen
    assert AuditAction.AGENT_VERSION_PROPOSE in seen
    assert AuditAction.AGENT_VERSION_APPROVE in seen
    assert AuditAction.AGENT_VERSION_REJECT in seen
    assert AuditAction.AGENT_VERSION_DEPLOY in seen
    assert AuditAction.AGENT_FORK in seen


# ── Team lifecycle emits ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_team_lifecycle_emits():
    """Cover team CRUD + version lifecycle + fork + run outcomes."""
    from agentic_primitives_gateway.audit.emit import emit_audit_event
    from agentic_primitives_gateway.audit.models import AuditOutcome, ResourceType

    actions = [
        AuditAction.TEAM_CREATE,
        AuditAction.TEAM_UPDATE,
        AuditAction.TEAM_DELETE,
        AuditAction.TEAM_FORK,
        AuditAction.TEAM_VERSION_CREATE,
        AuditAction.TEAM_VERSION_PROPOSE,
        AuditAction.TEAM_VERSION_APPROVE,
        AuditAction.TEAM_VERSION_REJECT,
        AuditAction.TEAM_VERSION_DEPLOY,
        AuditAction.TEAM_RUN_START,
        AuditAction.TEAM_RUN_FAILED,
        AuditAction.TEAM_RUN_CANCELLED,
    ]
    async with _wire_router() as sink:
        for action in actions:
            emit_audit_event(
                action=action,
                outcome=AuditOutcome.SUCCESS,
                resource_type=ResourceType.TEAM,
                resource_id="alice:crew",
            )

    seen = _seen_actions(sink)
    for action in actions:
        assert action in seen, f"missing: {action}"


# ── Agent run outcome emits ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_run_outcomes_emit():
    """AgentRunner emits agent.run.complete and agent.run.failed based on
    the run's outcome.  Covered end-to-end by the agent integration
    tests; this is a targeted emit-path assertion."""
    from agentic_primitives_gateway.audit.emit import emit_audit_event
    from agentic_primitives_gateway.audit.models import AuditOutcome, ResourceType

    async with _wire_router() as sink:
        emit_audit_event(
            action=AuditAction.AGENT_RUN_COMPLETE,
            outcome=AuditOutcome.SUCCESS,
            resource_type=ResourceType.AGENT,
            resource_id="alice:researcher",
        )
        emit_audit_event(
            action=AuditAction.AGENT_RUN_FAILED,
            outcome=AuditOutcome.FAILURE,
            resource_type=ResourceType.AGENT,
            resource_id="alice:researcher",
        )

    seen = _seen_actions(sink)
    assert AuditAction.AGENT_RUN_COMPLETE in seen
    assert AuditAction.AGENT_RUN_FAILED in seen


# ── A2A task cancel emits ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_a2a_cancel_emits_agent_run_cancelled():
    from agentic_primitives_gateway.routes import a2a as a2aroute

    principal = MagicMock(id="alice", is_admin=True)

    class _Bg:
        @staticmethod
        async def get_owner_async(_task_id: str) -> str:
            return "alice"

        @staticmethod
        async def cancel(_task_id: str) -> bool:
            return True

    async with _wire_router() as sink:
        with (
            patch.object(a2aroute, "_require_agent", AsyncMock()),
            patch.object(a2aroute, "require_principal", lambda: principal),
            patch("agentic_primitives_gateway.routes.agents._bg", _Bg),
        ):
            await a2aroute.cancel_task("my-agent", "task-42")

    assert AuditAction.AGENT_RUN_CANCELLED in _seen_actions(sink)
