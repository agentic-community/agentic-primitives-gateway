# E2E Self-Hosted LangChain Example

The definitive example for running the Agentic Primitives Gateway with all
open-source / self-hosted providers. A LangChain agent exercises every
available primitive through the gateway.

## What This Demonstrates

| Feature | Backend | Self-hosted? |
|---|---|---|
| Semantic memory | mem0 + Milvus | Yes |
| Observability | Langfuse | Yes |
| Code execution | Jupyter | Yes |
| Browser automation | Selenium Grid (Chrome) | Yes |
| Tool registry | MCP Registry | Yes |
| JWT authentication | Keycloak (any OIDC) | Yes |
| Policy enforcement | Cedar (local eval) | Yes |
| Agent/team/task stores | Redis | Yes |
| LLM inference | AWS Bedrock | No (only cloud dependency) |

Additionally, the server config includes **declarative agents** (research-assistant,
researcher, coder, coordinator) and a **research-team** -- usable via the web UI
at `http://localhost:8000/ui/` or the REST API without any Python code.

## Architecture

```
Client Script (LangChain + gateway-client)
    |
    | HTTP + JWT Bearer token
    v
+-------------------------------------------+
| Agentic Primitives Gateway                |
| (FastAPI)                                 |
|                                           |
|  Auth: JWT (Keycloak / any OIDC)          |
|  Enforcement: Cedar (local evaluation)    |
|                                           |
|  Providers:                               |
|    memory        -> mem0 + Milvus         |
|    observability -> Langfuse              |
|    code_interp   -> Jupyter               |
|    browser       -> Selenium Grid         |
|    tools         -> MCP Registry          |
|    gateway       -> AWS Bedrock (LLM)     |
|    tasks         -> Redis                 |
|                                           |
|  Stores (agents, teams): Redis            |
+-------------------------------------------+
    |         |         |         |
    v         v         v         v
  Milvus  Langfuse  Jupyter  Selenium  MCP Registry
 :19530    :3000    :8888     :4444      :8080
                                |
                              Redis
                              :6379
```

## Prerequisites

**Required:**
- Python 3.11+
- AWS credentials with Bedrock access (for LLM + embeddings)
- Docker (for self-hosted services)

**Self-hosted services (all via Docker):**

```bash
# Milvus -- vector database for semantic memory
docker run -d --name milvus \
  -p 19530:19530 \
  milvusdb/milvus:latest

# Selenium Grid -- self-hosted Chrome browser
docker run -d --name selenium \
  -p 4444:4444 -p 7900:7900 \
  --shm-size="2g" \
  selenium/standalone-chrome:latest

# Jupyter -- Python code execution kernel
docker run -d --name jupyter \
  -p 8888:8888 \
  -e JUPYTER_TOKEN="" \
  jupyter/base-notebook:latest

# Redis -- stores for agents, teams, tasks, and event persistence
docker run -d --name redis \
  -p 6379:6379 \
  redis:latest

# MCP Registry -- self-hosted tool registry (optional)
# See your MCP Registry documentation for setup
# Default: http://localhost:8080

# Langfuse (optional -- use cloud.langfuse.com instead)
# See https://langfuse.com/docs/deployment/self-host for full setup
```

**Optional:**
- Keycloak (or any OIDC provider) for JWT authentication. Without it, the
  gateway runs in noop auth mode (all requests get admin access).

## Quick Start

### 1. Install dependencies

```bash
# Server (from repo root)
pip install -e ".[mem0,langfuse,jupyter,redis,jwt,cedar]"

# Client (this example)
cd examples/e2e-selfhosted-langchain
pip install -r requirements.txt
```

### 2. Start the gateway

```bash
# Minimal (noop auth, Langfuse cloud)
LANGFUSE_PUBLIC_KEY=pk-lf-... \
LANGFUSE_SECRET_KEY=sk-lf-... \
./run.sh e2e-selfhosted-langchain

# With JWT auth (Keycloak)
JWT_ISSUER=https://keycloak.example.com/realms/my-realm \
JWT_CLIENT_ID=agentic-gateway \
LANGFUSE_PUBLIC_KEY=pk-lf-... \
LANGFUSE_SECRET_KEY=sk-lf-... \
./run.sh e2e-selfhosted-langchain
```

### 3. Run the agent

```bash
cd examples/e2e-selfhosted-langchain

# Without JWT
export LANGFUSE_PUBLIC_KEY=pk-lf-...
export LANGFUSE_SECRET_KEY=sk-lf-...
python agent.py

# With JWT (obtain token from your OIDC provider)
export JWT_TOKEN=eyJhbGciOi...
python agent.py
```

### 4. Use the web UI (declarative agents)

Open `http://localhost:8000/ui/` in your browser. The config seeds six agents
and one team -- no Python code needed:

- **research-assistant** -- memory + browser
- **researcher** -- memory + browser (used by coordinator and team)
- **coder** -- memory + Jupyter (used by coordinator and team)
- **coordinator** -- delegates to researcher + coder
- **research-team** -- planner decomposes, researcher + coder execute, synthesizer combines

## What to Try

**Memory (mem0 + Milvus):**
```
You: Remember that the project deadline is March 30th
You: What do you know about deadlines?
You: Search your memory for anything about the project
```

**Code execution (Jupyter):**
```
You: Write a Python function to calculate fibonacci numbers and test it
You: Import pandas, create a sample dataframe, and describe it
```

**Browser (Selenium Grid):**
```
You: Open the browser and go to https://example.com, then read the page
You: Navigate to https://news.ycombinator.com and get the top 3 headlines
```

**Observability (Langfuse):**
```
You: Show me the recent traces
You: Check which providers are active on the gateway
```

**Multi-turn with auto-memory:**
```
You: My name is Alice and I work on the infrastructure team
You: What's my name and team?  (agent recalls from auto-memory)
```

**Declarative agents (via UI or curl):**
```bash
# Chat with the coordinator (delegates to researcher + coder)
curl -X POST localhost:8000/api/v1/agents/coordinator/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Research Python async patterns and write an example"}'

# Run the research team
curl -X POST localhost:8000/api/v1/teams/research-team/run \
  -H "Content-Type: application/json" \
  -d '{"message": "Compare sorting algorithms with benchmarks"}'
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `GATEWAY_URL` | `http://localhost:8000` | Gateway base URL |
| `JWT_TOKEN` | (none) | Bearer token for JWT auth |
| `JWT_ISSUER` | (none) | OIDC issuer URL (server-side) |
| `JWT_CLIENT_ID` | `agentic-gateway` | OIDC client ID (server-side) |
| `LANGFUSE_PUBLIC_KEY` | (none) | Langfuse public key |
| `LANGFUSE_SECRET_KEY` | (none) | Langfuse secret key |
| `LANGFUSE_BASE_URL` | `https://cloud.langfuse.com` | Langfuse URL |
| `AWS_REGION` | `us-east-1` | AWS region for Bedrock |
| `MILVUS_HOST` | `localhost` | Milvus host |
| `MILVUS_PORT` | `19530` | Milvus port |
| `SELENIUM_HUB_URL` | `http://localhost:4444` | Selenium Grid hub URL |
| `JUPYTER_URL` | `http://localhost:8888` | Jupyter server URL |
| `JUPYTER_TOKEN` | (none) | Jupyter auth token |
| `MCP_REGISTRY_URL` | `http://localhost:8080` | MCP Registry URL |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis URL |
