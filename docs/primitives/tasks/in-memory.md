# In-Memory Tasks

Simple in-memory task board for single-replica development and testing. Used internally by agent teams.

## Configuration

```yaml
providers:
  tasks:
    backend: "agentic_primitives_gateway.primitives.tasks.in_memory.InMemoryTasksProvider"
    config: {}
```

No configuration parameters required. This is the default tasks provider.

## When to Use

- Local development and prototyping of agent teams
- Single-replica deployments
- Testing team orchestration workflows

For multi-replica production deployments, use the [Redis](redis.md) tasks provider.

## How It Works

Tasks are stored in a Python dictionary keyed by `(team_run_id, task_id)`. Task claiming checks dependencies synchronously. All operations are atomic within a single process but not across replicas.

## Limitations

- Data is lost on restart
- Single-replica only: no shared state between gateway instances
- Not suitable for production multi-replica deployments
