# Examples

Pre-built examples showing different agent framework integrations and provider configurations.

## Framework Examples

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

## Configuration Presets

| Config | Use Case |
|--------|----------|
| `configs/local.yaml` | Local development (all noop/in-memory) |
| `configs/kitchen-sink.yaml` | All providers + agents + teams + Cedar enforcement |
| `configs/agentcore.yaml` | Everything on AWS Bedrock AgentCore |
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
