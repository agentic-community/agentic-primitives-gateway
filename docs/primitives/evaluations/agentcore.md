# AgentCore Evaluations

AWS-managed evaluations provider backed by Bedrock AgentCore for LLM-as-a-judge evaluation.

## Configuration

### Server-Side (Gateway Config)

```yaml
providers:
  evaluations:
    backend: "agentic_primitives_gateway.primitives.evaluations.agentcore.AgentCoreEvaluationsProvider"
    config:
      region: "us-east-1"
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `region` | `us-east-1` | AWS region for AgentCore |

## Using the Evaluations API

### Create an Evaluator

```bash
curl -X POST http://localhost:8000/api/v1/evaluations/evaluators \
  -H "Content-Type: application/json" \
  -d '{
    "name": "helpfulness",
    "description": "Evaluates response helpfulness",
    "scoring_rubric": "1-5 scale: 1=unhelpful, 5=very helpful"
  }'
```

### Run an Evaluation

```bash
curl -X POST http://localhost:8000/api/v1/evaluations/evaluate \
  -H "Content-Type: application/json" \
  -d '{
    "evaluator_id": "eval-123",
    "input": "What is the capital of France?",
    "output": "The capital of France is Paris.",
    "expected": "Paris"
  }'
```

### List Evaluators

```bash
curl http://localhost:8000/api/v1/evaluations/evaluators
```

## Prerequisites

- `pip install agentic-primitives-gateway[agentcore]`
- AWS credentials with AgentCore access
