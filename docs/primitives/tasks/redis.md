# Redis Tasks

Redis-backed task board for multi-replica production deployments. Required for agent teams running across multiple gateway instances.

## Configuration

### Server-Side (Gateway Config)

```yaml
providers:
  tasks:
    backend: "agentic_primitives_gateway.primitives.tasks.redis.RedisTasksProvider"
    config:
      redis_url: "redis://localhost:6379/0"
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `redis_url` | `redis://localhost:6379/0` | Redis connection URL |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |

## Running Redis Locally

```bash
docker run -d -p 6379:6379 redis:7-alpine
```

## Using with Agent Teams

The tasks primitive is used internally by the team runner. Configure it alongside your team:

```yaml
providers:
  tasks:
    default: "redis"
    backends:
      redis:
        backend: "agentic_primitives_gateway.primitives.tasks.redis.RedisTasksProvider"
        config:
          redis_url: "${REDIS_URL:=redis://localhost:6379/0}"

teams:
  store:
    backend: redis
    config:
      redis_url: "${REDIS_URL:=redis://localhost:6379/0}"
  specs:
    research-team:
      planner: "planner"
      synthesizer: "synthesizer"
      workers: ["researcher", "coder"]
```

## How It Works

1. **Storage**: each team run's tasks are stored as a Redis hash (`tasks:{team_run_id}`). Fields are task IDs, values are JSON-serialized task objects.
2. **Atomic claiming**: a Lua script checks task status + dependency completion and sets `claimed` in a single round-trip. No distributed locks needed.
3. **Status updates**: task status changes and notes are also atomic Lua-scripted operations.
4. **Multi-replica safe**: multiple gateway replicas can claim and execute tasks concurrently without conflicts.

## When to Use

- Production deployments with multiple gateway replicas
- Agent teams that need shared task state
- Any deployment where task persistence across restarts is required

For single-replica development, use the [In-Memory](in-memory.md) provider instead.

## Prerequisites

- `pip install agentic-primitives-gateway[redis]`
- Running Redis instance (v5.0+)
