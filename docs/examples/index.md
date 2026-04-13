# Examples

Pre-built examples showing different agent framework integrations and provider configurations.

## Quickstart

The fastest way to get started. Four examples showing the same agent built with different tools, all using the `configs/quickstart.yaml` config (Bedrock LLM + in-memory storage):

| File | Description |
|------|-------------|
| `quickstart/plain_python.py` | Pure Python with `agentic-primitives-gateway-client` |
| `quickstart/with_langchain.py` | LangChain integration |
| `quickstart/with_strands.py` | Strands Agents integration |
| `quickstart/with_curl.sh` | Shell script using curl |

```bash
# Start the gateway with quickstart config
AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE=configs/quickstart.yaml \
  uvicorn agentic_primitives_gateway.main:app --port 8000

# Run any of the examples
cd examples/quickstart && python plain_python.py
```

## Per-Config Examples

Each primary config has matching Strands and LangChain examples that demonstrate the primitives available in that deployment:

| Directory | Config | Primitives demonstrated |
|---|---|---|
| `agentcore/` | `./run.sh agentcore` | Memory, browser, code interpreter, observability — all via AgentCore |
| `selfhosted/` | `./run.sh selfhosted` | Memory (mem0/Milvus), browser (Selenium), code (Jupyter), observability (Langfuse) |
| `mixed/` | `./run.sh mixed` | Both backends + JWT auth + provider routing |

Each directory contains `with_strands.py` and `with_langchain.py` plus a README with prerequisites.

## E2E Consolidated Examples

These three examples each demonstrate the **full gateway feature set** -- all primitives, JWT auth, Redis stores, Cedar enforcement, declarative agents, and teams. Start here after the per-config examples.

| Directory | Framework | Providers | Description |
|-----------|-----------|-----------|-------------|
| `e2e-agentcore-strands/` | Strands | All AgentCore | Every primitive via AWS-managed backends |
| `e2e-selfhosted-langchain/` | LangChain | mem0, Langfuse, Jupyter, Selenium, MCP Registry | Every primitive via self-hosted backends |
| `e2e-mixed/` | Both | AgentCore + mem0 + Langfuse | Per-primitive provider routing ("best of both worlds") |

Each E2E example includes:
- A server config (`configs/e2e-*.yaml`) with JWT auth, Cedar enforcement, Redis stores, declarative agents, and teams
- Client-side agent script(s) exercising all available primitives with auto-memory
- A README with prerequisites, quick start, and architecture overview

## Additional Examples

| Directory | Framework | Description |
|-----------|-----------|-------------|
| `langchain-mem0-langfuse/` | LangChain | Agent with mem0 memory and Langfuse tracing |
| `langchain-milvus-langfuse/` | LangChain | Agent with direct Milvus + Langfuse |
| `langchain-auto-memory/` | LangChain | Agent with auto-memory hooks |
| `langchain-mcp-tools/` | LangChain | Agent with MCP tool integration |
| `strands-agentcore/` | Strands | Agent backed by AWS Bedrock AgentCore |
| `strands-agentcore-full/` | Strands | Full AgentCore setup (memory, code, browser, identity) |
| `strands-auto-memory/` | Strands | Agent with auto-memory hooks |
| `strands-code-browser/` | Strands | Agent with code interpreter and browser |
| `strands-mixed-providers/` | Strands | Mixed provider configuration |
| `a2a-client/` | httpx | A2A protocol client — discovery, sync/streaming messages, task management |

## Configuration Presets

### Primary Configs (start here)

| Config | Use Case |
|--------|----------|
| `configs/quickstart.yaml` | **Quickstart**: Bedrock LLM + in-memory storage, no external dependencies |
| `configs/agentcore.yaml` | **AgentCore**: Everything on AWS Bedrock AgentCore |
| `configs/selfhosted.yaml` | **Self-hosted**: mem0 + Langfuse + Jupyter + Selenium + Redis |
| `configs/mixed.yaml` | **Mixed**: Both AgentCore and self-hosted providers with JWT auth + Cedar |

