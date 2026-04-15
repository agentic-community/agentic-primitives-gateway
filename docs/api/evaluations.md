# Evaluations API

`/api/v1/evaluations`

Evaluator management, LLM-as-a-judge evaluation, and score recording. All endpoints require authentication.

**Backends:** `NoopEvaluationsProvider`, [`LangfuseEvaluationsProvider`](../primitives/evaluations/langfuse.md), [`AgentCoreEvaluationsProvider`](../primitives/evaluations/agentcore.md)

## Evaluator Management

| Method | Path | Description |
|---|---|---|
| `POST` | `/evaluators` | Create an evaluator. Returns 201. |
| `GET` | `/evaluators` | List evaluators. Query params: `max_results` (default 100), `next_token`. |
| `GET` | `/evaluators/{evaluator_id}` | Get an evaluator. |
| `PUT` | `/evaluators/{evaluator_id}` | Update an evaluator. |
| `DELETE` | `/evaluators/{evaluator_id}` | Delete an evaluator. Returns 204. |

```bash
curl -X POST http://localhost:8000/api/v1/evaluations/evaluators \
  -H "Content-Type: application/json" \
  -d '{
    "name": "helpfulness",
    "evaluator_type": "numeric",
    "config": {"min_value": 0.0, "max_value": 1.0},
    "description": "Rates response helpfulness"
  }'
```

Evaluator types vary by backend:

| Backend | Evaluator types |
|---|---|
| **AgentCore** | `TRACE`, `TOOL_CALL`, `SESSION` (LLM-as-a-judge configs) |
| **Langfuse** | `numeric`, `boolean`, `categorical` (Score Config data types) |
| **Noop** | Any string (in-memory only) |

## Evaluate

| Method | Path | Description |
|---|---|---|
| `POST` | `/evaluate` | Run an evaluation or record a score. |

The behavior depends on the backend:

- **AgentCore**: Runs LLM-as-a-judge server-side — sends input/output to AgentCore, which calls the LLM and returns computed scores.
- **Langfuse**: Records a score against a trace. Pass `metadata.value` to set the score. For LLM-as-a-judge, configure evaluators in the [Langfuse UI](https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge) — Langfuse runs evaluations automatically.
- **Noop**: Returns a placeholder score.

```bash
# AgentCore: LLM-as-a-judge (computes a score)
curl -X POST http://localhost:8000/api/v1/evaluations/evaluate \
  -H "Content-Type: application/json" \
  -d '{
    "evaluator_id": "eval-123",
    "input_data": "What is the capital of France?",
    "output_data": "The capital of France is Paris.",
    "expected_output": "Paris"
  }'

# Langfuse: Record a score against a trace
curl -X POST http://localhost:8000/api/v1/evaluations/evaluate \
  -H "Content-Type: application/json" \
  -d '{
    "evaluator_id": "helpfulness",
    "target": "trace-abc123",
    "output_data": "The capital of France is Paris.",
    "metadata": {"value": 0.95}
  }'
```

**Request body:**

| Field | Type | Required | Description |
|---|---|---|---|
| `evaluator_id` | string | yes | ID of the evaluator (AgentCore) or score name (Langfuse). |
| `target` | string | no | Trace ID to associate the evaluation with. |
| `input_data` | string | no | The input/question. |
| `output_data` | string | no | The model's output. |
| `expected_output` | string | no | The expected/reference output. |
| `metadata` | object | no | Additional context. Langfuse: set `value` for the score. |

**Built-in evaluators (AgentCore):** `Builtin.Helpfulness`, `Builtin.Coherence`, `Builtin.Relevance`, `Builtin.Correctness`

## Scores

Record, retrieve, and manage pre-computed evaluation scores. Returns 501 if not supported by the configured provider. Only `LangfuseEvaluationsProvider` supports score CRUD.

| Method | Path | Description |
|---|---|---|
| `POST` | `/scores` | Record a score. Returns 201 or 501. |
| `GET` | `/scores` | List scores. Query params: `trace_id`, `name`, `config_id`, `data_type`, `page`, `limit`. Returns 501 if not supported. |
| `GET` | `/scores/{score_id}` | Get a score by ID. Returns 404/501. |
| `DELETE` | `/scores/{score_id}` | Delete a score. Returns 204 or 501. |

### Record a score

```bash
curl -X POST http://localhost:8000/api/v1/evaluations/scores \
  -H "Content-Type: application/json" \
  -d '{
    "name": "accuracy",
    "value": 0.92,
    "trace_id": "trace-abc123",
    "comment": "Correct and concise",
    "data_type": "NUMERIC"
  }'
```

**Request body:**

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Score name (e.g., `accuracy`, `helpfulness`). |
| `value` | float or string | yes | Score value. Numeric for `NUMERIC`/`BOOLEAN`, string for `CATEGORICAL`. |
| `trace_id` | string | no | Trace ID to associate the score with. |
| `observation_id` | string | no | Observation ID within the trace. |
| `comment` | string | no | Explanation or notes. |
| `data_type` | string | no | `NUMERIC`, `BOOLEAN`, or `CATEGORICAL`. |
| `config_id` | string | no | Score config (evaluator) ID. |
| `metadata` | object | no | Key-value metadata. |

### List scores

```bash
# All scores for a trace
curl "http://localhost:8000/api/v1/evaluations/scores?trace_id=trace-abc123"

# Filter by name
curl "http://localhost:8000/api/v1/evaluations/scores?name=accuracy&limit=10"
```

## Online Evaluation Configs (Optional)

Returns 501 if not supported. Only `AgentCoreEvaluationsProvider` supports online configs.

| Method | Path | Description |
|---|---|---|
| `POST` | `/online-configs` | Create an online eval config. Returns 201 or 501. |
| `GET` | `/online-configs` | List online eval configs. Returns 501 if not supported. |
| `GET` | `/online-configs/{config_id}` | Get an online eval config. Returns 501 if not supported. |
| `DELETE` | `/online-configs/{config_id}` | Delete an online eval config. Returns 204 or 501. |

## Backend Support

| Feature | Noop | Langfuse | AgentCore |
|---|---|---|---|
| Evaluator CRUD | yes (in-memory) | yes (Score Configs) | yes (LLM-as-a-judge configs) |
| Evaluate | yes (placeholder) | yes (record score) | yes (LLM-as-a-judge) |
| Score CRUD | no | yes | no |
| Online eval configs | no | no | yes |
