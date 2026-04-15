# Code Interpreter API

`/api/v1/code-interpreter`

Sandboxed code execution with persistent sessions. All endpoints require authentication. Session operations are scoped to the session owner.

**Backends:** `NoopCodeInterpreterProvider`, [`AgentCoreCodeInterpreterProvider`](../primitives/code-interpreter/agentcore.md), [`JupyterCodeInterpreterProvider`](../primitives/code-interpreter/jupyter.md)

## Sessions

| Method | Path | Description |
|---|---|---|
| `POST` | `/sessions` | Start an execution session. Returns 201. |
| `GET` | `/sessions` | List sessions (filtered to owned sessions for non-admins). |
| `GET` | `/sessions/{session_id}` | Get session details. Returns 404/501. |
| `DELETE` | `/sessions/{session_id}` | Stop a session. Returns 204. |

### Start session

```bash
curl -X POST http://localhost:8000/api/v1/code-interpreter/sessions \
  -H "Content-Type: application/json" \
  -d '{"language": "python"}'
```

**Request body:**

| Field | Type | Default | Description |
|---|---|---|---|
| `session_id` | string | auto-generated | Optional custom session ID. |
| `language` | string | `"python"` | Language: `python`, `javascript`, `typescript`, `ruby`, `java`, `bash`. |
| `config` | object | `{}` | Provider-specific config. |

## Code Execution

| Method | Path | Description |
|---|---|---|
| `POST` | `/sessions/{session_id}/execute` | Execute code. Returns execution result. |
| `GET` | `/sessions/{session_id}/history` | Get execution history. Query param: `limit` (1-500, default 50). Returns 501 if not supported. |

### Execute code

```bash
curl -X POST http://localhost:8000/api/v1/code-interpreter/sessions/s1/execute \
  -H "Content-Type: application/json" \
  -d '{"code": "print(2 + 2)", "language": "python"}'
```

**Response:**

```json
{
  "output": "4\n",
  "error": "",
  "status": "success",
  "execution_time": 0.05
}
```

## File Management

| Method | Path | Description |
|---|---|---|
| `POST` | `/sessions/{session_id}/files` | Upload a file (multipart form). |
| `GET` | `/sessions/{session_id}/files/{filename}` | Download a file (binary). |

```bash
# Upload
curl -X POST http://localhost:8000/api/v1/code-interpreter/sessions/s1/files \
  -F "file=@data.csv"

# Download
curl http://localhost:8000/api/v1/code-interpreter/sessions/s1/files/output.png -o output.png
```
