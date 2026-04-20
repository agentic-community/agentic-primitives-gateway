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

    # Tool + LLM invocations
    TOOL_CALL = "tool.call"
    LLM_GENERATE = "llm.generate"

    # Generic primitive invocation (emitted by MetricsProxy for every
    # provider method call).  Useful as a universal audit trail across
    # memory / tools / browser / code_interpreter / identity / policy /
    # evaluations / tasks / observability.
    PROVIDER_CALL = "provider.call"

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
