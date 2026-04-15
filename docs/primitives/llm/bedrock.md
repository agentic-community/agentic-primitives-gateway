# Bedrock Converse

AWS Bedrock LLM provider using the [Converse API](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html). Supports streaming, tool use, and all Bedrock-hosted models.

## Configuration

### Server-Side (Gateway Config)

```yaml
providers:
  llm:
    backend: "agentic_primitives_gateway.primitives.llm.bedrock.BedrockConverseProvider"
    config:
      region: "us-east-1"
      default_model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `region` | `us-east-1` | AWS region for Bedrock |
| `default_model` | `us.anthropic.claude-sonnet-4-20250514-v1:0` | Model used when the client doesn't specify one |

### Environment Variables

| Variable | Description |
|----------|-------------|
| `AWS_REGION` | AWS region (fallback if not in config) |
| `AWS_ACCESS_KEY_ID` | AWS access key |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key |
| `AWS_SESSION_TOKEN` | Session token (for temporary credentials) |

## Using the LLM API

### Non-Streaming

```bash
curl -X POST http://localhost:8000/api/v1/llm/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "us.anthropic.claude-sonnet-4-20250514-v1:0",
    "messages": [{"role": "user", "content": "What is 2+2?"}],
    "temperature": 0.7
  }'
```

### Streaming

```bash
curl -X POST http://localhost:8000/api/v1/llm/completions/stream \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "Explain quantum computing"}]
  }'
```

Returns SSE events: `content_delta`, `tool_use_start`, `tool_use_delta`, `tool_use_complete`, `message_stop`, `metadata`.

### With Tool Use

```bash
curl -X POST http://localhost:8000/api/v1/llm/completions \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "What is the weather in Seattle?"}],
    "tools": [
      {
        "name": "get_weather",
        "description": "Get current weather for a city",
        "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}}
      }
    ],
    "tool_choice": "auto"
  }'
```

## Using with the Python Client

### LLM Gateway Model (Strands)

```python
from agentic_primitives_gateway_client import AgenticPlatformClient

client = AgenticPlatformClient("http://localhost:8000", aws_from_environment=True)

# Model routed through the gateway (uses Bedrock behind the scenes)
model = client.get_model(format="strands")
```

### LLM Gateway Model (LangChain)

```python
model = client.get_model(format="langchain")
```

### Direct Completions

```python
result = await client.completions({
    "model": "us.anthropic.claude-sonnet-4-20250514-v1:0",
    "messages": [{"role": "user", "content": "Hello"}],
})
```

## How It Works

The provider translates between the gateway's internal message format and the Bedrock Converse API:

1. **Messages**: gateway flat format (`role` + `content` + `tool_calls`) is converted to Bedrock content blocks (`text`, `toolUse`, `toolResult`)
2. **System prompts**: extracted from messages and passed as the top-level `system` parameter
3. **Streaming**: uses `converse_stream()` with a background thread draining boto3's sync iterator into an `asyncio.Queue` for async iteration
4. **Tool calls**: tool definitions are converted to Bedrock `toolConfig` format; tool results are converted back

Per-request AWS credentials are resolved from headers (`X-AWS-Access-Key-Id`, etc.) or the environment, ensuring each request uses the caller's permissions.

## Running Multiple Regions

```yaml
providers:
  llm:
    default: "bedrock-us"
    backends:
      bedrock-us:
        backend: "agentic_primitives_gateway.primitives.llm.bedrock.BedrockConverseProvider"
        config:
          region: "us-east-1"
          default_model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
      bedrock-eu:
        backend: "agentic_primitives_gateway.primitives.llm.bedrock.BedrockConverseProvider"
        config:
          region: "eu-west-1"
          default_model: "eu.anthropic.claude-sonnet-4-20250514-v1:0"
```

## Prerequisites

- `pip install agentic-primitives-gateway[agentcore]`
- AWS credentials with Bedrock model access
- Model access enabled in the [Bedrock console](https://console.aws.amazon.com/bedrock/home#/modelaccess) for the target region
