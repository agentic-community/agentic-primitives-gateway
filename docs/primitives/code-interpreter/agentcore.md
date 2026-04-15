# AgentCore Code Interpreter

AWS-managed code execution provider backed by Bedrock AgentCore.

## Configuration

### Server-Side (Gateway Config)

```yaml
providers:
  code_interpreter:
    backend: "agentic_primitives_gateway.primitives.code_interpreter.agentcore.AgentCoreCodeInterpreterProvider"
    config:
      region: "us-east-1"
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `region` | `us-east-1` | AWS region for AgentCore |

## Using the Code Interpreter API

All standard code interpreter endpoints work with this provider. See the [Code Interpreter API reference](../../api/code-interpreter.md) for the full endpoint list.

```bash
# Start a session
curl -X POST http://localhost:8000/api/v1/code-interpreter/sessions

# Execute code
curl -X POST http://localhost:8000/api/v1/code-interpreter/sessions/{id}/execute \
  -d '{"code": "print(2 + 2)", "language": "python"}'
```

## Using with Declarative Agents

```yaml
agents:
  specs:
    coder:
      model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
      primitives:
        code_interpreter:
          enabled: true
```

## Prerequisites

- `pip install agentic-primitives-gateway[agentcore]`
- AWS credentials with AgentCore access
