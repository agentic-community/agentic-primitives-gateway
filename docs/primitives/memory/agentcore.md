# AgentCore Memory

AWS-managed memory provider backed by Bedrock AgentCore. Provides vector-based semantic search with automatic embedding generation.

## Configuration

### Server-Side (Gateway Config)

```yaml
providers:
  memory:
    backend: "agentic_primitives_gateway.primitives.memory.agentcore.AgentCoreMemoryProvider"
    config:
      region: "us-east-1"
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `region` | `us-east-1` | AWS region for AgentCore |
| `memory_id` | (auto) | Optional memory store ID |

### Environment Variables

AWS credentials can be provided via environment variables, IRSA, EKS Pod Identity, or client headers.

| Variable | Description |
|----------|-------------|
| `AWS_REGION` | AWS region (fallback if not in config) |
| `AWS_ACCESS_KEY_ID` | AWS access key |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key |
| `AWS_SESSION_TOKEN` | Session token (for temporary credentials) |

## Using the Memory API

### Store a Memory

```bash
curl -X POST http://localhost:8000/api/v1/memory/agent:my-agent \
  -H "Content-Type: application/json" \
  -d '{"key": "user-preference", "content": "User prefers dark mode"}'
```

### Search Memories

```bash
curl "http://localhost:8000/api/v1/memory/agent:my-agent/search?query=preferences&top_k=5"
```

### Retrieve by Key

```bash
curl http://localhost:8000/api/v1/memory/agent:my-agent/user-preference
```

## Using with Declarative Agents

```yaml
agents:
  specs:
    my-agent:
      model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
      primitives:
        memory:
          enabled: true
          namespace: "agent:{agent_name}:{session_id}"
      hooks:
        auto_memory: true
```

When `auto_memory` is enabled, the agent automatically stores conversation summaries after each turn.

## Using with the Python Client

```python
from agentic_primitives_gateway_client import AgenticPlatformClient, Memory

client = AgenticPlatformClient("http://localhost:8000", aws_from_environment=True)
memory = Memory(client, namespace="agent:my-agent")

# Store
await memory.remember("user-preference", "User prefers dark mode")

# Search
results = await memory.search("preferences")

# Retrieve
record = await memory.recall("user-preference")
```

## Prerequisites

- `pip install agentic-primitives-gateway[agentcore]`
- AWS credentials with Bedrock AgentCore access
- AgentCore memory enabled in your account

## Backend Comparison

| Feature | AgentCore | Mem0 + Milvus | In-Memory |
|---------|-----------|---------------|-----------|
| Semantic search | yes | yes | keyword only |
| Persistence | managed | self-hosted | none (lost on restart) |
| Multi-replica | yes | yes | no |
| Auto embeddings | yes | yes | no |
| Dependencies | `boto3` | `mem0ai`, `pymilvus` | none |
