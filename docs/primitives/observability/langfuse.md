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
| `base_url` | `LANGFUSE_BASE_URL`, then `LANGFUSE_HOST`, then `https://cloud.langfuse.com` | Operator-selected Langfuse server URL for SDK, REST, and health checks |

### Per-User Credentials

In multi-tenant deployments, each user can have their own Langfuse project:

```bash
curl -H "X-Cred-Langfuse-Public-Key: pk-..." \
     -H "X-Cred-Langfuse-Secret-Key: sk-..." \
     http://localhost:8000/api/v1/observability/traces
```

Or via the Python client:

```python
client.set_service_credentials("langfuse", {
    "public_key": "pk-...",
    "secret_key": "sk-...",
})
```

Or via OIDC attributes (Keycloak):

| User Attribute | Maps To |
|----------------|---------|
| `apg.langfuse.public_key` | Langfuse public key |
| `apg.langfuse.secret_key` | Langfuse secret key |

The Langfuse server URL is operator configuration only (`base_url` in the provider config, then `LANGFUSE_BASE_URL`, then `LANGFUSE_HOST`, then Langfuse cloud). Caller-supplied `base_url` values (headers or `apg.langfuse.base_url`) are ignored and audited as `network.access.denied`; letting a caller choose the host would send credentials — including operator fallback keys — to that host.

Both keys are required together — a partial pair is refused rather than letting the Langfuse SDK fill the secret from `LANGFUSE_SECRET_KEY`. The Langfuse v3 SDK caches one client per *public key* process-wide. The gateway verifies an unbound key pair against the configured host before SDK construction, with a bounded verification worker pool and short HTTP timeouts; concurrent requests with the same unbound key pair share one verification. A failed verification never creates a cached SDK client. After a public key is bound, requests with a different secret or host are rejected locally without HTTP requests; changing that key's secret requires restarting the gateway. Live rotation would shut down a manager still used by in-flight requests.

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
