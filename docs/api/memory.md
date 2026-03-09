# Memory API

Prefix: `/api/v1/memory`

## Key-Value Memory

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/{namespace}` | Store a memory |
| `GET` | `/{namespace}/{key}` | Retrieve by key |
| `GET` | `/{namespace}` | List memories (query: `limit`, `offset`) |
| `POST` | `/{namespace}/search` | Semantic search |
| `DELETE` | `/{namespace}/{key}` | Delete by key |
| `GET` | `/namespaces` | List all known namespaces |

### Store a Memory

```bash
curl -X POST http://localhost:8000/api/v1/memory/my-namespace \
  -H "Content-Type: application/json" \
  -d '{
    "key": "user-preference",
    "content": "Prefers dark mode and Python",
    "metadata": {"source": "chat"}
  }'
```

### Search Memories

```bash
curl -X POST http://localhost:8000/api/v1/memory/my-namespace/search \
  -H "Content-Type: application/json" \
  -d '{"query": "user preferences", "top_k": 5}'
```

## Conversation Events

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/sessions/{actor_id}/{session_id}/events` | Create event |
| `GET` | `/sessions/{actor_id}/{session_id}/events` | List events |
| `GET` | `/sessions/{actor_id}/{session_id}/events/{event_id}` | Get event |
| `DELETE` | `/sessions/{actor_id}/{session_id}/events/{event_id}` | Delete event |
| `GET` | `/sessions/{actor_id}/{session_id}/turns` | Get last K turns |

## Session Management

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/actors` | List actors |
| `GET` | `/actors/{actor_id}/sessions` | List sessions for actor |

## Branch Management

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/sessions/{actor_id}/{session_id}/branches` | Fork conversation |
| `GET` | `/sessions/{actor_id}/{session_id}/branches` | List branches |

## Memory Resources (Control Plane)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/resources` | Create memory resource |
| `GET` | `/resources` | List resources |
| `GET` | `/resources/{memory_id}` | Get resource |
| `DELETE` | `/resources/{memory_id}` | Delete resource |

## Strategy Management

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/resources/{memory_id}/strategies` | List strategies |
| `POST` | `/resources/{memory_id}/strategies` | Add strategy |
| `DELETE` | `/resources/{memory_id}/strategies/{strategy_id}` | Delete strategy |