### E2E Configs (full feature set)

| Config | Use Case |
|--------|----------|
| `configs/e2e-agentcore-strands.yaml` | All AgentCore + JWT + Redis + Cedar + agents + teams |
| `configs/e2e-selfhosted-langchain.yaml` | Self-hosted stack + JWT + Redis + Cedar + agents + teams |
| `configs/e2e-mixed.yaml` | Mixed providers + JWT + Redis + Cedar + agents + teams |

### Additional Configs

| Config | Use Case |
|--------|----------|
| `configs/local.yaml` | Local development (all noop/in-memory) |
| `configs/kitchen-sink.yaml` | All providers + agents + teams + Cedar enforcement |
| `configs/agentcore-redis.yaml` | AgentCore + Redis stores for multi-replica |
| `configs/local-jwt.yaml` | AgentCore + Redis + JWT authentication |
| `configs/milvus-langfuse.yaml` | mem0+Milvus memory, Langfuse observability |
| `configs/agents-agentcore.yaml` | Declarative agents with AgentCore backends |
| `configs/agents-mem0-langfuse.yaml` | Declarative agents with mem0 + Langfuse |
| `configs/agents-mixed.yaml` | Mixed providers per primitive |

## Quick Example: Declarative Agent with Memory

No framework needed -- just the gateway:

```bash
# Start with kitchen-sink config
AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE=configs/kitchen-sink.yaml \
  uvicorn agentic_primitives_gateway.main:app --port 8000

# Chat with the research assistant
curl -X POST http://localhost:8000/api/v1/agents/research-assistant/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Remember that my favorite language is Python"}'

# Later (even in a new session):
curl -X POST http://localhost:8000/api/v1/agents/research-assistant/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is my favorite language?"}'
# Response: "Your favorite language is Python!"
```

## Quick Example: Agent Team

```bash
# Run the research team
curl -X POST http://localhost:8000/api/v1/teams/research-team/run \
  -H "Content-Type: application/json" \
  -d '{"message": "Research the top 3 Python web frameworks and write a benchmark script"}'
```

The team:
1. Planner decomposes into tasks (research + coding)
2. Researcher and coder work in parallel
3. Replanner evaluates results and may create follow-up tasks
4. Synthesizer combines everything into a final response

## Quick Example: A2A Protocol

Any A2A-compatible agent can discover and interact with APG agents:

```bash
# Discover all agents via the gateway card
curl http://localhost:8000/.well-known/agent.json | jq '.skills[].name'

# Get a specific agent's card
curl http://localhost:8000/a2a/agents/research-assistant/.well-known/agent.json

# Send a message via A2A protocol
curl -X POST http://localhost:8000/a2a/agents/research-assistant/message:send \
  -H "Content-Type: application/json" \
  -d '{
    "message": {
      "message_id": "msg-001",
      "role": "user",
      "parts": [{"text": "What can you help me with?"}]
    }
  }'

# Stream a message (SSE)
curl -N -X POST http://localhost:8000/a2a/agents/research-assistant/message:stream \
  -H "Content-Type: application/json" \
  -d '{
    "message": {
      "message_id": "msg-002",
      "role": "user",
      "parts": [{"text": "Explain quantum computing briefly"}]
    }
  }'
```

See `examples/a2a-client/` for a full Python example with discovery, streaming, and task management.

## Quick Example: Python Client

```python
from agentic_primitives_gateway_client import AgenticPrimitivesGatewayClient

async with AgenticPrimitivesGatewayClient(base_url="http://localhost:8000") as client:
    # Store a memory
    await client.memory.store("my-ns", key="greeting", content="Hello!")

    # Chat with an agent
    response = await client.agents.chat("research-assistant", message="Hello!")
    print(response.response)
```
