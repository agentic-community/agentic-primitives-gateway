# Quickstart

Get the gateway running and chat with an agent in under 2 minutes.

## Prerequisites

- Python 3.11+
- AWS credentials configured (`aws configure` or environment variables)
- Access to Amazon Bedrock models in your AWS region

## Install & Run

```bash
git clone <repo-url>
cd agentic-primitives-gateway
pip install -e .
./run.sh
```

The gateway starts at `http://localhost:8000` with Bedrock for LLM and in-memory storage.

## Declarative Agent

The quickstart config defines an **assistant agent** in YAML — no Python code needed:

```yaml
# In configs/quickstart.yaml
agents:
  specs:
    assistant:
      model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
      system_prompt: "You are a helpful assistant with long-term memory..."
      primitives:
        memory:
          enabled: true
      max_turns: 20
```

The gateway runs the full LLM tool-call loop server-side: the agent receives your message, decides whether to use memory tools (remember, recall, search), executes them, and returns the response.

## Chat with the Agent

```bash
curl -X POST http://localhost:8000/api/v1/agents/assistant/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello! Remember that my favorite color is blue."}'
```

```bash
curl -X POST http://localhost:8000/api/v1/agents/assistant/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is my favorite color?"}'
```

## Use from Any Framework

The gateway is a REST API — use it from any language or framework.

### curl

```bash
# Call the LLM directly
curl -X POST http://localhost:8000/api/v1/llm/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "us.anthropic.claude-sonnet-4-20250514-v1:0",
       "messages": [{"role": "user", "content": "What is 2+2?"}]}'

# Store a memory
curl -X POST http://localhost:8000/api/v1/memory/my-namespace \
  -H "Content-Type: application/json" \
  -d '{"key": "fact", "content": "The sky is blue."}'

# Search memory
curl -X POST http://localhost:8000/api/v1/memory/my-namespace/search \
  -H "Content-Type: application/json" \
  -d '{"query": "what color is the sky"}'
```

### Python (no framework)

```python
from agentic_primitives_gateway_client import AgenticPlatformClient, Memory

client = AgenticPlatformClient("http://localhost:8000", aws_from_environment=True)
memory = Memory(client, namespace="agent:my-agent")

await memory.remember("api-limit", "100 requests per minute")
results = await memory.search("rate limiting")
```

### LangChain

```python
from agentic_primitives_gateway_client import AgenticPlatformClient, Memory
from langchain_core.tools import tool

client = AgenticPlatformClient("http://localhost:8000", aws_from_environment=True)
memory = Memory(client, namespace="agent:my-agent")

@tool
async def remember(key: str, content: str) -> str:
    """Store information in long-term memory."""
    return await memory.remember(key, content)

# Pass to any LangChain agent
```

### Strands

```python
from agentic_primitives_gateway_client import AgenticPlatformClient
from strands import Agent

client = AgenticPlatformClient("http://localhost:8000", aws_from_environment=True)
tools = client.get_tools_sync(["memory"], namespace="agent:my-agent")
agent = Agent(model="us.anthropic.claude-sonnet-4-20250514-v1:0", tools=tools)
```

See `examples/quickstart/` for complete runnable examples.

## Open the Web UI

Build and open the web UI for a visual dashboard, agent chat, and team management:

```bash
cd ui && npm install && npm run build && cd ..
```

Visit `http://localhost:8000/ui/` — you'll see the Dashboard with health status and the assistant agent ready to chat.

For UI development with hot reload:

```bash
cd ui && npm run dev
# Opens at http://localhost:5173/ui/
```

## Verify Health

```bash
curl http://localhost:8000/healthz
# {"status":"ok"}

curl http://localhost:8000/api/v1/providers
# {"memory":{"default":"in_memory","available":["in_memory"]}, "llm":{"default":"bedrock", ...}}
```

## Configurations

The gateway ships with four configurations for different stages:

| Config | Command | What it does |
|---|---|---|
| **quickstart** | `./run.sh` | Bedrock LLM + in-memory. No infra needed. |
| **agentcore** | `./run.sh agentcore` | All AWS managed (AgentCore + Bedrock). Needs Redis. |
| **selfhosted** | `./run.sh selfhosted` | Open-source backends (Milvus, Langfuse, Jupyter, Selenium). Needs Redis. |
| **mixed** | `./run.sh mixed` | Both AgentCore + self-hosted. JWT auth + Cedar + credentials. |

See [Configuration Guide](configuration.md) for details on each config and environment variables.

## Next Steps

- [Configuration Guide](configuration.md) — YAML config, environment variables, provider routing
- [Architecture](../concepts/architecture.md) — understand how it all fits together
- [Agents](../concepts/agents.md) — declarative agents with tool calling
- [Primitives](../concepts/primitives.md) — memory, browser, code execution, and more
