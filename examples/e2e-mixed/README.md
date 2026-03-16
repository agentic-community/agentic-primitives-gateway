# E2E Mixed Providers Example

This example demonstrates the gateway's **per-primitive provider routing** -- the "best of both worlds" pattern. Instead of going all-in on a single provider, you mix self-hosted open-source backends with AWS-managed services based on what makes sense for each primitive.

## Why Mixed Providers?

Different primitives have different operational characteristics:

| Primitive | Provider | Why |
|-----------|----------|-----|
| **Memory** | mem0 + Milvus (self-hosted) | Your data stays in your infrastructure. Full control over vector indices, retention policies, and data locality. |
| **Observability** | Langfuse (self-hosted) | Your traces stay in your infrastructure. No sensitive prompts leaving your network. |
| **Code Interpreter** | AgentCore (AWS) | Sandboxed execution is hard to self-host securely. Let AWS manage the isolation. |
| **Browser** | AgentCore (AWS) | Cloud browsers need significant compute and network. AWS handles scaling. |
| **Identity** | AgentCore (AWS) | Credential exchange and OAuth2 flows managed by AWS IAM. |
| **Tools** | AgentCore (AWS) | MCP gateway with managed tool discovery and invocation. |
| **Gateway** | Bedrock (AWS) | LLM routing through Bedrock's unified API. |

The gateway routes each primitive independently -- a single request can hit mem0 for memory, Langfuse for tracing, and AgentCore for code execution, all transparently.

## Two Agent Scripts

This example includes both a **Strands** and **LangChain** agent to show framework-agnostic usage:

- `agent_strands.py` -- Synchronous Strands agent with streaming
- `agent_langchain.py` -- Async LangChain agent with `astream_events`

Both scripts use identical provider routing and offer the same capabilities.

## Prerequisites

### Infrastructure

```bash
# Milvus (vector database for mem0 memory)
docker run -d --name milvus -p 19530:19530 milvusdb/milvus:latest

# Redis (for agent/team stores and task boards)
docker run -d --name redis -p 6379:6379 redis:latest

# Optional: Langfuse (self-hosted observability)
# See https://langfuse.com/docs/deployment/self-host
# Or use Langfuse Cloud: https://cloud.langfuse.com
```

### Credentials

```bash
# AWS credentials (for AgentCore + Bedrock)
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_REGION=us-east-1

# Langfuse (self-hosted or cloud)
export LANGFUSE_PUBLIC_KEY=pk-lf-...
export LANGFUSE_SECRET_KEY=sk-lf-...
export LANGFUSE_BASE_URL=http://localhost:3000  # or https://cloud.langfuse.com

# Optional: JWT authentication (Keycloak or any OIDC provider)
export JWT_ISSUER=https://your-keycloak/realms/your-realm
export JWT_TOKEN=<your-jwt-token>  # for the client scripts
```

### Python Dependencies

```bash
pip install -r requirements.txt
```

## Quick Start

### 1. Start the Gateway Server

```bash
# From the repo root
pip install -e ".[mem0,langfuse,agentcore,redis,jwt,cedar]"

LANGFUSE_PUBLIC_KEY=pk-lf-... \
LANGFUSE_SECRET_KEY=sk-lf-... \
AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE=configs/e2e-mixed.yaml \
  uvicorn agentic_primitives_gateway.main:app --reload
```

### 2. Run a Client Agent

```bash
cd examples/e2e-mixed

# Strands agent
python agent_strands.py

# Or LangChain agent
python agent_langchain.py
```

### 3. Use Declarative Agents (UI or API)

The config also seeds 8 declarative agents and a team that you can use without any client code:

```bash
# Chat with the research-assistant via API
curl -X POST localhost:8000/api/v1/agents/research-assistant/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <jwt-token>" \
  -d '{"message": "What is the capital of France? Remember it for later."}'

# Use the coordinator (delegates to researcher + coder)
curl -X POST localhost:8000/api/v1/agents/coordinator/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <jwt-token>" \
  -d '{"message": "Research Python web frameworks and write a comparison script"}'

# Run the research-team
curl -X POST localhost:8000/api/v1/teams/research-team/run \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <jwt-token>" \
  -d '{"message": "Analyze the pros and cons of microservices vs monoliths"}'

# Or use the Web UI at http://localhost:8000/ui/
```

## Architecture

```
Client (Strands / LangChain / curl / UI)
  |
  |  X-Provider-Memory: mem0
  |  X-Provider-Observability: langfuse
  |  X-Provider-Code-Interpreter: agentcore
  |  X-Provider-Browser: agentcore
  |  Authorization: Bearer <jwt>
  |
  v
+------------------------------------------+
|          Agentic Primitives Gateway       |
|                                          |
|  JWT Auth -> Cedar Enforcement -> Routes |
|                                          |
|  +----------+  +----------+  +--------+  |
|  | mem0     |  | Langfuse |  | Agent  |  |
|  | (Milvus) |  | (traces) |  | Core   |  |
|  +----+-----+  +----+-----+  +---+----+  |
|       |              |            |       |
+------------------------------------------+
        |              |            |
        v              v            v
   Your Milvus    Your Langfuse   AWS AgentCore
   (self-hosted)  (self-hosted)   (managed)
```

## Declarative Agents

The config seeds these agents (available via API and UI):

| Agent | Description | Primitives |
|-------|-------------|------------|
| `research-assistant` | Full-stack assistant | memory, code, browser, tools, identity |
| `researcher` | Focused researcher | memory, browser |
| `coder` | Coding assistant | memory, code |
| `data-analyst` | Data analysis | memory, code |
| `coordinator` | Delegates to researcher + coder | memory, agents |
| `meta-agent` | Creates agents dynamically | memory, agent_management |
| `planner` | Task decomposition for teams | (none) |
| `synthesizer` | Result synthesis for teams | (none) |

**Team:** `research-team` (planner + researcher + coder + synthesizer)
