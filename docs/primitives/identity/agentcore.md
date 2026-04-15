# AgentCore Identity

AWS-managed identity provider backed by Bedrock AgentCore for workload identity tokens and API key management.

## Configuration

### Server-Side (Gateway Config)

```yaml
providers:
  identity:
    backend: "agentic_primitives_gateway.primitives.identity.agentcore.AgentCoreIdentityProvider"
    config:
      region: "us-east-1"
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `region` | `us-east-1` | AWS region for AgentCore |

## Using the Identity API

### Get a Workload Token

```bash
curl -X POST http://localhost:8000/api/v1/identity/token \
  -H "Content-Type: application/json" \
  -d '{"audience": "https://api.example.com", "scopes": ["read"]}'
```

### Get an API Key

```bash
curl http://localhost:8000/api/v1/identity/api-key
```

## Using with the Python Client

```python
from agentic_primitives_gateway_client import AgenticPlatformClient, Identity

client = AgenticPlatformClient("http://localhost:8000", aws_from_environment=True)
identity = Identity(client)

token = await identity.get_token(audience="https://api.example.com")
workload_token = await identity.get_workload_token()
```

## Prerequisites

- `pip install agentic-primitives-gateway[agentcore]`
- AWS credentials with AgentCore access
