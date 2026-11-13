# AgentCore Observability

AWS-managed observability provider backed by Bedrock AgentCore with OpenTelemetry integration.

## Configuration

### Server-Side (Gateway Config)

```yaml
providers:
  observability:
    backend: "agentic_primitives_gateway.primitives.observability.agentcore.AgentCoreObservabilityProvider"
    config:
      region: "us-east-1"
      service_name: "agentic-primitives-gateway"
      agent_id: "agentic-primitives-gateway"
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `region` | `us-east-1` | AWS region for AgentCore |
| `service_name` | `agentic-primitives-gateway` | OpenTelemetry service name |
| `agent_id` | `agentic-primitives-gateway` | Agent identifier for traces |

**Credentials:** trace/log writes (`ingest_trace`, `ingest_log`, `log_generation`, and request-triggered `flush`) go through one process-wide OTLP exporter signed with the gateway's own AWS identity — they are operator-owned. They are refused under `allow_server_credentials: never` and audited as `provider.server_credentials_used` otherwise. Trace reads (X-Ray queries) use the request's AWS credentials.

## Using the Observability API

All standard observability endpoints work with this provider:

```bash
# Create a trace
curl -X POST http://localhost:8000/api/v1/observability/traces \
  -H "Content-Type: application/json" \
  -d '{"name": "agent-run", "metadata": {"agent": "researcher"}}'

# Log an event
curl -X POST http://localhost:8000/api/v1/observability/logs \
  -H "Content-Type: application/json" \
  -d '{"trace_id": "trace-123", "level": "INFO", "message": "Task completed"}'
```

## Using with Declarative Agents

```yaml
agents:
  specs:
    traced-agent:
      model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
      hooks:
        auto_trace: true
```

## Prerequisites

- `pip install agentic-primitives-gateway[agentcore]`
- AWS credentials with AgentCore access
