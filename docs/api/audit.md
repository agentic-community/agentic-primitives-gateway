# Audit API Reference

Structured governance events emitted by the gateway. See [Governance](../concepts/governance.md) for the conceptual overview.

## AuditEvent Schema

Audit events are frozen [Pydantic v2](https://docs.pydantic.dev/) models serialized to JSON by each sink. The schema is versioned and follows additive-only evolution within a version.

```json
{
  "schema_version": "1",
  "event_id": "bc8b6a823ffb4209bdc12697761ef676",
  "timestamp": "2026-04-17T17:09:23.259153Z",
  "action": "http.request",
  "outcome": "success",
  "actor_id": "alice",
  "actor_type": "user",
  "actor_groups": ["admins"],
  "resource_type": "http",
  "resource_id": "/api/v1/agents",
  "request_id": "6828fe0619354661ae478d994588eb87",
  "correlation_id": "trace-create-1",
  "source_ip": "127.0.0.1",
  "user_agent": "curl/8.7.1",
  "http_method": "POST",
  "http_path": "/api/v1/agents",
  "http_status": 201,
  "duration_ms": 17.242,
  "reason": null,
  "metadata": {}
}
```

### Fields

| Field | Type | Description |
|---|---|---|
| `schema_version` | string | Stable schema version. Breaking changes bump to `"2"`. |
| `event_id` | string | Unique per event (hex UUID). |
| `timestamp` | string (ISO-8601 UTC) | When the event was produced. |
| `action` | string | Stable action identifier from the taxonomy below. |
| `outcome` | enum | `allow`, `deny`, `success`, `failure`, `error`. |
| `actor_id` | string or null | Principal ID (hashed when `redact_principal_id: true`). |
| `actor_type` | string or null | `user`, `service`, `anonymous`. |
| `actor_groups` | list[string] | Principal groups. |
| `resource_type` | enum or null | `agent`, `team`, `policy`, `policy_engine`, `credential`, `session`, `tool`, `http`, `memory`, `user`, `llm`. |
| `resource_id` | string or null | Resource name / path / ID. |
| `request_id` | string or null | Per-HTTP-request identifier (`x-request-id`). |
| `correlation_id` | string or null | Cross-request chain identifier (`x-correlation-id`). |
| `source_ip` | string or null | Client IP (set by `AuditMiddleware`). |
| `user_agent` | string or null | Client UA (set by `AuditMiddleware`). |
| `http_method` | string or null | HTTP method when applicable. |
| `http_path` | string or null | HTTP path when applicable. |
| `http_status` | int or null | Final response status code. |
| `duration_ms` | float or null | Measured duration in milliseconds. |
| `reason` | string or null | Human-readable reason, especially for denials / failures. |
| `metadata` | object | Action-specific fields. Redacted before emit. |

## Action Taxonomy

All action constants live in `agentic_primitives_gateway.audit.models.AuditAction`. The first segment (before `.`) is the **category** and forms the Prometheus label on `gateway_audit_events_total`.

### Authentication

| Action | Outcome | Emitted by | Key metadata |
|---|---|---|---|
| `auth.success` | `success` | `AuthenticationMiddleware` | `backend` |
| `auth.failure` | `failure` | `AuthenticationMiddleware` | `backend`, `reason` |
| `auth.logout` | `success` | Reserved (UI-initiated logout) | |

### Policy Enforcement

| Action | Outcome | Emitted by | Key metadata |
|---|---|---|---|
| `policy.allow` | `allow` | `PolicyEnforcementMiddleware` | `cedar_principal`, `cedar_action` |
| `policy.deny` | `deny` | `PolicyEnforcementMiddleware` | `cedar_principal`, `cedar_action`, `reason` |
| `policy.create/update/delete` | `success` | `routes/policy.py` | `engine_id` |
| `policy.engine.create/delete` | `success` | `routes/policy.py` | `name` |
| `policy.load` | `success` | Reserved (Cedar refresh) | |

### Credentials

| Action | Outcome | Emitted by | Key metadata |
|---|---|---|---|
| `credential.resolve` | `success` | `CredentialResolutionMiddleware` | `services` (names only), `source` |
| `credential.read` | `success` | `routes/credentials.py` | |
| `credential.write` | `success`/`failure`/`error` | `routes/credentials.py` | `keys` (names only), `reason` |
| `credential.delete` | `success`/`failure`/`error` | `routes/credentials.py` | `keys`, `reason` |

Credential events **never** log values — only attribute names.

### Agents

| Action | Outcome | Emitted by | Key metadata |
|---|---|---|---|
| `agent.create` | `success` | `routes/agents.py` | `model` |
| `agent.update` | `success` | `routes/agents.py` | `fields` |
| `agent.delete` | `success` | `routes/agents.py` | |
| `agent.run.start` | `success` | `AgentRunner` | `session_id`, `depth` |
| `agent.run.complete` | `success` | `AgentRunner` | `session_id`, `depth`, `turns_used`, `tools_called` |
| `agent.run.failed` | `error` | `AgentRunner` | `session_id`, `depth` |
| `agent.run.cancelled` | `deny` | `AgentRunner` | `session_id`, `depth` |
| `agent.delegate` | `success` | Reserved (sub-agent call) | |

### Teams

| Action | Outcome | Emitted by | Key metadata |
|---|---|---|---|
| `team.create` | `success` | `routes/teams.py` | `workers`, `planner`, `synthesizer` |
| `team.update` | `success` | `routes/teams.py` | `fields` |
| `team.delete` | `success` | `routes/teams.py` | |
| `team.run.start` | `success` | `TeamRunner` | `team_run_id` |
| `team.run.complete` | `success` | `TeamRunner` | `team_run_id` |
| `team.run.failed` | `error` | `TeamRunner` | `team_run_id` |
| `team.run.cancelled` | `deny` | `TeamRunner` | `team_run_id` |

### Tools and LLM

| Action | Outcome | Emitted by | Key metadata |
|---|---|---|---|
| `tool.call` | `success`/`failure` | `agents/tools/catalog.execute_tool` | `tool_name`, `primitive`, `duration_ms`, `error_type` |
| `tool.register` | `success`/`failure` | `POST /api/v1/tools` | `duration_ms` |
| `tool.delete` | `success`/`failure` | `DELETE /api/v1/tools/{name}` | `duration_ms` |
| `tool.server.register` | `success`/`failure` | `POST /api/v1/tools/servers` | `duration_ms` |
| `llm.generate` | `success`/`error` | `LLMProvider` ABC (automatic) | `model`, `input_tokens`, `output_tokens`, `error_type` |

Tool events **never** log tool input (the LLM may pass credentials through tool arguments).

### Memory

| Action | Outcome | Emitted by | Key metadata |
|---|---|---|---|
| `memory.resource.create` | `success`/`failure` | `POST /api/v1/memory/resources` | `name`, `memory_id` |
| `memory.resource.delete` | `success`/`failure` | `DELETE /api/v1/memory/resources/{memory_id}` | `memory_id` |
| `memory.strategy.create` | `success`/`failure` | `POST /api/v1/memory/resources/{memory_id}/strategies` | `strategy_id` |
| `memory.strategy.delete` | `success`/`failure` | `DELETE /api/v1/memory/resources/{memory_id}/strategies/{strategy_id}` | `strategy_id` |
| `memory.event.append` | `success`/`failure` | `POST /api/v1/memory/sessions/{actor}/{session}/events` | `event_id`, `message_count` |
| `memory.event.delete` | `success`/`failure` | `DELETE /api/v1/memory/sessions/{actor}/{session}/events/{event_id}` | |
| `memory.branch.create` | `success`/`failure` | `POST /api/v1/memory/sessions/{actor}/{session}/branches` | `branch_name`, `root_event_id` |
| `memory.record.write` | `success`/`failure` | `POST /api/v1/memory/{namespace}` | |
| `memory.record.delete` | `success`/`failure` | `DELETE /api/v1/memory/{namespace}/{key}` | |

### Evaluators

| Action | Outcome | Emitted by | Key metadata |
|---|---|---|---|
| `evaluator.create` | `success`/`failure` | `POST /api/v1/evaluations/evaluators` | `name`, `evaluator_type`, `evaluator_id` |
| `evaluator.update` | `success`/`failure` | `PUT /api/v1/evaluations/evaluators/{id}` | |
| `evaluator.delete` | `success`/`failure` | `DELETE /api/v1/evaluations/evaluators/{id}` | |
| `evaluator.score.create` | `success`/`failure` | `POST /api/v1/evaluations/scores` | `name`, `trace_id`, `score_id` |
| `evaluator.score.delete` | `success`/`failure` | `DELETE /api/v1/evaluations/scores/{id}` | |
| `evaluator.online_config.create` | `success`/`failure` | `POST /api/v1/evaluations/online-configs` | `name`, `config_id` |
| `evaluator.online_config.delete` | `success`/`failure` | `DELETE /api/v1/evaluations/online-configs/{id}` | |

### Identity

| Action | Outcome | Emitted by | Key metadata |
|---|---|---|---|
| `credential.read` | `success`/`failure` | `POST /api/v1/identity/{token,api-key}` | `kind`, `credential_provider` |
| `identity.credential_provider.create` | `success`/`failure` | `POST /api/v1/identity/credential-providers` | `provider_type` |
| `identity.credential_provider.update` | `success`/`failure` | `PUT /api/v1/identity/credential-providers/{name}` | |
| `identity.credential_provider.delete` | `success`/`failure` | `DELETE /api/v1/identity/credential-providers/{name}` | |
| `identity.workload.create` | `success`/`failure` | `POST /api/v1/identity/workload-identities` | |
| `identity.workload.update` | `success`/`failure` | `PUT /api/v1/identity/workload-identities/{name}` | |
| `identity.workload.delete` | `success`/`failure` | `DELETE /api/v1/identity/workload-identities/{name}` | |

### Observability

| Action | Outcome | Emitted by | Key metadata |
|---|---|---|---|
| `observability.trace.ingest` | `success`/`failure` | `POST /api/v1/observability/traces` | |
| `observability.trace.update` | `success`/`failure` | `PUT /api/v1/observability/traces/{id}` | |
| `observability.trace.generation.log` | `success`/`failure` | `POST /api/v1/observability/traces/{id}/generations` | `name`, `model` |
| `observability.trace.score.create` | `success`/`failure` | `POST /api/v1/observability/traces/{id}/scores` | `name` |
| `observability.log.ingest` | `success`/`failure` | `POST /api/v1/observability/logs` | |
| `observability.flush` | `success`/`failure` | `POST /api/v1/observability/flush` | |

### Browser

| Action | Outcome | Emitted by | Key metadata |
|---|---|---|---|
| `browser.navigate` | `success`/`failure` | `POST /api/v1/browser/sessions/{id}/navigate` | `url` |
| `browser.click` | `success`/`failure` | `POST /api/v1/browser/sessions/{id}/click` | `selector` |
| `browser.type` | `success`/`failure` | `POST /api/v1/browser/sessions/{id}/type` | `selector`, `text_length` (never `text` itself) |
| `browser.evaluate` | `success`/`failure` | `POST /api/v1/browser/sessions/{id}/evaluate` | `expression_length` (never the expression itself) |

### Code Interpreter

| Action | Outcome | Emitted by | Key metadata |
|---|---|---|---|
| `code_interpreter.execute` | `success`/`failure` | `POST /api/v1/code-interpreter/sessions/{id}/execute` | `language`, `code_length` (never the code itself) |
| `code_interpreter.file.upload` | `success`/`failure` | `POST /api/v1/code-interpreter/sessions/{id}/files` | `size_bytes` |
| `code_interpreter.file.download` | `success`/`failure` | `GET /api/v1/code-interpreter/sessions/{id}/files/{name}` | `size_bytes` |

### Tasks (team-run task board)

Emitted when an agent invokes the task tool inside a team run.  Direct
`registry.tasks.*` calls from `TeamRunner` are covered by
`provider.call`.

| Action | Outcome | Emitted by | Key metadata |
|---|---|---|---|
| `task.create` | `success` | `agents/tools/handlers.py::task_create` | `created_by`, `depends_on`, `priority`, `suggested_worker` |
| `task.claim` | `success`/`failure` | `agents/tools/handlers.py::task_claim` | `claimed_by` |
| `task.update` | `success`/`failure` | `agents/tools/handlers.py::task_update` | `status`, `has_result` |
| `task.note` | `success`/`failure` | `agents/tools/handlers.py::task_add_note` | `author` |

### Resource Access

| Action | Outcome | Emitted by | Key metadata |
|---|---|---|---|
| `resource.access.denied` | `deny` | `auth/access.py` | `resource_owner`, `resource_type_hint`, `reason` |

### HTTP + Provider Call + Sessions

| Action | Outcome | Emitted by | Key metadata |
|---|---|---|---|
| `http.request` | `success`/`failure` | `AuditMiddleware` | method, path, status, duration, source_ip, user_agent |
| `provider.call` | `success`/`failure` | `MetricsProxy` (every wrapped primitive method) | `primitive`, `provider`, `method`, `duration_ms` |
| `session.create` | `success`/`failure` | `POST /api/v1/{browser,code-interpreter}/sessions` | `primitive`, `language` |
| `session.terminate` | `success`/`failure` | `DELETE /api/v1/{browser,code-interpreter}/sessions/{id}` | `primitive` |
| `agent.delegate` | `success`/`failure` | `agents/tools/delegation.py` (agent-as-tool) | `parent_owner_id`, `depth`, `error_type` |
| `policy.load` | `success`/`failure` | `CedarPolicyEnforcer.load_policies()` (only when policy set changes) | `policy_count`, `previous_count` |

## Outcome Values

```python
class AuditOutcome(StrEnum):
    ALLOW           = "allow"
    DENY            = "deny"
    SUCCESS         = "success"
    FAILURE         = "failure"
    ERROR           = "error"
    NOT_IMPLEMENTED = "not_implemented"
```

| Value | Use for |
|---|---|
| `allow` | Policy permit |
| `deny` | Policy deny, access denial, cancelled run |
| `success` | Successful mutation or HTTP 2xx |
| `failure` | Expected failure (auth rejected, 4xx, validation) |
| `error` | Unexpected failure (exception, 5xx) |
| `not_implemented` | Provider deliberately doesn't implement the requested operation — emitted by `MetricsProxy` so compliance dashboards don't count optional-method absence as a real failure |

## Resource Types

```python
class ResourceType(StrEnum):
    AGENT         = "agent"
    TEAM          = "team"
    POLICY        = "policy"
    POLICY_ENGINE = "policy_engine"
    CREDENTIAL    = "credential"
    SESSION       = "session"
    TOOL          = "tool"
    HTTP          = "http"
    MEMORY        = "memory"
    USER          = "user"
    LLM           = "llm"
    EVALUATOR     = "evaluator"
    IDENTITY      = "identity"
    TASK          = "task"
    TRACE         = "trace"
    CODE_EXECUTION = "code_execution"
    FILE          = "file"
    PAGE          = "page"
```

The `user` type is reserved for future emits that target a specific user
record (today, credential operations use `credential` since the operation
is on the credential, not the user).  Every other type is emitted by at
least one action above.

## Redaction

Three layers keep secrets out of audit output:

1. **Per-emit deny-list.** `redact_mapping()` in `audit/redaction.py` walks the `metadata` dict and replaces values for known-sensitive keys (`authorization`, `cookie`, `password`, `token`, `secret`, `api_key`, `x-aws-secret-access-key`, `x-aws-session-token`, any key listed in `audit.redact_keys`) with `"***"`.
2. **Principal ID hashing.** Set `audit.redact_principal_id: true` to replace `actor_id` with a 16-char SHA-256 prefix before emit. Useful for multi-tenant k8s deployments.
3. **Log sanitization.** Separate from audit events, `LogSanitizationFilter` scrubs Bearer tokens, AWS access keys, JWT three-part tokens, and `apg.*` key=value pairs from rendered log messages.

## Schema Evolution

- **Additive changes** within `schema_version: "1"` are allowed: new optional fields, new actions, new metadata keys.
- **Breaking changes** (rename / remove / type change) bump to `schema_version: "2"`.
- Consumers must tolerate unknown fields — don't reject events because of a new key.
- Emitting code is on one version at a time; consumers decide their compatibility window.

## Sink Configuration

Sinks are configured in the `audit:` block of the server YAML. Each entry has a stable `name` (used as the `sink` metric label) and a `backend` (short alias or dotted class path).

```yaml
audit:
  enabled: true
  stdout_json: true           # always-on unless explicitly disabled
  sinks:
    - name: local_file
      backend: file
      config:
        path: /var/log/apg/audit.log
        max_bytes: 10485760
        backup_count: 5
    - name: durable
      backend: redis_stream
      config:
        redis_url: "${REDIS_URL:=redis://localhost:6379/0}"
        stream: "gateway:audit"
        maxlen: 100000
    - name: traces
      backend: observability
  redact_keys: []            # extra keys to scrub in metadata
  redact_principal_id: false # hash actor_id before emit
  queue_size: 2048           # per-sink queue bound
  sink_timeout_seconds: 2.0  # per-emit timeout per sink
  filter:                    # drop noisy events before fan-out
    exclude_actions: []        # exact action drops (e.g. "provider.call")
    exclude_action_categories: []  # prefix drops (e.g. "memory")
    sample_rates: {}           # per-action keep rate in [0.0, 1.0]

logging:
  format: json              # text | json
  sanitize: true            # install LogSanitizationFilter
```

### Filtering Noisy Events

The router drops events that match any `filter` rule before fan-out, so
filtered events never hit a sink queue.  Each rule is independent and
compose logically with **AND**: an event must pass *every* rule to
survive.

- `exclude_actions`: exact action string match (drop every `provider.call`).
- `exclude_action_categories`: first `.`-segment match (drop every `memory.*`).
- `sample_rates`: per-action keep fraction in `[0.0, 1.0]`.  `0.0` drops
  every event, `1.0` keeps every event.  Sampling is independent per-event.

Dropped events increment
`gateway_audit_events_dropped_total{sink="__router__",reason="filtered"}`.

Keep compliance-relevant events unfiltered — auth, policy, resource
access, credential, version, and fork events are emitted at low volume
but carry high audit value.  Filter the high-volume ones:
`provider.call` (one per primitive RPC), `tool.call`, and the
`memory.record.*` family.

### Built-in Sink Aliases

| Alias | Class | Use for |
|---|---|---|
| `noop` | `NoopAuditSink` | Tests; disabled audit |
| `stdout_json` | `StdoutJsonSink` | Default; k8s log shipping |
| `file` | `RotatingFileAuditSink` | Local or sidecar tail |
| `redis_stream` | `RedisStreamAuditSink` | Durable cross-replica bus |
| `observability` | `ObservabilityProviderSink` | Route into Langfuse / AgentCore via `registry.observability.ingest_log` |

### Custom Sinks

Implement `AuditSink` and reference by dotted path:

```python
from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.models import AuditEvent

class MySink(AuditSink):
    def __init__(self, *, name: str = "mine", **config) -> None:
        self.name = name

    async def emit(self, event: AuditEvent) -> None:
        ...

    async def close(self) -> None:
        ...
```

```yaml
audit:
  sinks:
    - name: mine
      backend: myapp.audit.MySink
      config: {...}
```

### Read-back via `AuditReader`

The UI audit viewer (`/ui/audit`) and the `GET /api/v1/audit/{status,events,events/stream}`
endpoints are backend-agnostic — they iterate `router.sinks` and dispatch
against the first sink that implements the `AuditReader` protocol:

```python
from collections.abc import AsyncIterator
from typing import Any

from agentic_primitives_gateway.audit.base import AuditReader, AuditSink
from agentic_primitives_gateway.audit.models import AuditEvent

class MyDurableSink(AuditSink):
    """Write-only.  UI viewer will ignore this sink."""
    async def emit(self, event: AuditEvent) -> None: ...

class MyQueryableSink(AuditSink):
    """Implements AuditReader too — UI viewer can query it."""
    async def emit(self, event: AuditEvent) -> None: ...

    # AuditReader protocol — surfaces in /api/v1/audit/*.
    def describe(self) -> dict[str, Any]:
        return {"backend": "my_queryable", "retention_days": 90}

    async def count(self) -> int | None: ...

    async def list_events(
        self, *, start: str, end: str, count: int,
    ) -> tuple[list[AuditEvent], str | None]: ...

    async def tail(self) -> AsyncIterator[AuditEvent | None]:
        """Yield new events or ``None`` as a keepalive tick."""
        ...
```

Today only `RedisStreamAuditSink` implements `AuditReader`.  A custom
Postgres, SQLite, or S3-index sink can plug in without changing the
route layer or UI.  Write-only sinks (`stdout_json`, `file`,
`observability`, `noop`) stay silent — their audit data is consumed
externally (SIEM, Langfuse trace explorer, etc.).

## See Also

- [Governance](../concepts/governance.md) — conceptual overview
- [Observability Guide](../guides/observability.md) — SIEM / log-shipping recipes
- [Compliance Guide](../guides/compliance.md) — SOC2 / GDPR alignment
