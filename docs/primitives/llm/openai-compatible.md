# OpenAI Compatible

Generic LLM provider for any server implementing the OpenAI `/v1/chat/completions` endpoint. Works with OpenAI, LM Studio, Ollama, vLLM, TGI, and more. No extra dependencies beyond httpx.

## Configuration

### Server-Side (Gateway Config)

=== "OpenAI"

    ```yaml
    providers:
      llm:
        backend: "agentic_primitives_gateway.primitives.llm.openai_compatible.OpenAICompatibleProvider"
        config:
          base_url: "https://api.openai.com"
          default_model: "gpt-4o"
          api_key: "${OPENAI_API_KEY}"
    ```

=== "LM Studio"

    ```yaml
    providers:
      llm:
        backend: "agentic_primitives_gateway.primitives.llm.openai_compatible.OpenAICompatibleProvider"
        config:
          base_url: "http://localhost:1234"
    ```

=== "Ollama"

    ```yaml
    providers:
      llm:
        backend: "agentic_primitives_gateway.primitives.llm.openai_compatible.OpenAICompatibleProvider"
        config:
          base_url: "http://localhost:11434"
          default_model: "llama3"
    ```

=== "vLLM"

    ```yaml
    providers:
      llm:
        backend: "agentic_primitives_gateway.primitives.llm.openai_compatible.OpenAICompatibleProvider"
        config:
          base_url: "http://vllm-server:8000"
          default_model: "meta-llama/Llama-3-8b-chat-hf"
    ```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `base_url` | `https://api.openai.com` | Base URL of the OpenAI-compatible server |
| `default_model` | `""` | Model ID used when the client doesn't specify one |
| `api_key` | `""` | Bearer token for authentication (optional for local servers) |

### Environment Variables

| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | API key for OpenAI (use with env var expansion: `"${OPENAI_API_KEY}"`) |

## Using the LLM API

All standard LLM endpoints work identically regardless of the backend:

### Non-Streaming

```bash
curl -X POST http://localhost:8000/api/v1/llm/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### Streaming

```bash
curl -X POST http://localhost:8000/api/v1/llm/completions/stream \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "Tell me a story"}]
  }'
```

### List Models

```bash
curl http://localhost:8000/api/v1/llm/models
```

Queries the backend's `/v1/models` endpoint and returns available model IDs.

## Using with the Python Client

### LLM Gateway Model

```python
from agentic_primitives_gateway_client import AgenticPlatformClient

client = AgenticPlatformClient("http://localhost:8000")

# Uses whatever LLM backend is configured on the gateway
model = client.get_model(format="strands")

# Or with a specific model override
model = client.get_model(format="strands", model="gpt-4o")

# LangChain
model = client.get_model(format="langchain")
```

### Direct Completions

```python
result = await client.completions({
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello"}],
})
```

## Using with Declarative Agents

```yaml
agents:
  specs:
    local-agent:
      model: "llama3"
      system_prompt: "You are a helpful assistant."
      primitives:
        memory:
          enabled: true
      provider_overrides:
        llm: "local"
```

## Running Multiple Backends

You can register multiple OpenAI-compatible servers simultaneously:

```yaml
providers:
  llm:
    default: "openai"
    backends:
      openai:
        backend: "agentic_primitives_gateway.primitives.llm.openai_compatible.OpenAICompatibleProvider"
        config:
          base_url: "https://api.openai.com"
          default_model: "gpt-4o"
          api_key: "${OPENAI_API_KEY}"
      local:
        backend: "agentic_primitives_gateway.primitives.llm.openai_compatible.OpenAICompatibleProvider"
        config:
          base_url: "http://localhost:1234"
```

Agents or clients select the backend via `provider_overrides` or the `X-Provider-LLM` header.

## How It Works

1. **Request translation**: gateway messages (`role` + `content` + `tool_calls` + `tool_results`) are converted to OpenAI chat format
2. **Streaming**: sends `stream: true`, reads SSE events from the response, translates OpenAI delta chunks to gateway event format
3. **Tool use**: gateway tool definitions are converted to OpenAI function-calling format; tool call results are converted back
4. **[DONE] handling**: the provider correctly handles the `data: [DONE]` sentinel that OpenAI-compatible APIs send at the end of a stream

The provider uses a sync httpx client in a background thread for streaming, bridged to async via `asyncio.Queue`, the same pattern used by the Bedrock provider.

## Backend Comparison

| Feature | OpenAI Compatible | Bedrock Converse |
|---------|-------------------|------------------|
| Streaming | SSE (`data: [DONE]`) | Bedrock `converse_stream()` |
| Tool use | OpenAI function calling | Bedrock `toolConfig` |
| Auth | Bearer token | AWS SigV4 |
| Dependencies | none (httpx is core) | `boto3` |
| Works with | OpenAI, LM Studio, Ollama, vLLM, TGI | AWS Bedrock models |

## Prerequisites

- An OpenAI-compatible server running and accessible from the gateway
- API key (if the server requires authentication)
- No additional Python packages required
