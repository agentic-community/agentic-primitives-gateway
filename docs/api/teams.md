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
    "global_timeout_seconds": 300,
    "shared_memory_namespace": "team:{team_owner}:{team_name}:shared"
  }'
```

The `shared_memory_namespace` field enables team-scoped shared memory. When set, all workers receive `share_finding`, `read_shared`, `search_shared`, and `list_shared` tools that read/write to the same memory namespace. User-owned teams create pools beneath `team:{team_owner}:…`; both placeholders expand at runtime. System-seeded teams may retain operator-defined namespaces.

## Export

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/{name}/export` | Export team as a standalone Python script |

### Export Team

```bash
curl http://localhost:8000/api/v1/teams/research-team/export -o research-team.py
```

Generates a self-contained Python script that includes the planner, all worker agents, and the synthesizer. Uses `agentic-primitives-gateway-client` for primitive calls and `boto3` Bedrock `converse()` for LLM loops. The exported script handles dependency-aware task execution (tasks with `depends_on` wait for their dependencies), per-task session isolation for browser/code_interpreter, shared memory namespace support, and a live-updating task board (using `rich` if available).

The response has `Content-Type: text/x-python` and `Content-Disposition: attachment`.

## Run

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/{name}/run` | Run team (non-streaming) |
| `POST` | `/{name}/run/stream` | Run team (SSE streaming, background task) |

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

The run executes in a background task: if the client disconnects, the run completes independently. All events are recorded for replay on reconnect.

See [Streaming](../concepts/streaming.md) for team event types, and [Teams](../concepts/teams.md) for the full execution model.

## Runs (History & Status)

Multiple runs can exist per team. Each run has a generated `team_run_id`; its event replay, status, task board, and cancellation are restricted to the caller and the resolved team identity. Agent sessions, other teams, and task retries use separate event keys even when their raw IDs match. Unknown or foreign runs are denied.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/{name}/runs` | List all known runs for this team |
| `GET` | `/{name}/runs/{id}` | Get task board state (tasks with status/result) |
| `GET` | `/{name}/runs/{id}/status` | Check if run is active (`"running"` or `"idle"`) |
| `GET` | `/{name}/runs/{id}/events` | Get all recorded SSE events (for UI replay) |
| `GET` | `/{name}/runs/{id}/stream` | SSE reconnect stream |
| `DELETE` | `/{name}/runs/{id}/cancel` | Cancel active run |
| `DELETE` | `/{name}/runs/{id}` | Delete run data (tasks, events) |

### SSE Reconnect Stream

```bash
curl -N http://localhost:8000/api/v1/teams/research-team/runs/abc123/stream
```

Reconnects to a running or recently-completed team run owned by the caller. Replays only this team's stored events from the event store with token throttling, then polls for new events if the run is still active. Returns `text/event-stream`.

### Cancel Active Run

```bash
curl -X DELETE http://localhost:8000/api/v1/teams/research-team/runs/abc123/cancel
```

Cancellation checks a caller- and team-scoped signal before further planner, replanner and worker tool calls. Pending and in-progress tasks are marked failed. A recovery attempt rechecks cancellation before reviving a checkpoint, and its renewable lock prevents two replicas from executing the same run. The response is unavailable for unknown or foreign runs.

```json
{"status": "cancelled"}
```

Returns 403 for unknown or foreign team runs.

### List Runs

```bash
curl http://localhost:8000/api/v1/teams/research-team/runs
```

```json
{
  "team_name": "research-team",
  "runs": [
    {"team_run_id": "abc123", "status": "idle"},
    {"team_run_id": "def456", "status": "running"}
  ]
}
```

### Get Task Board State

```bash
curl http://localhost:8000/api/v1/teams/research-team/runs/abc123
```

```json
{
  "team_run_id": "abc123",
  "team_name": "research-team",
  "status": "idle",
  "tasks": [
    {"id": "t1", "title": "Research frameworks", "status": "done", "assigned_to": "researcher", "result": "..."},
    {"id": "t2", "title": "Write benchmarks", "status": "done", "assigned_to": "coder", "result": "..."}
  ],
  "tasks_created": 2,
  "tasks_completed": 2
}
```

### Get Events for Replay

```bash
curl http://localhost:8000/api/v1/teams/research-team/runs/abc123/events
```

Returns all recorded SSE events. The UI replays these to reconstruct the full task board, activity log, streaming content, and synthesized response after a page refresh or navigation.

```json
{
  "team_run_id": "abc123",
  "status": "idle",
  "events": [
    {"type": "team_start", "team_run_id": "abc123", "team_name": "research-team"},
    {"type": "phase_change", "phase": "planning"},
    {"type": "tasks_created", "count": 2, "tasks": [...]},
    {"type": "task_claimed", "agent": "researcher", "task_id": "t1", "title": "Research frameworks"},
    {"type": "task_completed", "agent": "researcher", "task_id": "t1", "result": "..."},
    {"type": "done", "response": "...", "tasks_created": 2, "tasks_completed": 2, "workers_used": ["researcher", "coder"]}
  ]
}
```

### Check Background Run Status

```bash
curl http://localhost:8000/api/v1/teams/research-team/runs/abc123/status
```

```json
{"status": "running"}
```

Returns `"running"` if a background task is actively executing this run, `"idle"` otherwise. After a server restart, stale `"running"` statuses in Redis are detected and reported as `"idle"`.

## Task Retry

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/{name}/runs/{id}/tasks/{task_id}/retry` | Retry a single failed task (SSE streaming) |

### Retry a Failed Task

```bash
curl -N -X POST http://localhost:8000/api/v1/teams/research-team/runs/abc123/tasks/t1/retry
```

Resets the failed task to `in_progress`, recovers partial tokens from the event store as resume context, and re-executes the assigned worker agent. Returns an SSE stream of events for the retry. Useful for recovering from transient failures without re-running the entire team.
