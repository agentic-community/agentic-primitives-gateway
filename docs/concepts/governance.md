# Governance

The gateway emits three kinds of governance signal for every request:

1. **Audit events** — structured records of who did what, when, with what outcome.
2. **Prometheus metrics** — aggregated counters for auth, policy, credentials, runs, tool calls, and LLM tokens.
3. **Structured logs** — JSON-formatted application logs with secret scrubbing and correlation IDs.

Together they cover compliance (immutable audit trail), operational debugging (metrics + logs), and security detection (failed auth, policy denials, access violations) from one coherent source.

## The Audit Subsystem

`audit/` is a pluggable subsystem that sits alongside `auth/`, `enforcement/`, and `credentials/`. Every non-exempt request and every significant internal action produces an [`AuditEvent`](../api/audit.md) that flows through a single `AuditRouter` out to one or more `AuditSink` implementations in parallel.

```
 emit_audit_event(action=..., outcome=..., ...)
         │
         ▼
   ┌─────────────┐
   │ AuditRouter │   one queue + worker per sink
   └──┬──┬──┬──┬─┘
      │  │  │  │
      ▼  ▼  ▼  ▼
   stdout_json  file  redis_stream  observability
```

**Design properties:**

- **Non-blocking emit.** `router.emit()` is a synchronous `put_nowait` per sink. The request path pays microseconds.
- **Per-sink failure isolation.** A slow or broken sink doesn't hold up others or the response.
- **Backpressure.** Queue full drops the event for that sink and increments `gateway_audit_events_dropped_total`. Per-sink timeouts bound each `emit()` call.
- **Graceful shutdown.** The FastAPI lifespan drains every queue and calls `sink.close()` on teardown.
- **Best-effort delivery.** At-most-once semantics. Use the `redis_stream` sink when durability matters.

See [Audit API Reference](../api/audit.md) for the `AuditEvent` schema and the full action taxonomy.

## Signals at a Glance

Every primitive emits structured audit events for its mutations.
Read-only operations are covered by the universal `provider.call` emit
from `MetricsProxy`.  Subsystems (auth, policy, credentials) emit at the
middleware layer.

**Cross-cutting:**

| Layer | Signal | Where it fires |
|---|---|---|
| HTTP | `http.request` | `AuditMiddleware` wraps every non-exempt request |
| Auth | `auth.success` / `auth.failure` / `auth.logout` | `AuthenticationMiddleware` |
| Policy | `policy.allow` / `policy.deny` / `policy.load` | `PolicyEnforcementMiddleware` + `CedarPolicyEnforcer.load_policies()` |
| Credentials | `credential.resolve` / `credential.read` / `credential.write` / `credential.delete` | `CredentialResolutionMiddleware` + `routes/{credentials,identity}.py` |
| Ownership | `resource.access.denied` | `auth/access.py::require_access` / `require_owner_or_admin` |
| Network | `network.access.denied` | gateway egress guards (URL-shaped credential rejection) |
| Primitive (universal) | `provider.call` | `MetricsProxy` — every wrapped primitive method (coroutine + async generator) |

**Per-primitive CRUD + lifecycle:**

| Primitive | Resource type(s) | Actions | Where it fires |
|---|---|---|---|
| Agents | `agent` | `agent.{create,update,delete}`, `agent.run.{start,complete,failed,cancelled}`, `agent.delegate`, `agent.version.{create,propose,approve,reject,deploy}`, `agent.fork` | `routes/agents.py`, `AgentRunner`, `agents/tools/delegation.py` |
| Teams | `team` | `team.{create,update,delete}`, `team.run.{start,complete,failed,cancelled}`, `team.version.{create,propose,approve,reject,deploy}`, `team.fork` | `routes/teams.py`, `TeamRunner` |
| Policy | `policy`, `policy_engine` | `policy.{create,update,delete}`, `policy.load` | `routes/policy.py`, `CedarPolicyEnforcer` |
| Credentials | `credential`, `user` | `credential.{resolve,read,write,delete}` | `credentials/middleware.py`, `routes/credentials.py`, `routes/identity.py` |
| Identity | `identity` | `identity.credential_provider.{create,update,delete}`, `identity.workload.{create,update,delete}` | `routes/identity.py` |
| Memory | `memory` | `memory.resource.{create,delete}`, `memory.strategy.{create,delete}`, `memory.branch.create`, `memory.event.{append,delete}`, `memory.record.{write,delete}` | `routes/memory.py` |
| Tools | `tool` | `tool.{register,delete}`, `tool.server.register`, `tool.call` (per execution) | `routes/tools.py`, `agents/tools/catalog.execute_tool` |
| LLM | `llm` | `llm.generate` (with input/output tokens) | `LLMProvider.__init_subclass__` (automatic on every backend) |
| Evaluations | `evaluator` | `evaluator.{create,update,delete}`, `evaluator.score.{create,delete}`, `evaluator.online_config.{create,delete}` | `routes/evaluations.py` |
| Observability | `trace` | `observability.trace.{ingest,update}`, `observability.trace.generation.log`, `observability.trace.score.create`, `observability.log.ingest`, `observability.flush` | `routes/observability.py` |
| Browser | `session`, `page` | `session.{create,terminate}` (with `metadata.primitive=browser`), `browser.{navigate,click,type,evaluate}` | `routes/browser.py` |
| Code-interpreter | `session`, `code_execution`, `file` | `session.{create,terminate}` (with `metadata.primitive=code_interpreter`), `code_interpreter.execute`, `code_interpreter.file.{upload,download}` | `routes/code_interpreter.py` |
| Tasks | `task` | `task.{create,claim,update,note}` | `agents/tools/handlers.py` (when invoked via agent tool calls) |

