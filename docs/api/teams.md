# Teams API

Prefix: `/api/v1/teams`

## CRUD

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/` | Create team |
| `GET` | `/` | List teams |
| `GET` | `/{name}` | Get team spec |
| `PUT` | `/{name}` | Update team |
| `DELETE` | `/{name}` | Delete team |

### Create Team

```bash
curl -X POST http://localhost:8000/api/v1/teams \
  -H "Content-Type: application/json" \
  -d '{
    "name": "research-team",
    "description": "Researches and codes",
    "planner": "planner",
    "synthesizer": "synthesizer",
    "workers": ["researcher", "coder"],
    "global_max_turns": 100,
    "global_timeout_seconds": 300
  }'
```

## Run

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/{name}/run` | Run team (non-streaming) |
| `POST` | `/{name}/run/stream` | Run team (SSE streaming) |

### Non-Streaming Run

```bash
curl -X POST http://localhost:8000/api/v1/teams/research-team/run \
  -H "Content-Type: application/json" \
  -d '{"message": "Research Python web frameworks and write benchmarks"}'
```

Response:

```json
{
  "response": "Here are the findings and benchmarks...",
  "team_run_id": "abc123",
  "team_name": "research-team",
  "phase": "done",
  "tasks_created": 5,
  "tasks_completed": 5,
  "workers_used": ["researcher", "coder"]
}
```

### Streaming Run

```bash
curl -N -X POST http://localhost:8000/api/v1/teams/research-team/run/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "Research Python web frameworks and write benchmarks"}'
```

See [Streaming](../concepts/streaming.md) for team event types, and [Teams](../concepts/teams.md) for the full execution model.
