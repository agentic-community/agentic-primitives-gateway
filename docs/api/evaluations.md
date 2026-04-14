# Evaluations API

`/api/v1/evaluations`

LLM-as-a-judge evaluator management and evaluation. All endpoints require authentication.

**Backends:** `NoopEvaluationsProvider`, `AgentCoreEvaluationsProvider`

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
    "evaluator_type": "Builtin.Helpfulness",
    "description": "Evaluates response helpfulness"
  }'
```

## Evaluate

| Method | Path | Description |
|---|---|---|
| `POST` | `/evaluate` | Run an evaluation. |

```bash
curl -X POST http://localhost:8000/api/v1/evaluations/evaluate \
  -H "Content-Type: application/json" \
  -d '{
    "evaluator_id": "eval-123",
    "input_data": "What is the capital of France?",
    "output_data": "The capital of France is Paris.",
    "expected_output": "Paris"
  }'
```

**Request body:**

| Field | Type | Required | Description |
|---|---|---|---|
| `evaluator_id` | string | yes | ID of the evaluator to use. |
| `target` | string | no | Evaluation target identifier. |
| `input_data` | string | no | The input/question. |
| `output_data` | string | no | The model's output. |
| `expected_output` | string | no | The expected/reference output. |
| `metadata` | object | no | Additional context for evaluation. |

**Built-in evaluators:** `Builtin.Helpfulness`, `Builtin.Coherence`, `Builtin.Relevance`, `Builtin.Correctness`

## Online Evaluation Configs (Optional)

Returns 501 if not supported. Only `AgentCoreEvaluationsProvider` supports online configs.

| Method | Path | Description |
|---|---|---|
| `POST` | `/online-configs` | Create an online eval config. Returns 201 or 501. |
| `GET` | `/online-configs` | List online eval configs. Returns 501 if not supported. |
| `GET` | `/online-configs/{config_id}` | Get an online eval config. Returns 501 if not supported. |
| `DELETE` | `/online-configs/{config_id}` | Delete an online eval config. Returns 204 or 501. |
