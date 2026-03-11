# Streaming

The gateway supports token-by-token streaming for agents, teams, and sub-agent delegation.

## How It Works

Streaming uses Server-Sent Events (SSE). The client sends a POST request and receives a stream of `data:` lines:

```bash
curl -N -X POST http://localhost:8000/api/v1/agents/my-agent/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello"}'
```

```
data: {"type": "stream_start", "session_id": "abc123"}
data: {"type": "token", "content": "Hello"}
data: {"type": "token", "content": " there!"}
data: {"type": "done", "response": "Hello there!", ...}
```

## Event Types

### Agent Streaming (`/agents/{name}/chat/stream`)

| Event | Fields | Description |
|-------|--------|-------------|
| `stream_start` | `session_id` | Stream began |
| `token` | `content` | Text delta from the LLM |
| `tool_call_start` | `name`, `id` | LLM requested a tool call |
| `tool_call_result` | `name`, `id`, `result` | Tool execution completed |
| `sub_agent_token` | `agent`, `content` | Token from a sub-agent (delegation) |
| `sub_agent_tool` | `agent`, `name` | Sub-agent using a tool |
| `done` | `response`, `turns_used`, `tools_called`, `artifacts` | Complete |

### Team Streaming (`/teams/{name}/run/stream`)

| Event | Fields | Description |
|-------|--------|-------------|
| `team_start` | `team_run_id`, `team_name` | Team run began |
| `phase_change` | `phase` | Transitioning (planning/execution/replanning/synthesis) |
| `tasks_created` | `count`, `tasks[]` | Planner/replanner created tasks |
| `worker_start` | `agent` | Worker began its loop |
| `task_claimed` | `agent`, `task_id`, `title` | Worker claimed a task |
| `agent_token` | `agent`, `content`, `task_id?` | Token from a worker |
| `agent_tool` | `agent`, `name`, `task_id?` | Worker using a tool |
| `task_completed` | `agent`, `task_id`, `result` | Task done |
| `task_failed` | `agent`, `task_id`, `error` | Task failed |
| `worker_done` | `agent` | Worker exited |
| `done` | `response`, summary stats | Final response |

## Implementation Details

### Bedrock Streaming

The Bedrock Converse API provides `converse_stream()` which returns a synchronous iterator. Since the gateway is async, we bridge using an `asyncio.Queue`:

1. A background thread reads events from boto3's sync stream
2. Each event is put on the queue via `call_soon_threadsafe`
3. The async generator awaits events from the queue one at a time
4. `None` sentinel signals stream exhaustion

This delivers tokens to the client the moment Bedrock produces them.

### Tool Call Reassembly

Bedrock streams tool calls as incremental events:

```
contentBlockStart  →  toolUse with id + name
contentBlockDelta  →  input JSON chunks (accumulated)
contentBlockStop   →  we parse the complete JSON, emit tool_use_complete
```

### Parallel Sub-Agent Streaming

When a coordinator agent delegates to multiple sub-agents, their streams are merged via a shared `asyncio.Queue`. Each sub-agent task puts events on the queue; the main loop yields them as they arrive. This naturally interleaves events from concurrent sub-agents.

### Background Task Decoupling

Streaming endpoints run the agent/team in a background `asyncio.Task` that feeds events into a queue. The SSE response reads from the queue. If the client disconnects (page refresh, navigation), the task continues to completion.

```
Client → SSE Response → Queue ← Background Task (asyncio.Task)
         (may disconnect)        (always completes)
```

This ensures `_finalize()` runs (storing the conversation turn) and tool calls complete even without a connected client.

### Event Replay

For teams, all events are recorded in an event log (in-memory, optionally persisted to Redis via `RedisEventStore`). When a client reconnects, it can fetch all recorded events via `GET /teams/{name}/runs/{id}/events` and replay them through the same event handler to reconstruct the full UI state: task board, activity log, streaming content, and synthesized response.

For agents, conversation history is reconstructed from the memory provider via `GET /agents/{name}/sessions/{id}`.

## Client Usage

### JavaScript/TypeScript

```typescript
const response = await fetch('/api/v1/agents/my-agent/chat/stream', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ message: 'Hello', session_id: 'abc' }),
});

const reader = response.body.getReader();
const decoder = new TextDecoder();

while (true) {
  const { done, value } = await reader.read();
  if (done) break;

  const chunk = decoder.decode(value, { stream: true });
  for (const line of chunk.split('\n')) {
    if (line.startsWith('data: ')) {
      const event = JSON.parse(line.slice(6));
      if (event.type === 'token') {
        process.stdout.write(event.content);
      }
    }
  }
}
```

### Python

```python
import httpx

with httpx.stream("POST", "http://localhost:8000/api/v1/agents/my-agent/chat/stream",
                   json={"message": "Hello"}) as response:
    for line in response.iter_lines():
        if line.startswith("data: "):
            event = json.loads(line[6:])
            if event["type"] == "token":
                print(event["content"], end="", flush=True)
```
