# Observability API

`/api/v1/observability`

Trace and log ingestion, LLM generation tracking, evaluation scoring, and session management. All endpoints require authentication.

**Backends:** `NoopObservabilityProvider`, `LangfuseObservabilityProvider`, `AgentCoreObservabilityProvider`

## Trace Ingestion

| Method | Path | Description |
|---|---|---|
| `POST` | `/traces` | Ingest a trace. Returns 202. |
| `POST` | `/logs` | Ingest a log entry. Returns 202. |
| `GET` | `/traces` | Query traces. Query params: `trace_id`, `limit` (1--1000, default 100). |

```bash
curl -X POST http://localhost:8000/api/v1/observability/traces \
  -H "Content-Type: application/json" \
  -d '{"name": "agent-run", "input": "user query", "output": "response", "metadata": {}}'
```

## Trace Retrieval and Updates

| Method | Path | Description |
|---|---|---|
| `GET` | `/traces/{trace_id}` | Get a single trace. Returns 404 if not found, 501 if not supported. |
| `PUT` | `/traces/{trace_id}` | Update trace metadata. Returns 501 if not supported. |

**Update request body:**

| Field | Type | Description |
|---|---|---|
| `name` | string | Trace name. |
| `user_id` | string | Associated user. |
| `session_id` | string | Associated session. |
| `input` | any | Trace input data. |
| `output` | any | Trace output data. |
| `metadata` | object | Key-value metadata. |
| `tags` | list[string] | Tags for filtering. |

## LLM Generation Logging

| Method | Path | Description |
|---|---|---|
| `POST` | `/traces/{trace_id}/generations` | Log an LLM call. Returns 201 or 501. |

```bash
curl -X POST http://localhost:8000/api/v1/observability/traces/t1/generations \
  -H "Content-Type: application/json" \
  -d '{
    "name": "completion",
    "model": "claude-sonnet",
    "input": "What is 2+2?",
    "output": "4",
    "usage": {"prompt_tokens": 10, "completion_tokens": 5}
  }'
```

## Evaluation Scoring

| Method | Path | Description |
|---|---|---|
| `POST` | `/traces/{trace_id}/scores` | Attach an evaluation score. Returns 201 or 501. |
| `GET` | `/traces/{trace_id}/scores` | List scores for a trace. Returns 501 if not supported. |

```bash
curl -X POST http://localhost:8000/api/v1/observability/traces/t1/scores \
  -H "Content-Type: application/json" \
  -d '{"name": "helpfulness", "value": 0.95, "comment": "Accurate response"}'
```

## Sessions

| Method | Path | Description |
|---|---|---|
| `GET` | `/sessions` | List observability sessions. Query params: `user_id`, `limit` (1--1000, default 100). Non-admins can only query their own sessions. |
| `GET` | `/sessions/{session_id}` | Get session details. Returns 501 if not supported. |

## Flush

| Method | Path | Description |
|---|---|---|
| `POST` | `/flush` | Force flush pending telemetry. Returns 202 or 501. |

## Backend Support

| Feature | Langfuse | AgentCore | Noop |
|---|---|---|---|
| Trace ingestion | yes | yes | no-op |
| Trace retrieval | yes | yes | no |
| Generation logging | yes | yes | no |
| Scoring | yes | no | no |
| Sessions | yes | no | no |
| Flush | yes | yes | no |
