"""Prometheus metrics for the Agentic Primitives Gateway.

All metrics include ``primitive`` and ``provider`` labels so operators can
filter and aggregate by either dimension in a multi-provider setup.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from prometheus_client import Counter, Gauge, Histogram

logger = logging.getLogger(__name__)

# ── Counters ────────────────────────────────────────────────────────

REQUEST_COUNT = Counter(
    "agentic_primitives_gateway_requests_total",
    "Total requests handled by provider methods",
    ["primitive", "provider", "method", "status"],
)

ERROR_COUNT = Counter(
    "agentic_primitives_gateway_provider_errors_total",
    "Total errors raised by provider methods",
    ["primitive", "provider", "method", "error_type"],
)

# ── Histograms ──────────────────────────────────────────────────────

REQUEST_DURATION = Histogram(
    "agentic_primitives_gateway_request_duration_seconds",
    "Latency of provider method calls in seconds",
    ["primitive", "provider", "method"],
)

# ── Gauges ──────────────────────────────────────────────────────────

ACTIVE_SESSIONS = Gauge(
    "agentic_primitives_gateway_active_sessions",
    "Number of active sessions (browser / code_interpreter)",
    ["primitive", "provider"],
)

PROVIDER_HEALTH = Gauge(
    "agentic_primitives_gateway_provider_health",
    "Provider health status (1=healthy, 0=unhealthy)",
    ["primitive", "provider"],
)

# ── Governance: audit fan-out ───────────────────────────────────────

AUDIT_EVENTS = Counter(
    "gateway_audit_events_total",
    "Audit events emitted (counted at the emit site, before fan-out).",
    ["action_category", "outcome"],
)

AUDIT_SINK_EVENTS = Counter(
    "gateway_audit_sink_events_total",
    "Per-sink delivery attempts.",
    ["sink", "outcome"],  # outcome: success|timeout|error
)

AUDIT_SINK_QUEUE_DEPTH = Gauge(
    "gateway_audit_sink_queue_depth",
    "Current queue depth for each audit sink.",
    ["sink"],
)

AUDIT_EVENTS_DROPPED = Counter(
    "gateway_audit_events_dropped_total",
    "Audit events dropped before reaching a sink.",
    ["sink", "reason"],  # reason: queue_full|serialize_error
)

# ── Governance: business-level signals ─────────────────────────────
#
# Label cardinality is bounded by configuration or taxonomy — never
# by principal or resource identifiers.  If a label's cardinality is
# bounded by something dynamic (e.g. agent_name), call it out here so
# future contributors don't add per-user labels without thinking.

AUTH_EVENTS = Counter(
    "gateway_auth_events_total",
    "Authentication outcomes.",
    ["backend", "outcome", "principal_type"],  # principal_type: user|service|anonymous
)

POLICY_DECISIONS = Counter(
    "gateway_policy_decisions_total",
    "Policy enforcement outcomes (first segment of the Cedar action is the category).",
    ["decision", "action_category"],  # decision: allow|deny
)

CREDENTIAL_OPS = Counter(
    "gateway_credential_operations_total",
    "Credential resolver/writer operations.",
    ["op", "service", "outcome"],  # op: resolve|read|write|delete
)

AGENT_RUNS = Counter(
    "gateway_agent_runs_total",
    "Agent run lifecycle outcomes (agent_name bounded by configured specs).",
    ["agent_name", "status"],  # status: start|complete|failed|cancelled
)

TEAM_RUNS = Counter(
    "gateway_team_runs_total",
    "Team run lifecycle outcomes (team_name bounded by configured specs).",
    ["team_name", "status"],
)

ACCESS_DENIALS = Counter(
    "gateway_access_denials_total",
    "Resource-level access check denials.",
    ["resource_type"],
)

# tool_name is bounded by the static ``_TOOL_CATALOG`` plus dynamic
# ``call_<agent>`` names for agent-as-tool delegation — still bounded by
# the set of configured agents.
TOOL_CALLS = Counter(
    "gateway_tool_calls_total",
    "Agent tool invocations.",
    ["tool_name", "status"],  # status: success|failure
)

# model is bounded by configured LLM backends; kind: input|output|total.
LLM_TOKENS = Counter(
    "gateway_llm_tokens_total",
    "LLM token usage reported by the provider (skipped when usage is not returned).",
    ["model", "kind"],
)

LLM_REQUESTS = Counter(
    "gateway_llm_requests_total",
    "LLM provider invocations.",
    ["model", "status"],  # status: success|failure
)

# ── Knowledge primitive ──────────────────────────────────────────────
#
# Labels are deliberately bounded:
#   - ``provider`` is bounded by configured knowledge backends.
#   - ``store_type`` is one of ``vector|graph|hybrid|native`` (the last
#     is used by managed backends like AgentCore KB that don't surface
#     a store distinction).  Bounded by taxonomy, not config.

KNOWLEDGE_CHUNKS_RETRIEVED = Counter(
    "gateway_knowledge_chunks_retrieved_total",
    "Chunks returned from knowledge retrieve() calls.",
    ["provider", "store_type"],
)

KNOWLEDGE_RETRIEVAL_SCORE = Histogram(
    "gateway_knowledge_retrieval_score",
    "Top-1 relevance score per knowledge retrieve() call.",
    ["provider", "store_type"],
    buckets=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
)

KNOWLEDGE_DOCUMENTS_INGESTED = Counter(
    "gateway_knowledge_documents_ingested_total",
    "Documents accepted by knowledge ingest() calls.",
    ["provider", "store_type"],
)

KNOWLEDGE_DOCUMENTS_DELETED = Counter(
    "gateway_knowledge_documents_deleted_total",
    "Documents removed by knowledge delete() calls.",
    ["provider", "store_type"],
)

KNOWLEDGE_QUERY_TOKENS = Counter(
    "gateway_knowledge_query_tokens_total",
    "Tokens consumed by knowledge query() synthesis (only populated by backends that route synthesis through the LLM primitive or surface token usage).",
    ["provider", "kind"],  # kind: prompt|completion
)

# ── Versioning / fork / approval ─────────────────────────────────────
#
# Labels are deliberately bounded:
#   - ``ns_kind`` reduces unbounded owner IDs to ``system`` vs ``user``
#     so dashboards stay readable even with thousands of users.
#   - ``auto_deployed`` is a boolean so we can split initial-create +
#     PUT-update traffic from admin-gated-deploy traffic without
#     exploding the cardinality by owner.

AGENT_VERSIONS_CREATED = Counter(
    "gateway_agent_versions_created_total",
    "New agent-version records persisted.",
    ["ns_kind", "auto_deployed"],
)

TEAM_VERSIONS_CREATED = Counter(
    "gateway_team_versions_created_total",
    "New team-version records persisted.",
    ["ns_kind", "auto_deployed"],
)

AGENT_FORKS = Counter(
    "gateway_agent_forks_total",
    "Agent fork operations.",
    ["source_ns_kind"],  # which namespace the source lived in (system|user)
)

TEAM_FORKS = Counter(
    "gateway_team_forks_total",
    "Team fork operations.",
    ["source_ns_kind"],
)

AGENT_VERSION_APPROVALS = Counter(
    "gateway_agent_version_approvals_total",
    "Admin decisions on agent-version proposals.",
    ["outcome"],  # approved|rejected|deployed
)

TEAM_VERSION_APPROVALS = Counter(
    "gateway_team_version_approvals_total",
    "Admin decisions on team-version proposals.",
    ["outcome"],
)

# Methods that represent session lifecycle transitions.
_SESSION_START_METHODS = frozenset({"start_session"})
_SESSION_STOP_METHODS = frozenset({"stop_session"})


class MetricsProxy:
    """Transparent proxy that records Prometheus metrics + audit events.

    Wraps a provider instance so that all public async methods are
    automatically instrumented with request counts, error counts, latency
    histograms, and (for session-bearing primitives) active-session gauges.

    The proxy also emits a ``provider.call`` audit event per method call
    so every primitive invocation lands in the audit stream with a
    uniform shape (primitive, provider, method, duration, outcome).
    Primitive-specific audit enrichment (LLM token counts, for example)
    layers on top inside the primitive's ABC.

    Handles both plain coroutines (``async def``) and async generators
    (``async def`` + ``yield``).  The generator wrapper times the entire
    generator lifetime, not just its creation, so streaming LLM calls
    get accurate duration and get audited on close.
    """

    def __init__(self, provider: Any, primitive: str, provider_name: str) -> None:
        self._provider = provider
        self._primitive = primitive
        self._provider_name = provider_name

    # Forward attribute access (properties, sync helpers, etc.) to the real
    # provider so that ``isinstance`` checks done *after* wrapping still work
    # for duck-typed access patterns.
    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._provider, name)
        if name.startswith("_") or not callable(attr):
            return attr
        if inspect.isasyncgenfunction(attr):
            return self._wrap_async_gen(name, attr)
        if asyncio.iscoroutinefunction(attr):
            return self._wrap_async(name, attr)
        return attr

    # Allow ``isinstance(proxy, SomeProviderBase)`` to succeed.
    def __isinstance_check__(self, cls: type) -> bool:  # pragma: no cover
        return isinstance(self._provider, cls)

    # ── Shared recording helpers ────────────────────────────────────

    def _record_success(self, method_name: str, duration_ms: float) -> None:
        primitive = self._primitive
        provider_name = self._provider_name
        REQUEST_COUNT.labels(
            primitive=primitive,
            provider=provider_name,
            method=method_name,
            status="success",
        ).inc()

        # Track active sessions for browser / code_interpreter.
        if method_name in _SESSION_START_METHODS:
            ACTIVE_SESSIONS.labels(primitive=primitive, provider=provider_name).inc()
        elif method_name in _SESSION_STOP_METHODS:
            ACTIVE_SESSIONS.labels(primitive=primitive, provider=provider_name).dec()

        _emit_provider_call_event(
            primitive=primitive,
            provider_name=provider_name,
            method_name=method_name,
            outcome="success",
            duration_ms=duration_ms,
        )

    def _record_error(self, method_name: str, duration_ms: float, exc: BaseException) -> None:
        primitive = self._primitive
        provider_name = self._provider_name

        # ``NotImplementedError`` is how primitives declare "this optional
        # operation isn't supported by my backend" (e.g. mem0 doesn't
        # implement ``get_last_turns``).  Callers catch it and degrade
        # gracefully; the route layer translates it to 501.  Auditing it
        # as a generic ``failure`` would pollute compliance dashboards
        # with non-incidents — emit ``not_implemented`` instead and skip
        # the Prometheus error counter.
        if isinstance(exc, NotImplementedError):
            REQUEST_COUNT.labels(
                primitive=primitive,
                provider=provider_name,
                method=method_name,
                status="not_implemented",
            ).inc()
            _emit_provider_call_event(
                primitive=primitive,
                provider_name=provider_name,
                method_name=method_name,
                outcome="not_implemented",
                duration_ms=duration_ms,
            )
            return

        REQUEST_COUNT.labels(
            primitive=primitive,
            provider=provider_name,
            method=method_name,
            status="error",
        ).inc()
        ERROR_COUNT.labels(
            primitive=primitive,
            provider=provider_name,
            method=method_name,
            error_type=type(exc).__name__,
        ).inc()
        _emit_provider_call_event(
            primitive=primitive,
            provider_name=provider_name,
            method_name=method_name,
            outcome="failure",
            duration_ms=duration_ms,
            error_type=type(exc).__name__,
        )

    # ── Wrappers ────────────────────────────────────────────────────

    def _wrap_async(self, method_name: str, func: Any) -> Any:
        primitive = self._primitive
        provider_name = self._provider_name

        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            with REQUEST_DURATION.labels(
                primitive=primitive,
                provider=provider_name,
                method=method_name,
            ).time():
                try:
                    result = await func(*args, **kwargs)
                except Exception as exc:
                    self._record_error(method_name, (time.perf_counter() - start) * 1000.0, exc)
                    raise
                self._record_success(method_name, (time.perf_counter() - start) * 1000.0)
                return result

        return wrapper

    def _wrap_async_gen(self, method_name: str, func: Any) -> Any:
        primitive = self._primitive
        provider_name = self._provider_name
        record_success = self._record_success
        record_error = self._record_error

        async def wrapper(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
            start = time.perf_counter()
            timer = REQUEST_DURATION.labels(
                primitive=primitive,
                provider=provider_name,
                method=method_name,
            ).time()
            timer.__enter__()
            gen = func(*args, **kwargs)
            try:
                async for item in gen:
                    yield item
            except Exception as exc:
                timer.__exit__(type(exc), exc, None)
                record_error(method_name, (time.perf_counter() - start) * 1000.0, exc)
                raise
            else:
                timer.__exit__(None, None, None)
                record_success(method_name, (time.perf_counter() - start) * 1000.0)

        return wrapper


def _emit_provider_call_event(
    *,
    primitive: str,
    provider_name: str,
    method_name: str,
    outcome: str,
    duration_ms: float,
    error_type: str | None = None,
) -> None:
    """Emit a generic ``provider.call`` audit event.

    Deferred import: ``metrics`` is imported extremely early in the
    process lifecycle (before ``audit.emit``), so the top-level
    ``metrics.py`` module must not itself import the audit subsystem.
    Instead, we resolve the helper lazily — which is also a no-op when
    the audit router isn't installed (tests, library use).
    """
    try:
        from agentic_primitives_gateway.audit.emit import emit_audit_event
        from agentic_primitives_gateway.audit.models import PRIMITIVE_RESOURCE_TYPE, AuditAction, AuditOutcome
    except ImportError:  # pragma: no cover
        return

    audit_outcome = {
        "success": AuditOutcome.SUCCESS,
        "failure": AuditOutcome.FAILURE,
        "not_implemented": AuditOutcome.NOT_IMPLEMENTED,
    }.get(outcome, AuditOutcome.FAILURE)
    metadata: dict[str, Any] = {
        "primitive": primitive,
        "provider": provider_name,
        "method": method_name,
        "duration_ms": round(duration_ms, 3),
    }
    if error_type:
        metadata["error_type"] = error_type
    emit_audit_event(
        action=AuditAction.PROVIDER_CALL,
        outcome=audit_outcome,
        resource_type=PRIMITIVE_RESOURCE_TYPE.get(primitive),
        resource_id=f"{primitive}/{provider_name}",
        duration_ms=round(duration_ms, 3),
        metadata=metadata,
    )
