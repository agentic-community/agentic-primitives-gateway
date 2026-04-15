# AgentCore Policy

AWS-managed policy provider backed by Bedrock AgentCore for Cedar-based policy management, including policy generation from natural language.

## Configuration

### Server-Side (Gateway Config)

```yaml
providers:
  policy:
    backend: "agentic_primitives_gateway.primitives.policy.agentcore.AgentCorePolicyProvider"
    config:
      region: "us-east-1"
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `region` | `us-east-1` | AWS region for AgentCore |

## Using the Policy API

### Create a Policy Engine

```bash
curl -X POST http://localhost:8000/api/v1/policy/engines \
  -H "Content-Type: application/json" \
  -d '{"description": "Agent access control"}'
```

### Add a Policy

```bash
curl -X POST http://localhost:8000/api/v1/policy/engines/{engine_id}/policies \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Allow alice to use memory",
    "policy_body": "permit(principal == User::\"alice\", action == Action::\"memory:store_memory\", resource);"
  }'
```

### Generate a Policy from Natural Language

```bash
curl -X POST http://localhost:8000/api/v1/policy/engines/{engine_id}/generations \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Allow all users to list and search tools, but only admins can register new tools"}'
```

### List Policies

```bash
curl http://localhost:8000/api/v1/policy/engines/{engine_id}/policies
```

## Policy Enforcement

This provider manages policy **definitions**. For **enforcement** at request time, configure the `PolicyEnforcementMiddleware`:

```yaml
enforcement:
  backend: "agentic_primitives_gateway.enforcement.cedar.CedarPolicyEnforcer"
  config:
    policy_refresh_interval: 30
```

See the [Policy Enforcement](../../concepts/policy.md) concept guide for details.

## Prerequisites

- `pip install agentic-primitives-gateway[agentcore]`
- AWS credentials with AgentCore access
