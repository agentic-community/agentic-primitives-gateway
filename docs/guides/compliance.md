# Compliance Guide

Practical guidance for aligning the gateway's audit trail with common compliance regimes. None of this is legal advice; it's a map from built-in signals to the control families auditors typically ask about.

See [Governance](../concepts/governance.md) for the conceptual model and the [Audit API Reference](../api/audit.md) for the event schema.

## What the Gateway Records by Default

With `audit.enabled: true` (the default) and `audit.stdout_json: true` (the default), every request produces a `http.request` event plus the specific actions it triggered:

- **Authentication outcome** (`auth.success` / `auth.failure`) with the backend and reason.
- **Authorization decision** (`policy.allow` / `policy.deny`) with the Cedar principal, action, and resource.
- **Resource access** (`resource.access.denied`) when ownership/sharing checks fail.
- **CRUD mutations** on agents, teams, policies, and credentials.
- **Run lifecycle** (`agent.run.*`, `team.run.*`) with session IDs and depth.
- **LLM invocations** (`llm.generate`) with model and token counts for cost accounting.
- **Tool calls** (`tool.call`) with name and outcome (never input).

Every event carries `actor_id`, `actor_type`, `actor_groups`, `request_id`, `correlation_id`, `source_ip`, `user_agent`, and a millisecond-precision `timestamp` in UTC.

## SOC 2 Mapping

### CC6 — Logical and Physical Access

| Control concept | Built-in coverage |
|---|---|
| User authentication | `auth.success` / `auth.failure` with backend + reason |
| Authorization checks | `policy.allow` / `policy.deny` with Cedar principal + action |
| Privileged access | Admin-only routes (policy CRUD) log `policy.create/update/delete`; principal must have `admin` scope |
| Access reviews | Ownership in `AgentSpec.owner_id`/`shared_with`; `resource.access.denied` events |
| Secrets management | `credential.write` / `credential.delete` log attribute names, never values |

### CC7 — System Operations

| Control concept | Built-in coverage |
|---|---|
| Change logging | `agent.create/update/delete`, `team.*`, `policy.*` |
| Anomaly detection | `gateway_auth_events_total{outcome="failure"}`, `gateway_policy_decisions_total{decision="deny"}` — alert on rate spikes |
| Data integrity | Audit events are append-only by emission; durability via `redis_stream` sink |
| Log protection | `LogSanitizationFilter` scrubs tokens and keys from app logs; audit events never contain secrets by construction |

### CC8 — Change Management

Every mutation emits an event. Pair with git-log for config-as-code changes and you have a continuous record of who changed what.

## GDPR Alignment

### Article 30 — Records of Processing

Audit events contain: the actor (`actor_id`, `actor_type`), the action (`action`), the resource (`resource_type`, `resource_id`), the time (`timestamp`), and the IP (`source_ip`). Shipping these to a durable sink satisfies the record-keeping requirement for processing activities.

### Article 32 — Security of Processing

- **Pseudonymisation.** Set `audit.redact_principal_id: true` in multi-tenant deployments. `actor_id` becomes a 16-char SHA-256 prefix before emission — the audit record retains correlation value without the raw identifier.
- **Integrity.** Choose a durable sink (`redis_stream`, cloud log service) rather than an ephemeral one.
- **Ability to restore.** Back up the audit sink's store on the same cadence as application data.

### Article 17 — Right to Erasure

The gateway records `actor_id` in every event. To honor an erasure request:

1. **Identify** events by `actor_id` in the sink's store.
2. **Remove or anonymize** per the sink's native mechanism:
   - Redis stream: `XDEL` for exact entries, or set a shorter `maxlen` that ages out old entries.
   - Log aggregator: use the retention/redaction tool for that platform.
   - File sink: rotate and replace with a redacted copy or purge by age.
3. **Prevent recurrence** by enabling `audit.redact_principal_id: true` so future events for the same user arrive pre-hashed.

The in-process redaction layer does not rewrite already-shipped records — that's a property of the sink, not the gateway. Pick sinks whose retention and redaction story matches your legal requirements.

### Article 33 — Breach Notification

`policy.deny`, `auth.failure`, and `resource.access.denied` with elevated rates are leading indicators. Connect Prometheus alerts (see [Observability Guide](observability.md)) to your on-call rotation.

## Retention

The gateway itself does not enforce retention — it delegates to the sink. Recommended retention by sink:

| Sink | Native retention mechanism | Typical horizon |
|---|---|---|
| `stdout_json` → Loki / CloudWatch / Datadog | Platform retention policy | 30-400 days |
| `redis_stream` | `maxlen` bounds stream size (FIFO eviction) | Hours to days; pair with archival consumer |
| `file` | File rotation + archival cron | Application-specific |
| `observability` | Langfuse / AgentCore trace retention | Platform default |

For long-horizon retention (≥1 year for SOC 2, potentially longer for specific regulated industries), pair an in-process sink for real-time consumers with an archival consumer that writes to object storage (S3, GCS, Azure Blob) with a lifecycle policy.

## Secret Protection

Two mechanisms prevent secrets from reaching audit sinks:

1. **Metadata redaction** (`audit/redaction.py`) scrubs values for known-sensitive keys (`authorization`, `cookie`, `password`, `token`, `secret`, `api_key`, `x-aws-secret-access-key`, `x-aws-session-token`, plus any listed in `audit.redact_keys`) before the event is constructed. Called by `emit_audit_event`.
2. **Tool-call input is never logged.** `tool.call` events record name, duration, and error type — not tool arguments. The LLM may pass credentials through tool arguments legitimately (e.g., an API key for a downstream service); logging them would defeat credential resolution.

Log sanitization (`LogSanitizationFilter`) is a separate, complementary layer that scrubs Bearer tokens, AWS access keys, JWTs, and `apg.*` key=value pairs from application log messages. Leave `logging.sanitize: true` enabled.

## Multi-Tenant Hygiene

For shared-cluster deployments:

```yaml
audit:
  redact_principal_id: true   # hash actor_id before emit
  redact_keys:                # extra tenant-specific keys to scrub
    - tenant_secret
    - cross_account_role_arn
```

This preserves the ability to detect anomalies (the same hashed ID still correlates across events) while removing raw per-user identifiers from the audit stream.

## What Auditors Usually Ask For

| Question | Prometheus / Audit query |
|---|---|
| "Show me all failed logins from the last week." | `auth.failure` events in the sink, filter by `timestamp` |
| "Who modified policy X on date Y?" | `policy.update` with `resource_id=X`, filter by `timestamp` |
| "What did user A do yesterday?" | Filter sink by `actor_id=A` and `timestamp` range |
| "Are denials spiking?" | `rate(gateway_policy_decisions_total{decision="deny"}[5m])` |
| "Does the service have an audit trail at all?" | `gateway_audit_sink_events_total` shows delivery counts |

## See Also

- [Governance](../concepts/governance.md) — conceptual overview
- [Observability Guide](observability.md) — SIEM / dashboard setup
- [Audit API Reference](../api/audit.md) — event schema + taxonomy
