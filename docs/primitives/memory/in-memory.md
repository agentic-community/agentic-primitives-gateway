# In-Memory

Simple dictionary-based memory provider for development and testing. Data is not persisted across restarts.

## Configuration

```yaml
providers:
  memory:
    backend: "agentic_primitives_gateway.primitives.memory.in_memory.InMemoryProvider"
    config: {}
```

No configuration parameters required. This is the default provider used by the `quickstart.yaml` config.

## Using the Memory API

All standard memory endpoints work with this provider:

```bash
# Store
curl -X POST http://localhost:8000/api/v1/memory/agent:demo \
  -H "Content-Type: application/json" \
  -d '{"key": "greeting", "content": "Hello world"}'

# Retrieve
curl http://localhost:8000/api/v1/memory/agent:demo/greeting

# Search (keyword-based)
curl "http://localhost:8000/api/v1/memory/agent:demo/search?query=hello"

# List all
curl "http://localhost:8000/api/v1/memory/agent:demo?limit=20"

# Delete
curl -X DELETE http://localhost:8000/api/v1/memory/agent:demo/greeting
```

## Using with the Python Client

```python
from agentic_primitives_gateway_client import AgenticPlatformClient, Memory

client = AgenticPlatformClient("http://localhost:8000")
memory = Memory(client, namespace="agent:demo")

# Sync API
memory.remember_sync("greeting", "Hello world")
record = memory.recall_sync("greeting")
results = memory.search_sync("hello")
```

## When to Use

- Local development and prototyping
- Unit and integration testing
- Single-replica deployments where persistence isn't needed

## Limitations

- Data is lost on restart
- Search is keyword-based (substring match), not semantic
- Single-replica only; no shared state between gateway instances
- Not suitable for production workloads
