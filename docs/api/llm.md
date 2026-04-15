# LLM API

`/api/v1/llm`

LLM request routing with tool_use support. All endpoints require authentication.

**Backends:** `NoopLLMProvider`, [`BedrockConverseProvider`](../primitives/llm/bedrock.md), [`OpenAICompatibleProvider`](../primitives/llm/openai-compatible.md)

## Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/completions` | Route an LLM completion request. |
| `POST` | `/completions/stream` | Stream an LLM completion via SSE. |
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
| `model` | string | no | Model ID. Defaults to the provider's configured `default_model` if omitted. |
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

## Streaming Completions

```bash
curl -X POST http://localhost:8000/api/v1/llm/completions/stream \
  -H "Content-Type: application/json" \
  -d '{
    "model": "us.anthropic.claude-sonnet-4-20250514-v1:0",
    "messages": [{"role": "user", "content": "What is 2+2?"}]
  }'
```

Returns an SSE stream (`text/event-stream`) with the following event types:

| Event type | Fields | Description |
|---|---|---|
| `content_delta` | `delta` | Text token fragment |
| `tool_use_start` | `id`, `name` | Start of a tool call |
| `tool_use_delta` | `id`, `delta` | Incremental tool call arguments |
| `tool_use_complete` | `id`, `name`, `input` | Completed tool call with parsed arguments |
| `message_stop` | `stop_reason`, `model` | End of response |
| `metadata` | `usage` | Token usage (`input_tokens`, `output_tokens`) |

**Example SSE stream:**

```
data: {"type": "content_delta", "delta": "2 + 2"}
data: {"type": "content_delta", "delta": " = 4"}
data: {"type": "message_stop", "stop_reason": "end_turn", "model": "us.anthropic.claude-sonnet-4-20250514-v1:0"}
data: {"type": "metadata", "usage": {"input_tokens": 15, "output_tokens": 8}}
```

The request body is the same as the non-streaming `/completions` endpoint.

### Client Usage

```python
# Strands
model = client.get_model(format="strands")

# LangChain
model = client.get_model(format="langchain")
```

Both adapters route inference through this streaming endpoint. See the [OpenAI Compatible](../primitives/llm/openai-compatible.md) and [Bedrock](../primitives/llm/bedrock.md) provider docs for configuration.

## List Models

```bash
curl http://localhost:8000/api/v1/llm/models
```

Returns a list of available model IDs from the configured backend.
