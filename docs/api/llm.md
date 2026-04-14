# LLM API

`/api/v1/llm`

LLM request routing with tool_use support. All endpoints require authentication.

**Backends:** `NoopLLMProvider`, `BedrockConverseProvider`

## Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/completions` | Route an LLM completion request. |
| `GET` | `/models` | List available models. |

## Completions

```bash
curl -X POST http://localhost:8000/api/v1/llm/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "us.anthropic.claude-sonnet-4-20250514-v1:0",
    "messages": [{"role": "user", "content": "What is 2+2?"}]
  }'
```

**Request body:**

| Field | Type | Required | Description |
|---|---|---|---|
| `model` | string | yes | Model ID (e.g., `us.anthropic.claude-sonnet-4-20250514-v1:0`). |
| `messages` | list | yes | Conversation messages (`role` + `content`). |
| `system` | string | no | System prompt. |
| `tools` | list | no | Tool definitions for tool_use. |
| `tool_choice` | object | no | Tool selection strategy. |
| `max_tokens` | int | no | Maximum tokens to generate. |
| `temperature` | float | no | Sampling temperature. |

**Response:**

```json
{
  "content": "2 + 2 = 4",
  "model": "us.anthropic.claude-sonnet-4-20250514-v1:0",
  "stop_reason": "end_turn",
  "tool_calls": [],
  "usage": {"prompt_tokens": 15, "completion_tokens": 8}
}
```

## List Models

```bash
curl http://localhost:8000/api/v1/llm/models
```

Returns a list of available model IDs from the configured backend.
