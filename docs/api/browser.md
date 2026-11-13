# Browser API

`/api/v1/browser`

Cloud-based browser automation. All endpoints require authentication. Session operations are scoped to the session owner.

**Backends:** `NoopBrowserProvider`, [`AgentCoreBrowserProvider`](../primitives/browser/agentcore.md), [`SeleniumGridBrowserProvider`](../primitives/browser/selenium-grid.md)

## Sessions

| Method | Path | Description |
|---|---|---|
| `POST` | `/sessions` | Start a browser session. Returns 201. |
| `GET` | `/sessions` | List sessions (filtered to owned sessions for non-admins). |
| `GET` | `/sessions/{session_id}` | Get session info. Returns 404 if not found. With AgentCore, a fresh remote `READY` check must authorize the current request's AWS identity before the cached page URL is returned. Revoked access is refused; missing caller credentials are refused under `allow_server_credentials: never`. |
| `DELETE` | `/sessions/{session_id}` | Stop a session. Returns 204. |
| `GET` | `/sessions/{session_id}/live-view` | Get a live view URL. Query param: `expires` (1-300, default 300 — the AgentCore presigned-URL maximum). |

### Start session

```bash
curl -X POST http://localhost:8000/api/v1/browser/sessions \
  -H "Content-Type: application/json" \
  -d '{"config": {}, "viewport": {"width": 1280, "height": 720}}'
```

**Request body:**

| Field | Type | Default | Description |
|---|---|---|---|
| `session_id` | string | auto-generated | Optional custom session ID. Already-claimed or previously stopped IDs return `409 Conflict`; session IDs are never reused, so in-flight requests cannot reach a replacement session. |
| `config` | object | `{}` | Provider-specific session config. |
| `viewport` | object | none | Optional viewport size (`width`, `height`). |

## Interaction

All interaction endpoints return 400 if the session is not found or the operation fails.

| Method | Path | Description |
|---|---|---|
| `POST` | `/sessions/{session_id}/navigate` | Navigate to a URL. Body: `{"url": "..."}`. |
| `GET` | `/sessions/{session_id}/screenshot` | Take a screenshot. Returns `{"format": "png", "data": "<base64>"}`. |
| `GET` | `/sessions/{session_id}/content` | Get current page content. Returns `{"content": "<html>"}`. |
| `POST` | `/sessions/{session_id}/click` | Click an element. Body: `{"selector": "..."}`. |
| `POST` | `/sessions/{session_id}/type` | Type text. Body: `{"selector": "...", "text": "..."}`. |
| `POST` | `/sessions/{session_id}/evaluate` | Evaluate JavaScript. Body: `{"expression": "..."}`. Returns `{"result": ...}`. |

### Example: navigate and read

```bash
# Navigate
curl -X POST http://localhost:8000/api/v1/browser/sessions/s1/navigate \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'

# Read page content
curl http://localhost:8000/api/v1/browser/sessions/s1/content

# Take screenshot
curl http://localhost:8000/api/v1/browser/sessions/s1/screenshot
```
