# E2E AgentCore + Strands Example

The definitive example for running the Agentic Primitives Gateway with all AWS-managed providers. Includes a Strands client agent that exercises every primitive, plus server-side declarative agents and teams.

## What This Demonstrates

| Feature | Details |
|---|---|
| **All 7 Primitives** | Memory, Identity, Code Interpreter, Browser, Observability, Tools (MCP), Gateway (Bedrock) |
| **JWT Authentication** | Keycloak OIDC (optional -- falls back to noop/admin if not configured) |
| **Cedar Enforcement** | Server-side policy evaluation with seed policies (permit-all baseline) |
| **Redis Stores** | Agent specs, team specs, and task boards persisted in Redis |
| **Auto-Memory** | Transparent per-turn storage and context recall in the client script |
| **Declarative Agents** | research-assistant, researcher, coder, coordinator, meta-agent |
| **Agent Delegation** | coordinator delegates to researcher + coder via agent-as-tool |
| **Meta-Agent** | Creates ephemeral agents on the fly with agent_management tools |
| **Team Orchestration** | research-team: planner decomposes, workers execute, synthesizer merges |

## Architecture

```
                        Client Script (agent.py)
                              |
                              | httpx + JWT Bearer token
                              v
                   +---------------------+
                   |   Gateway Server    |
                   |  (FastAPI + Cedar)  |
                   |                     |
                   |  JWT Auth Middleware |
                   |  Cedar Enforcement  |
                   +---------------------+
                              |
            +-----------------+-----------------+
            |                 |                 |
            v                 v                 v
    +---------------+  +-----------+  +------------------+
    |  AgentCore    |  |  Bedrock  |  |     Redis        |
    |  (Memory,     |  |  Converse |  |  (Agent/Team     |
    |   Identity,   |  |  (LLM    |  |   specs, Tasks,  |
    |   Code, Obs,  |  |   calls) |  |   Event store)   |
    |   Browser,    |  +-----------+  +------------------+
    |   Tools,      |
    |   Policy,     |
    |   Evals)      |
    +---------------+
```

## Prerequisites

1. **AWS Credentials** -- any standard method: env vars, `~/.aws/credentials`, IRSA, Pod Identity
2. **Redis** -- running at `localhost:6379` (or set `REDIS_URL`)
3. **Python 3.14+**
4. **AgentCore Memory Resource** -- create one in the AgentCore console and set `AGENTCORE_MEMORY_ID`

Optional:
- **Keycloak** (or any OIDC provider) for JWT authentication

## Quick Start

### 1. Install server dependencies

```bash
cd /path/to/agentic-primitives-gateway
pip install -e ".[agentcore,redis,jwt]"
```

### 2. Start Redis

```bash
# Docker
docker run -d --name redis -p 6379:6379 redis:7-alpine

# Or brew
brew services start redis
```

### 3. Start the gateway server

```bash
# Set your AgentCore memory resource ID
export AGENTCORE_MEMORY_ID=memory_xxxxx

# Start the server
./run.sh e2e-agentcore-strands
```

### 4. Run the client agent

```bash
cd examples/e2e-agentcore-strands
pip install -r requirements.txt

# Without JWT auth (requires noop auth on server, or comment out auth block in config):
python agent.py

# With JWT auth:
export KEYCLOAK_ISSUER=https://your-keycloak/realms/your-realm
export KEYCLOAK_USERNAME=your-user
export KEYCLOAK_PASSWORD=your-password
python agent.py
```

## What to Try

### Client Script (agent.py)

The interactive agent has access to all primitives. Try:

```
You: Remember that the project deadline is March 31st
You: What do you remember about deadlines?
You: Run some Python code to calculate 2**100
You: Open a browser and go to https://example.com
You: What providers are available?
You: List all available tools in the MCP gateway
You: What models are available through the gateway?
```

### Declarative Agents (via UI or API)

The server config seeds agents that run server-side. Open the UI at `http://localhost:8000/ui/` or use curl:

```bash
# List available agents
curl -s localhost:8000/api/v1/agents | jq '.[] | .name'

# Chat with the research assistant
curl -s localhost:8000/api/v1/agents/research-assistant/run \
  -H "Content-Type: application/json" \
  -d '{"message": "What is AgentCore?"}' | jq .

# Use the coordinator (delegates to researcher + coder)
curl -s localhost:8000/api/v1/agents/coordinator/run \
  -H "Content-Type: application/json" \
  -d '{"message": "Research quantum computing and write a Python simulation"}' | jq .

# Stream responses via SSE
curl -N localhost:8000/api/v1/agents/research-assistant/run/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "Tell me about Cedar policy language"}'
```

### Teams (via UI or API)

```bash
# List teams
curl -s localhost:8000/api/v1/teams | jq '.[] | .name'

# Run the research team (planner + researcher + coder + synthesizer)
curl -N localhost:8000/api/v1/teams/research-team/run/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "Compare sorting algorithms and write benchmarks in Python"}'
```

### With JWT Auth

Add the `Authorization` header to all API calls:

```bash
# Get a token
TOKEN=$(curl -s https://your-keycloak/realms/your-realm/protocol/openid-connect/token \
  -d "grant_type=password&client_id=agentic-gateway&username=user&password=pass" \
  | jq -r .access_token)

# Use it
curl -s localhost:8000/api/v1/agents \
  -H "Authorization: Bearer $TOKEN" | jq .
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `AWS_REGION` | `us-east-1` | AWS region for all AgentCore calls |
| `AGENTCORE_MEMORY_ID` | _(empty)_ | AgentCore memory resource ID |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `JWT_ISSUER` | _(empty)_ | OIDC issuer URL (leave empty to skip JWT) |
| `JWT_AUDIENCE` | _(empty)_ | Expected JWT audience claim |
| `JWT_CLIENT_ID` | `agentic-gateway` | OIDC client ID for UI login |
| `KEYCLOAK_ISSUER` | _(empty)_ | Keycloak issuer (client script) |
| `KEYCLOAK_USERNAME` | _(empty)_ | Keycloak username (client script) |
| `KEYCLOAK_PASSWORD` | _(empty)_ | Keycloak password (client script) |
| `KEYCLOAK_CLIENT_ID` | `agentic-gateway` | Keycloak client ID (client script) |
| `GATEWAY_URL` | `http://localhost:8000` | Gateway server URL (client script) |
