# Agentic Primitives Gateway

![logo](docs/images/logo.png)

A pluggable infrastructure gateway for AI agents. Agents call a stable REST API for memory, browser automation, code execution, observability, identity, tools, LLM routing, policy enforcement, and evaluations. Platform operators choose backends via YAML configuration. The two concerns are fully decoupled.

**[Documentation](https://agentic-community.github.io/agentic-primitives-gateway/)** | **[API Reference](https://agentic-community.github.io/agentic-primitives-gateway/api/overview/)** | **[Examples](examples/)**

## Why This Exists

AI agents need infrastructure: memory, identity, sandboxed code execution, browsers, observability, tools, policies, and evaluations. Today, every agent framework hard-codes these to specific vendors. Switching memory from mem0 to AWS Bedrock AgentCore means rewriting agent code. Running the same agent in different environments means maintaining multiple configurations inside the agent itself.

The gateway extracts infrastructure into a standalone service. Agent developers code against the API. Platform operators choose backends via configuration. Switching from Langfuse to AgentCore for observability is a YAML config change, not a code change.

## Quickstart

```bash
# Prerequisites: Python 3.11+, AWS credentials (aws configure)
git clone <repo-url>
cd agentic-primitives-gateway
pip install -e .
./run.sh
```

The gateway starts at `http://localhost:8000` with Bedrock for LLM and in-memory storage. A declarative agent (assistant with memory) is included — no Python code needed.

**Chat with the agent:**

```bash
curl -X POST http://localhost:8000/api/v1/agents/assistant/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello! Remember that my favorite color is blue."}'

curl -X POST http://localhost:8000/api/v1/agents/assistant/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is my favorite color?"}'
```

**Use the Python client:**

```python
from agentic_primitives_gateway_client import AgenticPlatformClient, Memory

client = AgenticPlatformClient("http://localhost:8000", aws_from_environment=True)
memory = Memory(client, namespace="agent:my-agent")

await memory.remember("api-limit", "100 requests per minute")
results = await memory.search("rate limiting")
```

**Auto-build tools for any framework:**

```python
# Strands
tools = client.get_tools_sync(["memory", "browser"], namespace="agent:demo", format="strands")
agent = Agent(model="us.anthropic.claude-sonnet-4-20250514-v1:0", tools=tools)

# LangChain
tools = await client.get_tools(["memory", "browser"], namespace="agent:demo", format="langchain")
agent = create_agent(llm, tools=tools)
```

Open the web UI at `http://localhost:8000/ui/` (after `cd ui && npm install && npm run build`). Interactive API docs at `/docs`.

See the [Quickstart Guide](https://agentic-community.github.io/agentic-primitives-gateway/getting-started/quickstart/) for complete setup instructions and the [Building Tools Guide](https://agentic-community.github.io/agentic-primitives-gateway/guides/building-tools/) for all integration approaches.

## Configurations

| Config | Command | What you get |
|---|---|---|
| **quickstart** | `./run.sh` | Bedrock LLM + in-memory storage. AWS creds only. |
| **agentcore** | `./run.sh agentcore` | All AWS managed (AgentCore + Bedrock). Needs Redis + `AGENTCORE_MEMORY_ID`. |
| **selfhosted** | `./run.sh selfhosted` | Open-source: mem0/Milvus, Langfuse, Jupyter, Selenium. Needs Redis. |
| **mixed** | `./run.sh mixed` | Both backends + JWT auth + Cedar policies + OIDC credentials. |

The **selfhosted** and **mixed** configs require open-source infrastructure (Milvus, Langfuse, Selenium, Jupyter, Redis). You can run these locally with Docker, or deploy them on Kubernetes using the [Agents on EKS](https://awslabs.github.io/ai-on-eks/docs/infra/agents-on-eks) infrastructure which provisions everything with a single command.

See the [Configuration Guide](https://agentic-community.github.io/agentic-primitives-gateway/getting-started/configuration/) for a full reference of every config option.

## Architecture

```
+------------------------------------------------------------------------+
|                      Agentic Primitives Gateway                        |
|                                                                        |
|  +------------------------------------------------------------------+  |
|  |  Web UI (React SPA at /ui/) -- Dashboard, Agents, Teams, Chat    |  |
|  +------------------------------------------------------------------+  |
|                                                                        |
|  +------------------------------------------------------------------+  |
|  |                     Agents Subsystem                              |  |
|  |  (Declarative specs, CRUD API, LLM tool-call loop, auto-hooks)   |  |
|  |  POST /api/v1/agents/{name}/chat --> AgentRunner --> primitives   |  |
|  +----+------------------------------+------------------------------+  |
|       |                              |                                 |
|  +---------+ +---------+ +---------+ +---------+ +---------+          |
|  | Memory  | |Identity | |  Code   | | Browser | |  Tools  |          |
|  | Routes  | | Routes  | |Interpret| | Routes  | | Routes  |          |
|  +----+----+ +----+----+ | Routes  | +----+----+ +----+----+          |
|       |           |      +----+----+      |           |               |
|  +----+----+ +----+----+     |       +----+----+ +----+----+          |
|  |Observ.  | |  LLM    |     |       | Policy  | | Evals   |          |
|  | Routes  | | Routes  |     |       | Routes  | | Routes  |          |
|  +----+----+ +----+----+     |       +----+----+ +----+----+          |
|       |           |           |       |         | |         |          |
|  +----v-----------v-----------v-------v---------v-v---------v--------+ |
|  |              PolicyEnforcementMiddleware (Cedar)                  | |
|  +------------------------------------------------------------------+ |
|  |              AuthenticationMiddleware (JWT/API key/noop)          | |
|  +------------------------------------------------------------------+ |
|  |              CredentialResolutionMiddleware (OIDC)                | |
|  +------------------------------------------------------------------+ |
|  |              RequestContextMiddleware (AWS creds + routing)       | |
|  +------------------------------------------------------------------+ |
|  |                     Provider Registry (MetricsProxy)              | |
|  +--+-------+-------+-------+-------+--------+-------+------+-------+ |
+-----+-------+-------+-------+-------+--------+-------+------+---------+
      |       |       |       |       |        |       |      |
 +----v---+ +-v-------+ +v------+ +v----+ +v-----+ +v------+ +v------+ +v------+ +v----------+
 | Memory | |Identity | |Code   | |Brwsr| |Obsrv.| | LLM   | |Policy | | Evals | |  Tools   |
 |--------| |---------| |Interp | |-----| |------| |-------| |-------| |-------| |----------|
 | Noop   | |Noop     | |Noop   | |Noop | |Noop  | |Noop   | |Noop   | |Noop   | | Noop     |
 | InMem  | |AgntCore | |AgntCr | |Agnt | |Lang  | |Bedrock| |Agnt   | |Agnt   | | AgntCore |
 | Mem0   | |Keycloak | |Juptyr | |Core | |fuse  | |Convrs | |Core   | |Core   | | MCP      |
 | Agnt   | |Entra    | |       | |Seln | |Agnt  | |       | |       | |       | | Registry |
 | Core   | |Okta     | |       | |Grid | |Core  | |       | |       | |       | |          |
 +--------+ +---------+ +-------+ +-----+ +------+ +-------+ +-------+ +-------+ +----------+
```

## Primitives

| Primitive | Description | Backends |
|---|---|---|
| **Memory** | Key-value storage, semantic search, conversation history, session management | Noop, InMemory, mem0/Milvus, AgentCore |
| **Identity** | Workload tokens, OAuth2 exchange, API keys, credential management | Noop, AgentCore, Keycloak, Entra, Okta |
| **Code Interpreter** | Sandboxed code execution with persistent sessions | Noop, AgentCore, Jupyter |
| **Browser** | Headless browser automation (navigate, click, screenshot) | Noop, AgentCore, Selenium Grid |
| **Observability** | Trace/log ingestion, LLM generation tracking, scoring | Noop, Langfuse, AgentCore |
| **LLM** | LLM routing with tool_use support | Noop, Bedrock Converse |
| **Tools** | Tool registration, discovery, and invocation (MCP) | Noop, AgentCore, MCP Registry |
| **Policy** | Cedar policy engine and policy CRUD | Noop, AgentCore |
| **Evaluations** | LLM-as-a-judge evaluator management and evaluation | Noop, AgentCore |

See the [Primitives Guide](https://agentic-community.github.io/agentic-primitives-gateway/concepts/primitives/) for details on each primitive and its backends.

## Declarative Agents

Agents are defined in YAML — no framework code needed. The gateway runs the LLM tool-call loop server-side.

```yaml
agents:
  specs:
    research-assistant:
      model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
      system_prompt: "You are a research assistant with long-term memory..."
      primitives:
        memory: { enabled: true }
        browser: { enabled: true }
      hooks:
        auto_memory: true
        auto_trace: true
```

Key capabilities:
- **Token streaming** via SSE
- **Agent-as-tool delegation** — agents call other agents (coordinator pattern)
- **Meta-agents** — create specialist agents at runtime
- **Teams** — multi-agent collaboration with shared task board and parallel execution
- **User-scoped memory** — automatic per-user isolation in multi-tenant deployments
- **Durable execution** — Redis checkpointing with cross-replica recovery
- **Background runs** — agent/team runs continue if the client disconnects

See the [Agents Guide](https://agentic-community.github.io/agentic-primitives-gateway/concepts/agents/) and [Teams Guide](https://agentic-community.github.io/agentic-primitives-gateway/concepts/teams/) for full documentation.

## Security

| Feature | Description |
|---|---|
| **Authentication** | Pluggable: noop (dev), static API keys, JWT/OIDC (Keycloak, Cognito, Auth0, Okta) |
| **Policy enforcement** | Cedar policies evaluated on every request. Default-deny when active. |
| **Resource ownership** | Agents/teams have `owner_id` + `shared_with`. Mutations require ownership. |
| **User-scoped memory** | Automatic `:u:{user_id}` namespace isolation for declarative agents |
| **Session ownership** | Browser/code_interpreter sessions are tied to their creator |
| **Credential isolation** | Per-request contextvars — no credential leakage between concurrent requests |
| **Per-user credentials** | OIDC-resolved credentials from user attributes (Langfuse keys, MCP tokens, etc.) |

See the [Authentication](https://agentic-community.github.io/agentic-primitives-gateway/getting-started/configuration/#authentication), [Policy Enforcement](https://agentic-community.github.io/agentic-primitives-gateway/getting-started/configuration/#enforcement), and [Credentials](https://agentic-community.github.io/agentic-primitives-gateway/getting-started/configuration/#credential-resolution) sections of the Configuration Guide.

## Development

```bash
# Server
pip install -e ".[dev]"
python -m pytest tests/ -v          # 1800+ tests

# Client (separate package)
cd client && pip install -e ".[dev]"
python -m pytest tests/ -v          # 100+ tests

# Lint & format
make format        # Auto-fix
make lint          # Check
make typecheck     # mypy
make check         # All three + tests

# Web UI
cd ui && npm install && npm run dev  # Dev server at :5173
cd ui && npm run build               # Production build
```

## Deploying

Prebuilt images are available on ECR Public:

```bash
docker pull public.ecr.aws/ai-registry/agentic-primitives-gateway/gateway:latest
```

Or build your own:

```bash
docker build -t agentic-primitives-gateway:latest .
```

Deploy with Helm:

```bash
cd deploy/helm
helm install apg ./agentic-primitives-gateway -f my-values.yaml
```

The Helm chart renders provider config as a ConfigMap, sets up health probes, and triggers rolling restarts on config changes. See `deploy/helm/` for the full chart.

## Extending

Add a custom provider by implementing the primitive's ABC and registering it in config:

```python
# my_company/providers/redis_memory.py
from agentic_primitives_gateway.primitives.base import MemoryProvider

class RedisMemoryProvider(MemoryProvider):
    def __init__(self, redis_url: str = "redis://localhost:6379", **kwargs):
        self._redis = Redis.from_url(redis_url)

    async def store(self, namespace, key, content, metadata=None): ...
    async def retrieve(self, namespace, key): ...
    async def search(self, namespace, query, top_k=10, filters=None): ...
    async def healthcheck(self): return self._redis.ping()
```

```yaml
providers:
  memory:
    default: "redis"
    backends:
      redis:
        backend: "my_company.providers.redis_memory.RedisMemoryProvider"
        config:
          redis_url: "redis://redis:6379/0"
```

## Project Structure

```
agentic-primitives-gateway/
├── src/agentic_primitives_gateway/  # Server package
│   ├── main.py                      # FastAPI app, lifespan, router registration
│   ├── middleware.py                # Request context (AWS creds, provider routing)
│   ├── config.py                    # Pydantic settings, YAML config loading
│   ├── registry.py                  # Provider registry (dynamic loading, per-request resolution)
│   ├── routes/                      # FastAPI routers (one per primitive + agents + teams)
│   ├── agents/                      # Declarative agent orchestration (runner, stores, tools, teams)
│   ├── auth/                        # Authentication (JWT, API key, noop)
│   ├── enforcement/                 # Cedar policy enforcement
│   ├── credentials/                 # Per-user credential resolution (OIDC)
│   ├── models/                      # Pydantic request/response models
│   └── primitives/                  # Provider ABCs + backend implementations
├── ui/                              # React + Vite + TypeScript + Tailwind CSS
├── client/                          # Python client (separate package)
├── tests/                           # 1800+ server tests
├── configs/                         # YAML presets (quickstart, agentcore, selfhosted, mixed)
├── examples/                        # Example agents (Strands, LangChain)
└── deploy/helm/                     # Kubernetes Helm chart
```

## License

[Apache 2.0](LICENSE)
