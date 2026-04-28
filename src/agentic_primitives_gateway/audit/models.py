"""Audit event schema and taxonomy.

``AuditEvent`` is the wire format for structured governance events.  It is
emitted from middleware, routes, and runners, and written unchanged to
every configured sink.

Schema evolution rules:

* Additive-only within a ``schema_version``.  Consumers must tolerate
  unknown fields.
* Renaming or removing a field bumps ``schema_version`` to the next
  integer string ("1" → "2").  Sinks may translate between versions
  for a deprecation window.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class AuditOutcome(StrEnum):
    """Outcome of the audited action."""

    ALLOW = "allow"
    DENY = "deny"
    SUCCESS = "success"
    FAILURE = "failure"
    ERROR = "error"
    # Provider deliberately does not implement the requested operation.
    # Distinct from ``failure`` so compliance dashboards don't count
    # "backend doesn't support this optional method" as a real failure.
    NOT_IMPLEMENTED = "not_implemented"


class ResourceType(StrEnum):
    """Type of resource the action operated on.

    Kept deliberately small and bounded so sinks and dashboards can use it
    as a label without cardinality concerns.
    """

    AGENT = "agent"
    TEAM = "team"
    POLICY = "policy"
    POLICY_ENGINE = "policy_engine"
    CREDENTIAL = "credential"
    SESSION = "session"
    TOOL = "tool"
    HTTP = "http"
    MEMORY = "memory"
    USER = "user"
    LLM = "llm"
    EVALUATOR = "evaluator"
    IDENTITY = "identity"
    TASK = "task"
    TRACE = "trace"
    CODE_EXECUTION = "code_execution"
    FILE = "file"
    PAGE = "page"
    KNOWLEDGE = "knowledge"
    CONFIG = "config"


# Canonical ``primitive → ResourceType`` label for cross-cutting audit
# emitters (``MetricsProxy``, provider healthchecks) that don't know the
# specific kind of resource a method touches.  Kept deliberately coarse
# (one label per primitive) so ``ResourceType`` stays "small and bounded."
# The precise method name is in ``metadata.method``.
PRIMITIVE_RESOURCE_TYPE: dict[str, ResourceType] = {
    "memory": ResourceType.MEMORY,
    "llm": ResourceType.LLM,
    "policy": ResourceType.POLICY,
    "tools": ResourceType.TOOL,
    "tasks": ResourceType.TASK,
    "observability": ResourceType.TRACE,
    "evaluations": ResourceType.EVALUATOR,
    "identity": ResourceType.IDENTITY,
    "code_interpreter": ResourceType.CODE_EXECUTION,
    "browser": ResourceType.PAGE,
    "knowledge": ResourceType.KNOWLEDGE,
}


class AuditAction:
    """String constants for the audit action taxonomy.

    Stable identifiers used across sinks and dashboards.  Grouped by the
    subsystem emitting the event; the first segment before ``.`` becomes the
    ``action_category`` label in Prometheus metrics so cardinality stays
    bounded.
    """

    # Authentication
    AUTH_SUCCESS = "auth.success"
    AUTH_FAILURE = "auth.failure"
    AUTH_LOGOUT = "auth.logout"

    # Policy enforcement
    POLICY_ALLOW = "policy.allow"
    POLICY_DENY = "policy.deny"
    POLICY_LOAD = "policy.load"
    POLICY_CREATE = "policy.create"
    POLICY_UPDATE = "policy.update"
    POLICY_DELETE = "policy.delete"

    # Credentials
    CREDENTIAL_RESOLVE = "credential.resolve"
    CREDENTIAL_READ = "credential.read"
    CREDENTIAL_WRITE = "credential.write"
    CREDENTIAL_DELETE = "credential.delete"

    # Agents
    AGENT_CREATE = "agent.create"
    AGENT_UPDATE = "agent.update"
    AGENT_DELETE = "agent.delete"
    AGENT_RUN_START = "agent.run.start"
    AGENT_RUN_COMPLETE = "agent.run.complete"
    AGENT_RUN_FAILED = "agent.run.failed"
    AGENT_RUN_CANCELLED = "agent.run.cancelled"
    AGENT_DELEGATE = "agent.delegate"

    # Agent versioning — immutable version lifecycle.  ``resource_id`` for
    # these events is the qualified identity ``"{owner_id}:{name}"`` so
    # dashboards can filter by fork.
    AGENT_VERSION_CREATE = "agent.version.create"
    AGENT_VERSION_PROPOSE = "agent.version.propose"
    AGENT_VERSION_APPROVE = "agent.version.approve"
    AGENT_VERSION_REJECT = "agent.version.reject"
    AGENT_VERSION_DEPLOY = "agent.version.deploy"
    AGENT_FORK = "agent.fork"

    # Teams
    TEAM_CREATE = "team.create"
    TEAM_UPDATE = "team.update"
    TEAM_DELETE = "team.delete"
    TEAM_RUN_START = "team.run.start"
    TEAM_RUN_COMPLETE = "team.run.complete"
    TEAM_RUN_FAILED = "team.run.failed"
    TEAM_RUN_CANCELLED = "team.run.cancelled"

    # Team versioning — see agent versioning notes above.
    TEAM_VERSION_CREATE = "team.version.create"
    TEAM_VERSION_PROPOSE = "team.version.propose"
    TEAM_VERSION_APPROVE = "team.version.approve"
    TEAM_VERSION_REJECT = "team.version.reject"
    TEAM_VERSION_DEPLOY = "team.version.deploy"
    TEAM_FORK = "team.fork"

    # Knowledge primitive — bulk-indexed RAG / graph retrieval corpus.
    # Retrieve/query enrich via the KnowledgeProvider __init_subclass__
    # wrapper (chunk count + top-1 score land in metadata); ingest/delete
    # additionally emit via ``audit_mutation`` at the route layer.
    KNOWLEDGE_INGEST = "knowledge.ingest"
    KNOWLEDGE_DELETE = "knowledge.delete"
    KNOWLEDGE_RETRIEVE = "knowledge.retrieve"
    KNOWLEDGE_QUERY = "knowledge.query"

    # Memory primitive — resource CRUD, strategies, branches, event/record writes
    MEMORY_RESOURCE_CREATE = "memory.resource.create"
    MEMORY_RESOURCE_DELETE = "memory.resource.delete"
    MEMORY_STRATEGY_CREATE = "memory.strategy.create"
    MEMORY_STRATEGY_DELETE = "memory.strategy.delete"
    MEMORY_BRANCH_CREATE = "memory.branch.create"
    MEMORY_EVENT_APPEND = "memory.event.append"
    MEMORY_EVENT_DELETE = "memory.event.delete"
    MEMORY_RECORD_WRITE = "memory.record.write"
    MEMORY_RECORD_DELETE = "memory.record.delete"

    # Tools primitive — registry CRUD (single tools + MCP servers).  Per-call
    # execution is covered by ``TOOL_CALL``.
    TOOL_REGISTER = "tool.register"
    TOOL_DELETE = "tool.delete"
    TOOL_SERVER_REGISTER = "tool.server.register"

    # Evaluations primitive — evaluator CRUD + score writes + online config CRUD
    EVALUATOR_CREATE = "evaluator.create"
    EVALUATOR_UPDATE = "evaluator.update"
    EVALUATOR_DELETE = "evaluator.delete"
    SCORE_CREATE = "evaluator.score.create"
    SCORE_DELETE = "evaluator.score.delete"
    ONLINE_CONFIG_CREATE = "evaluator.online_config.create"
    ONLINE_CONFIG_DELETE = "evaluator.online_config.delete"

    # Browser primitive — page interactions within a session (sessions
    # themselves are covered by ``session.create``/``session.terminate``).
    # These are the mutating actions; read-only ones (screenshot,
    # get_page_content, live_view) stay on ``provider.call``.
    BROWSER_NAVIGATE = "browser.navigate"
    BROWSER_CLICK = "browser.click"
    BROWSER_TYPE = "browser.type"
    BROWSER_EVALUATE = "browser.evaluate"

    # Code-interpreter primitive — code execution + file I/O.  Remote
    # code execution is always audit-worthy.
    CODE_EXECUTE = "code_interpreter.execute"
    CODE_FILE_UPLOAD = "code_interpreter.file.upload"
    CODE_FILE_DOWNLOAD = "code_interpreter.file.download"

    # Tasks primitive — team-run task board mutations.  List/get are
    # covered by ``provider.call``.
    TASK_CREATE = "task.create"
    TASK_CLAIM = "task.claim"
    TASK_UPDATE = "task.update"
    TASK_NOTE = "task.note"

    # Observability primitive — trace/score/generation writes (log ingestion
    # and session reads are covered by ``provider.call``).
    TRACE_INGEST = "observability.trace.ingest"
    TRACE_UPDATE = "observability.trace.update"
    TRACE_GENERATION_LOG = "observability.trace.generation.log"
    TRACE_SCORE_CREATE = "observability.trace.score.create"
    LOG_INGEST = "observability.log.ingest"
    OBSERVABILITY_FLUSH = "observability.flush"

    # Identity primitive — credential provider + workload identity CRUD.
    # Token/api-key issuance is covered by ``CREDENTIAL_READ``.
    IDENTITY_CREDENTIAL_PROVIDER_CREATE = "identity.credential_provider.create"
    IDENTITY_CREDENTIAL_PROVIDER_UPDATE = "identity.credential_provider.update"
    IDENTITY_CREDENTIAL_PROVIDER_DELETE = "identity.credential_provider.delete"
    IDENTITY_WORKLOAD_CREATE = "identity.workload.create"
    IDENTITY_WORKLOAD_UPDATE = "identity.workload.update"
    IDENTITY_WORKLOAD_DELETE = "identity.workload.delete"

    # Tool + LLM invocations
    TOOL_CALL = "tool.call"
    LLM_GENERATE = "llm.generate"

    # Generic primitive invocation (emitted by MetricsProxy for every
    # provider method call).  Useful as a universal audit trail across
    # memory / tools / browser / code_interpreter / identity / policy /
    # evaluations / tasks / observability.
    PROVIDER_CALL = "provider.call"

    # Provider healthcheck outcome — emitted by the /readyz and
    # /api/v1/providers/status routes for every (primitive, provider) pair.
    # Distinct from ``provider.call`` so dashboards can filter connection
    # failures (bad credentials, unreachable backend, dim mismatch, etc.)
    # without wading through every primitive RPC.  ``metadata`` carries
    # ``primitive``, ``provider``, ``status`` (ok|reachable|down|timeout),
    # and ``error_type`` + truncated ``error_message`` on failure.
    PROVIDER_HEALTHCHECK = "provider.healthcheck"

    # Gateway config hot-reload outcome — emitted by the ConfigWatcher each
    # time the watched YAML's mtime/inode changes and ``registry.reload()``
    # runs. SUCCESS carries ``config_path`` + ``duration_ms`` in metadata;
    # FAILURE uses ``reason="reload_failed"`` and ``error_type``. The app
    # continues serving on the previous config on failure — this event is
    # the only durable record that a deploy-time config edit didn't take.
    CONFIG_RELOAD = "config.reload"

    # Resource access control
    RESOURCE_ACCESS_DENIED = "resource.access.denied"

    # Network-layer access control — egress blocks applied by gateway
    # guards (today: the X-Cred-* URL-shaped key rejection).  Distinct
    # from ``resource.access.denied`` (ownership/share) so compliance
    # dashboards can alert on attempted SSRF specifically.
    NETWORK_ACCESS_DENIED = "network.access.denied"

    # Sessions
    SESSION_CREATE = "session.create"
    SESSION_TERMINATE = "session.terminate"

    # HTTP (emitted by AuditMiddleware for every non-exempt request)
    HTTP_REQUEST = "http.request"


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class AuditEvent(BaseModel):
    """A single structured governance event.

    Frozen Pydantic model — instances are value objects shipped from emit
    sites to sinks.  Call sites should not need to populate every field;
    the :func:`emit_audit_event` helper fills in timestamp, request/correlation
    IDs, and actor info from contextvars automatically.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"] = "1"
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    timestamp: datetime = Field(default_factory=_utcnow)

    action: str
    outcome: AuditOutcome

    actor_id: str | None = None
    actor_type: str | None = None
    actor_groups: list[str] = Field(default_factory=list)

    resource_type: ResourceType | None = None
    resource_id: str | None = None

    request_id: str | None = None
    correlation_id: str | None = None

    source_ip: str | None = None
    user_agent: str | None = None

    http_method: str | None = None
    http_path: str | None = None
    http_status: int | None = None
    duration_ms: float | None = None

    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
