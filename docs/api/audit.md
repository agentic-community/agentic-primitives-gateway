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
| `llm.generate` | `success`/`error` | `LLMProvider` ABC (automatic) | `model`, `input_tokens`, `output_tokens`, `error_type` |

Tool events **never** log tool input (the LLM may pass credentials through tool arguments).

### Resource Access

| Action | Outcome | Emitted by | Key metadata |
|---|---|---|---|
| `resource.access.denied` | `deny` | `auth/access.py` | `resource_owner`, `resource_type_hint`, `reason` |

### HTTP + Provider Call + Sessions

| Action | Outcome | Emitted by | Key metadata |
|---|---|---|---|
| `http.request` | `success`/`failure` | `AuditMiddleware` | method, path, status, duration, source_ip, user_agent |
| `provider.call` | `success`/`failure` | `MetricsProxy` (every wrapped primitive method) | `primitive`, `provider`, `method`, `duration_ms` |
| `session.create/terminate` | `success` | Reserved | |

## Outcome Values

```python
class AuditOutcome(StrEnum):
    ALLOW   = "allow"
    DENY    = "deny"
    SUCCESS = "success"
    FAILURE = "failure"
    ERROR   = "error"
```

| Value | Use for |
|---|---|
| `allow` | Policy permit |
| `deny` | Policy deny, access denial, cancelled run |
| `success` | Successful mutation or HTTP 2xx |
| `failure` | Expected failure (auth rejected, 4xx, validation) |
| `error` | Unexpected failure (exception, 5xx) |

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
```

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

logging:
  format: json              # text | json
  sanitize: true            # install LogSanitizationFilter
```

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

## See Also

- [Governance](../concepts/governance.md) — conceptual overview
- [Observability Guide](../guides/observability.md) — SIEM / log-shipping recipes
- [Compliance Guide](../guides/compliance.md) — SOC2 / GDPR alignment
