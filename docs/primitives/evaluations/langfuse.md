# Langfuse Evaluations

Self-hosted evaluations provider using [Langfuse](https://langfuse.com/) for score recording and annotation.

## Configuration

### Server-Side (Gateway Config)

```yaml
providers:
  evaluations:
    backend: "agentic_primitives_gateway.primitives.evaluations.langfuse.LangfuseEvaluationsProvider"
    config:
      public_key: "pk-..."
      secret_key: "sk-..."
      base_url: "https://cloud.langfuse.com"
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `public_key` | (none) | Langfuse public key |
| `secret_key` | (none) | Langfuse secret key |
| `base_url` | (none) | Langfuse server URL |

### Per-User Credentials

Credentials can be provided per-request via headers or OIDC attributes using the `apg.langfuse.*` convention. See the [Langfuse Observability](../observability/langfuse.md) page for details on credential resolution.

## Using the Evaluations API

### Record a Score

```bash
curl -X POST http://localhost:8000/api/v1/evaluations/scores \
  -H "Content-Type: application/json" \
  -d '{
    "trace_id": "trace-123",
    "name": "helpfulness",
    "value": 4.5,
    "comment": "Very helpful response"
  }'
```

### List Scores

```bash
curl "http://localhost:8000/api/v1/evaluations/scores?trace_id=trace-123"
```

## Prerequisites

- `pip install agentic-primitives-gateway[langfuse]`
- Langfuse account (cloud or self-hosted)
