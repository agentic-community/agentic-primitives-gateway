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

The run executes in a background task -- if the client disconnects, the run completes independently. All events are recorded for replay on reconnect.

See [Streaming](../concepts/streaming.md) for team event types, and [Teams](../concepts/teams.md) for the full execution model.

## Runs (History & Status)

Multiple runs can exist per team. Each run has a `team_run_id` generated when the run starts.

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

Reconnects to a running or recently-completed team run. Replays all stored events from the event store with token throttling for smooth playback, then polls for new events if the run is still active. Returns `text/event-stream`.

### Cancel Active Run

```bash
curl -X DELETE http://localhost:8000/api/v1/teams/research-team/runs/abc123/cancel
```

Cancels the active team run using cooperative cancellation. The runner checks an `asyncio.Event` at every worker checkpoint; when triggered, all in-progress tasks are marked as failed and the run terminates. Works for both local runs and runs recovered from a checkpoint on another replica.

```json
{"status": "cancelled", "team_run_id": "abc123"}
```

Returns 404 if no active run exists for the given ID.

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