## Correlation IDs

Every request carries two identifiers:

- **`request_id`** — unique per HTTP request. Generated if not supplied via `X-Request-Id`. Returned on `x-request-id`.
- **`correlation_id`** — threaded across sub-agent calls, background runs, and checkpoint resumes. Generated from the request ID if not supplied via `X-Correlation-Id`. Returned on `x-correlation-id`.

Both IDs are automatically attached to every audit event, every JSON log line, and response headers — so a single `correlation_id` query ties together the HTTP request, its auth outcome, its policy decision, the agent run it triggered, every sub-agent it delegated to, every tool call, and every LLM request.

## Middleware Order

```
CORS
 → RequestContextMiddleware        (sets request_id + correlation_id)
   → AuditMiddleware               (emits one http.request event per request)
     → AuthenticationMiddleware    (auth.success / auth.failure)
       → CredentialResolutionMiddleware  (credential.resolve)
         → PolicyEnforcementMiddleware   (policy.allow / policy.deny)
           → route handler
```

`AuditMiddleware` wraps the auth + policy chain so the `http.request` event sees the final response status and the authenticated principal — and still runs inside `RequestContextMiddleware` so request/correlation IDs are populated before any emit.

## Structured Logs

Application logs are separate from audit events. Set `logging.format: json` to get one JSON object per log line, enriched with:

- `timestamp`, `level`, `logger`, `module`, `message`
- `request_id`, `correlation_id`, `principal_id`, `principal_type`

`LogSanitizationFilter` is installed by default (`logging.sanitize: true`) and scrubs Bearer tokens, AWS access keys, JWTs, and `apg.*` key=value pairs from rendered messages before the formatter sees them. This protects against accidental secret leakage in exception tracebacks and debug logs.

## Prometheus Metrics

All metrics are exposed at `GET /metrics`. Cardinality is intentionally bounded: `actor_id` and `resource_id` are never labels — only bounded enums (`outcome`, `decision`, `principal_type`, `kind`) and configuration-bounded identifiers (`agent_name`, `team_name`, `model`, `tool_name`).

| Metric | Labels | Source |
|---|---|---|
| `gateway_auth_events_total` | `backend`, `outcome`, `principal_type` | Auth middleware |
| `gateway_policy_decisions_total` | `decision`, `action_category` | Policy middleware |
| `gateway_credential_operations_total` | `op`, `service`, `outcome` | Credential resolver + writer |
| `gateway_agent_runs_total` | `agent_name`, `status` | `AgentRunner` |
| `gateway_team_runs_total` | `team_name`, `status` | `TeamRunner` |
| `gateway_tool_calls_total` | `tool_name`, `status` | `execute_tool` |
| `gateway_llm_requests_total` | `model`, `status` | `LLMProvider` ABC |
| `gateway_llm_tokens_total` | `model`, `kind` (input/output/total) | `LLMProvider` ABC |
| `gateway_access_denials_total` | `resource_type` | `require_access` / `require_owner_or_admin` |
| `gateway_audit_events_total` | `action_category`, `outcome` | `emit_audit_event` |
| `gateway_audit_sink_events_total` | `sink`, `outcome` | Per-sink worker |
| `gateway_audit_sink_queue_depth` | `sink` | Per-sink gauge |
| `gateway_audit_events_dropped_total` | `sink`, `reason` | Router backpressure |

## Wire Everything, Filter At Config

APG's default is **emit-everything** — every mutation, every primitive
call, every session lifecycle event — so the audit stream is the
authoritative record of *what happened*, not just *what somebody
remembered to audit*.  High-volume deployments tame the stream at the
router with `audit.filter`:

- `exclude_actions`: exact action drops (`provider.call`).
- `exclude_action_categories`: category prefix drops (`memory` → every `memory.*`).
- `sample_rates`: per-action keep fraction in `[0.0, 1.0]`.

Filtered events increment
`gateway_audit_events_dropped_total{sink="__router__",reason="filtered"}`
so operators can see how aggressive the filter is.  Keep the
compliance-relevant events unfiltered — auth, policy, ownership,
version/fork, and credential events.  See the [Observability
Guide](../guides/observability.md#taming-audit-volume) for recipes.

## Choosing Sinks

| Sink | Good for | Multi-replica |
|---|---|---|
| `stdout_json` (always-on) | k8s log shipping (Fluent Bit → Loki/Datadog/CloudWatch) | ✓ (each pod writes its own stream) |
| `file` | Single-node dev; sidecar tail to SIEM | One file per pod |
| `redis_stream` | Durable cross-replica audit bus; SIEM consumer via `XREAD` | ✓ |
| `observability` | Route audit into Langfuse / AgentCore trace explorer | ✓ |

See the [Observability Guide](../guides/observability.md) for deployment recipes and the [Compliance Guide](../guides/compliance.md) for SOC2 / GDPR alignment.

## See Also

- [Audit API Reference](../api/audit.md) — `AuditEvent` schema, action taxonomy, redaction rules
- [Observability Guide](../guides/observability.md) — SIEM / Loki / Datadog / CloudWatch integration
- [Compliance Guide](../guides/compliance.md) — retention, PII, right-to-erasure
- [Policy Enforcement](policy.md) — how `policy.allow` / `policy.deny` decisions are made
