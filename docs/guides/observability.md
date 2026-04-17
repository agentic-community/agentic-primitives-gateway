# Observability Guide

Wire the gateway's governance signal into the observability stack you already run. This guide covers three deployment patterns: log-shipping from stdout, a durable audit bus on Redis, and routing audit events into a trace backend.

For the conceptual picture see [Governance](../concepts/governance.md); for the event schema see the [Audit API Reference](../api/audit.md).

## Pattern 1: Stdout JSON → Log Shipper → SIEM

The default setup. `StdoutJsonSink` is always on unless explicitly disabled, so every deployment writes one JSON line per audit event to stdout. Any log collector can tail it.

**Server config:**

```yaml
audit:
  stdout_json: true      # default
logging:
  format: json           # application logs also go to JSON
  sanitize: true         # scrub secrets in log messages
```

**Kubernetes + Fluent Bit → Loki:**

```yaml
# fluent-bit.conf (DaemonSet; reads pod stdout from /var/log/containers)
[INPUT]
    Name              tail
    Path              /var/log/containers/*apg*.log
    Parser            cri
    Tag               kube.apg.*

[FILTER]
    Name              parser
    Match             kube.apg.*
    Key_Name          log
    Parser            json
    Reserve_Data      On

[OUTPUT]
    Name              loki
    Match             kube.apg.*
    host              loki.monitoring
    labels            job=apg, action=$action, outcome=$outcome
    label_keys        $action,$outcome,$actor_type
```

Query audit events in Grafana:

```logql
{job="apg"} | json | action=`policy.deny`
```

**Kubernetes + Fluent Bit → Datadog:**

```ini
[OUTPUT]
    Name        datadog
    Match       kube.apg.*
    Host        http-intake.logs.datadoghq.com
    TLS         on
    apikey      ${DATADOG_API_KEY}
    dd_service  apg
    dd_source   apg-audit
    dd_tags     env:prod
```

**AWS ECS / Fargate → CloudWatch:**

The awslogs driver forwards stdout to CloudWatch automatically. Query with CloudWatch Logs Insights:

```
fields @timestamp, action, outcome, actor_id, correlation_id, http_path
| filter action = "policy.deny"
| sort @timestamp desc
```

## Pattern 2: Durable Cross-Replica Audit Bus on Redis

For multi-replica deployments where you need a single consumable stream across pods, and want at-least-minute-durability without an external logging tier.

**Server config:**

```yaml
audit:
  stdout_json: true      # keep the k8s-native log story
  sinks:
    - name: bus
      backend: redis_stream
      config:
        redis_url: "${REDIS_URL:=redis://localhost:6379/0}"
        stream: "gateway:audit"
        maxlen: 100000   # ring-buffer cap; old entries evicted on write
```

**Consume with redis-cli for ad-hoc inspection:**

```bash
# Latest 20 events
redis-cli XREVRANGE gateway:audit + - COUNT 20

# Stream from newest
redis-cli XREAD COUNT 100 STREAMS gateway:audit $
```

**Dedicated consumer group (Python):**

```python
import redis.asyncio as redis

r = redis.from_url("redis://localhost:6379/0", decode_responses=True)
try:
    await r.xgroup_create("gateway:audit", "siem-shipper", id="0", mkstream=True)
except redis.ResponseError:
    pass  # group exists

while True:
    entries = await r.xreadgroup(
        "siem-shipper", "worker-1",
        streams={"gateway:audit": ">"},
        count=100, block=5000,
    )
    for _stream, events in entries or []:
        for eid, fields in events:
            event = json.loads(fields["event"])
            await ship_to_siem(event)
            await r.xack("gateway:audit", "siem-shipper", eid)
```

Pair this with a stdout sink so operators can tail `kubectl logs` for quick checks while the durable consumer handles compliance delivery.

## Pattern 3: Audit Events into Your Trace Backend

Route audit into the already-configured observability provider (Langfuse or AgentCore) so the audit stream appears in the same trace explorer your team uses for agent runs.

```yaml
audit:
  stdout_json: true
  sinks:
    - name: traces
      backend: observability   # delegates to registry.observability.ingest_log
```

No extra setup beyond configuring the `observability` primitive itself. Audit events arrive as log entries in Langfuse / CloudWatch X-Ray with `request_id` and `correlation_id` already attached.

## Prometheus Metrics

The gateway exposes Prometheus metrics at `GET /metrics` — exempt from auth and policy, so scraping is always allowed. Add it to your `ServiceMonitor` or Prometheus scrape config:

```yaml
scrape_configs:
  - job_name: apg
    metrics_path: /metrics
    static_configs:
      - targets: ['apg.monitoring:8000']
```

### Useful Alerts

```yaml
# Sudden spike in auth failures (potential brute force).
- alert: APGAuthFailureSpike
  expr: |
    sum(rate(gateway_auth_events_total{outcome="failure"}[5m])) > 5
  for: 2m
  annotations:
    summary: "Authentication failures elevated on APG"

# Policy denials (possible misconfig or attack).
- alert: APGPolicyDenySpike
  expr: |
    sum(rate(gateway_policy_decisions_total{decision="deny"}[5m])) > 1
  for: 5m

# Sink is dropping events — audit pipeline broken.
- alert: APGAuditDrops
  expr: |
    rate(gateway_audit_events_dropped_total[5m]) > 0
  for: 2m
  labels:
    severity: warning

# Sink queue filling — backpressure building.
- alert: APGAuditQueueDepth
  expr: gateway_audit_sink_queue_depth > 1500
  for: 10m
```

### Useful Dashboard Queries

```promql
# LLM token usage by model (input + output per second)
sum by (model, kind) (rate(gateway_llm_tokens_total[5m]))

# Agent run error rate
sum by (agent_name) (rate(gateway_agent_runs_total{status="failed"}[5m]))
  /
sum by (agent_name) (rate(gateway_agent_runs_total{status="start"}[5m]))

# Top tools by latency (approximate — use provider duration metrics for precise)
topk(10, sum by (tool_name) (rate(gateway_tool_calls_total[5m])))

# Policy decision mix
sum by (decision, action_category) (rate(gateway_policy_decisions_total[5m]))
```

## Correlating Signals

Every audit event, log line, and response header carries `request_id` and `correlation_id`. Given a user complaint with a response header:

1. Grep audit stream for `correlation_id` to see the full chain of events for that request and any sub-agent calls it triggered.
2. Grep application logs for the same `correlation_id` to see diagnostic output from middleware and providers.
3. Use Prometheus to see aggregate behavior at the time of the request.

This is the intended workflow: one identifier, three lenses.

## See Also

- [Governance](../concepts/governance.md) — conceptual overview
- [Audit API Reference](../api/audit.md) — `AuditEvent` schema
- [Compliance Guide](compliance.md) — SOC2 / GDPR alignment and retention
