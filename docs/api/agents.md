# Agents API

Prefix: `/api/v1/agents`

## CRUD

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/` | Create agent. Returns 201. Returns 409 if name exists. |
| `GET` | `/` | List all agents. |
| `GET` | `/{name}` | Get agent spec. |
| `PUT` | `/{name}` | Update agent (partial). |
| `DELETE` | `/{name}` | Delete agent. |

### Create Agent

```bash
curl -X POST http://localhost:8000/api/v1/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-agent",
    "model": "us.anthropic.claude-sonnet-4-20250514-v1:0",
    "description": "A helpful assistant",
    "system_prompt": "You are helpful.",
    "primitives": {
      "memory": {"enabled": true, "namespace": "agent:{agent_name}"}
    },
    "hooks": {"auto_memory": true, "auto_trace": false},
    "max_turns": 20
  }'
```

## Chat

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/{name}/chat` | Non-streaming chat |
| `POST` | `/{name}/chat/stream` | SSE streaming chat (background task) |

### Non-Streaming Chat

```bash
curl -X POST http://localhost:8000/api/v1/agents/my-agent/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello!", "session_id": "optional-session-id"}'
```

Response:

```json
{
  "response": "Hello! How can I help you?",
  "session_id": "abc123",
  "agent_name": "my-agent",
  "turns_used": 1,
  "tools_called": [],
  "artifacts": [],
  "metadata": {"trace_id": "..."}
}
```

### Streaming Chat

```bash
curl -N -X POST http://localhost:8000/api/v1/agents/my-agent/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello!", "session_id": "my-session"}'
```

Returns `text/event-stream`. The run executes in a background task -- if the client disconnects, the run completes and stores the conversation turn. See [Streaming](../concepts/streaming.md) for event types.

## Sessions

Each chat uses a `session_id` to track conversation history. Multiple sessions can exist per agent.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/{name}/sessions` | List all sessions for this agent |
| `GET` | `/{name}/sessions/{session_id}` | Get conversation history |
| `GET` | `/{name}/sessions/{session_id}/status` | Check if a background run is active (`"running"` or `"idle"`) |
| `DELETE` | `/{name}/sessions/{session_id}` | Delete session history |

### List Sessions

```bash
curl http://localhost:8000/api/v1/agents/my-agent/sessions
```

### Get Session History

```bash
curl http://localhost:8000/api/v1/agents/my-agent/sessions/my-session
```

```json
{
  "agent_name": "my-agent",
  "session_id": "my-session",
  "messages": [
    {"role": "user", "content": "Hello!"},
    {"role": "assistant", "content": "Hi! How can I help?"}
  ]
}
```

### Check Background Run Status

```bash
curl http://localhost:8000/api/v1/agents/my-agent/sessions/my-session/status
```

```json
{"status": "running"}
```

Returns `"running"` if a background task is actively processing this session, `"idle"` otherwise. The UI uses this to show a "working in the background" indicator and poll for completion.

## Introspection

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/{name}/tools` | List tools available to this agent with provider info |
| `GET` | `/{name}/memory` | Introspect memory stores (namespaces + contents) |
| `GET` | `/tool-catalog` | List all primitives and their available tools |

### List Agent Tools

```bash
curl http://localhost:8000/api/v1/agents/my-agent/tools
```

```json
{
  "agent_name": "my-agent",
  "tools": [
    {"name": "remember", "description": "Store information...", "primitive": "memory", "provider": "in_memory"},
    {"name": "recall", "description": "Retrieve a memory...", "primitive": "memory", "provider": "in_memory"}
  ]
}
```
