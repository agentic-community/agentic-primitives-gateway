# Langfuse Observability

Self-hosted observability provider using [Langfuse](https://langfuse.com/) for tracing, logging, and LLM generation tracking.

## Configuration

### Server-Side (Gateway Config)

```yaml
providers:
  observability:
    backend: "agentic_primitives_gateway.primitives.observability.langfuse.LangfuseObservabilityProvider"
    config:
      public_key: "pk-..."
      secret_key: "sk-..."
      base_url: "https://cloud.langfuse.com"
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `public_key` | (none) | Langfuse public key (fallback if not provided per-request) |
| `secret_key` | (none) | Langfuse secret key (fallback) |
| `base_url` | (none) | Langfuse server URL |

### Per-User Credentials

In multi-tenant deployments, each user can have their own Langfuse project:

```bash
curl -H "X-Cred-Langfuse-Public-Key: pk-..." \
     -H "X-Cred-Langfuse-Secret-Key: sk-..." \
     -H "X-Cred-Langfuse-Base-Url: https://cloud.langfuse.com" \
     http://localhost:8000/api/v1/observability/traces
```

Or via the Python client:

```python
client.set_service_credentials("langfuse", {
    "public_key": "pk-...",
    "secret_key": "sk-...",
    "base_url": "https://cloud.langfuse.com",
})
```

Or via OIDC attributes (Keycloak):

| User Attribute | Maps To |
|----------------|---------|
| `apg.langfuse.public_key` | Langfuse public key |
| `apg.langfuse.secret_key` | Langfuse secret key |
| `apg.langfuse.base_url` | Langfuse server URL |

**Credential resolution order:**

1. Client headers (`X-Cred-Langfuse-*`): always win
2. OIDC-resolved attributes (`apg.langfuse.*`): per-user
3. Provider config: server ambient

## Using the Observability API

### Create a Trace

```bash
curl -X POST http://localhost:8000/api/v1/observability/traces \
  -H "Content-Type: application/json" \
  -d '{
    "name": "agent-run",
    "metadata": {"agent": "researcher", "session": "abc123"}
  }'
```

### Log an Event

```bash
curl -X POST http://localhost:8000/api/v1/observability/logs \
  -H "Content-Type: application/json" \
  -d '{
    "trace_id": "trace-123",
    "level": "INFO",
    "message": "Tool call completed",
    "metadata": {"tool": "recall", "duration_ms": 150}
  }'
```

## Using with Declarative Agents

Agents automatically trace when `auto_trace` is enabled:

```yaml
agents:
  specs:
    traced-agent:
      model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
      primitives:
        memory:
          enabled: true
      hooks:
        auto_trace: true
```

## Using with the Python Client

```python
from agentic_primitives_gateway_client import AgenticPlatformClient, Observability

client = AgenticPlatformClient("http://localhost:8000")
obs = Observability(client)

# Create a trace
trace = await obs.trace(name="my-task", metadata={"user": "alice"})

# Log within a trace
await obs.log(trace_id=trace["id"], level="INFO", message="Started processing")

# Flush pending events
await obs.flush()
```

## Prerequisites

- `pip install agentic-primitives-gateway[langfuse]`
- Langfuse account ([cloud](https://cloud.langfuse.com) or [self-hosted](https://langfuse.com/docs/deployment/self-host))
