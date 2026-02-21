# Agentic Primitives Gateway Client

Python client for the Agentic Primitives Gateway API. Use this to interact with gateway primitives — memory, identity, code interpreter, browser, observability, gateway, and tools — from your agent code.

## Installation

```bash
pip install agentic-primitives-gateway-client
```

The only dependency is `httpx`. No server-side dependencies are pulled in.

Optional extras:
- `pip install agentic-primitives-gateway-client[aws]` -- adds `boto3` for automatic AWS credential resolution from the environment (EKS Pod Identity, IRSA, etc.)

## Quick Start

```python
from agentic_primitives_gateway_client import AgenticPlatformClient

async with AgenticPlatformClient("http://agentic-primitives-gateway:8000") as client:
    # Store a memory
    await client.store_memory("agent:my-agent", "user-preference", "prefers dark mode")

    # Search memories
    results = await client.search_memory("agent:my-agent", "color preference")

    # Retrieve a specific memory
    record = await client.retrieve_memory("agent:my-agent", "user-preference")
    print(record["content"])  # "prefers dark mode"
```

## Connecting to the Server

The client takes a `base_url` pointing to your Agentic Primitives Gateway deployment:

```python
# Local development
client = AgenticPlatformClient("http://localhost:8000")

# Kubernetes (in-cluster, via service name)
client = AgenticPlatformClient("http://agentic-primitives-gateway.default.svc.cluster.local:8000")

# Custom timeout and headers
client = AgenticPlatformClient(
    "https://platform.example.com",
    timeout=60.0,
    headers={"Authorization": "Bearer <token>"},
)
```

All `**kwargs` beyond the named parameters are forwarded to `httpx.AsyncClient`, so you can pass `headers`, `auth`, `verify`, `transport`, etc.

## Error Handling

All API errors raise `AgenticPlatformError`:

```python
from agentic_primitives_gateway_client import AgenticPlatformClient, AgenticPlatformError

try:
    record = await client.retrieve_memory("ns", "nonexistent-key")
except AgenticPlatformError as e:
    print(e.status_code)  # 404
    print(e.detail)       # "Memory not found"
```

## Provider Routing

The server can have multiple named backends per primitive (e.g., `mem0` and `agentcore` for memory). The client can select which backend to use at runtime without any code changes when backends are added or removed.

### Discover available providers

```python
providers = await client.list_providers()
# {
#   "memory": {"default": "mem0", "available": ["mem0", "agentcore", "in_memory"]},
#   "identity": {"default": "noop", "available": ["noop", "agentcore"]},
#   ...
# }
```

### Set a default provider for all primitives

```python
# At construction time
client = AgenticPlatformClient("http://platform:8000", provider="agentcore")

# Or change it later
client.set_provider("agentcore")
```

### Override per-primitive

```python
# Use agentcore for everything, but mem0 for memory
client.set_provider("agentcore")
client.set_provider_for("memory", "mem0")
```

### Clear overrides

```python
client.clear_provider()  # go back to server defaults
```

The client sends these as `X-Provider` / `X-Provider-Memory` / etc. headers. The server resolves the right backend per request. If you request an unknown provider name, the server returns 400 with the list of available options.

---

## Primitives

### Memory

Store, retrieve, and search agent memories scoped by namespace.

```python
# Store
record = await client.store_memory(
    namespace="agent:research-bot",
    key="finding-1",
    content="The API rate limit is 100 requests per minute",
    metadata={"source": "api-docs", "confidence": 0.95},
)

# Retrieve by key
record = await client.retrieve_memory("agent:research-bot", "finding-1")

# List all memories in a namespace
result = await client.list_memories("agent:research-bot", limit=50, offset=0)
for record in result["records"]:
    print(record["key"], record["content"])

# Semantic search
results = await client.search_memory(
    namespace="agent:research-bot",
    query="rate limiting",
    top_k=5,
    filters={"source": "api-docs"},
)
for hit in results["results"]:
    print(f"{hit['score']:.2f} - {hit['record']['content']}")

# Delete
await client.delete_memory("agent:research-bot", "finding-1")
```

**Namespace conventions:**
- `agent:<agent-id>` — memories belonging to a specific agent
- `user:<user-id>` — memories scoped to a user
- `session:<session-id>` — memories for a conversation session
- `global` — shared across all agents

### Identity

Exchange credentials for access tokens or retrieve API keys. When the server is backed by AWS Bedrock AgentCore, this proxies through AgentCore's identity service.

```python
# Get an OAuth2 access token
token = await client.get_token(
    provider_name="github",
    scopes=["repo", "read:user"],
    context={"agent_identity_token": "<your-workload-token>"},
)
print(token["access_token"])

# Get an API key
key = await client.get_api_key(
    provider_name="openai",
    context={"agent_identity_token": "<your-workload-token>"},
)
print(key["api_key"])

# List configured identity providers
providers = await client.list_identity_providers()
```

### Code Interpreter

Run code in sandboxed sessions. When backed by AgentCore, sessions run in isolated AWS-managed containers.

```python
# Start a session
session = await client.start_code_session(language="python")
session_id = session["session_id"]

# Execute code
result = await client.execute_code(session_id, code="print(2 + 2)")
print(result["stdout"])  # "4\n"

# List active sessions
sessions = await client.list_code_sessions()

# Stop when done
await client.stop_code_session(session_id)
```

### Browser

Automate web interactions via cloud-based browser sessions.

```python
# Start a browser session
session = await client.start_browser_session(
    viewport={"width": 1920, "height": 1080},
)
session_id = session["session_id"]

# Get session info
info = await client.get_browser_session(session_id)

# Get a live view URL (for debugging/observation)
live = await client.get_live_view_url(session_id)
print(live["url"])

# List and stop
sessions = await client.list_browser_sessions()
await client.stop_browser_session(session_id)
```

### Observability

Ingest traces and logs for agent monitoring.

```python
# Ingest a trace
await client.ingest_trace({
    "trace_id": "abc-123",
    "spans": [{"name": "llm-call", "duration_ms": 450}],
    "metadata": {"agent": "research-bot"},
})

# Ingest a log
await client.ingest_log({
    "level": "info",
    "message": "Agent completed task",
    "metadata": {"task_id": "task-42"},
})

# Query traces
traces = await client.query_traces({"trace_id": "abc-123", "limit": 10})
```

### Gateway

Route LLM requests through the platform's model gateway.

```python
# Send a completion request
response = await client.completions({
    "model": "claude-sonnet-4-20250514",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 100,
})

# List available models
models = await client.list_models()
```

### Tools

Register, discover, and invoke MCP-compatible tools. Backends include
AgentCore Gateway and MCP Gateway Registry.

```python
# List all available tools
tools = await client.list_tools()

# Semantic search for tools by capability
results = await client.search_tools("weather forecast", max_results=5)

# Invoke a tool
result = await client.invoke_tool("web-search", {"query": "latest news"})

# Register a tool (MCP Gateway Registry only)
await client.register_tool({
    "name": "web-search",
    "description": "Search the web",
    "parameters": {"query": {"type": "string"}},
})
```

**Connecting to MCP Gateway Registry** with a JWT token:

```python
client.set_service_credentials("mcp_registry", {
    "url": "http://mcp-registry:8080",
    "token": "eyJhbGciOiJSUzI1NiIs...",
})
client.set_provider_for("tools", "mcp_registry")

# All tool calls now go through the registry with your JWT
tools = await client.list_tools()
result = await client.invoke_tool("my-server/my-tool", {"param": "value"})
```

**Connecting to AgentCore Gateway**:

```python
client.set_service_credentials("agentcore", {
    "gateway_url": "https://gw-id.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
    "gateway_token": "...",  # OAuth token from Cognito
})
client.set_provider_for("tools", "agentcore")
```

### Health

```python
# Liveness check
health = await client.healthz()
assert health["status"] == "ok"

# Readiness check (all providers healthy)
ready = await client.readyz()
print(ready["checks"])  # {"memory": true, "identity": true, ...}
```

---

## AWS Credential Pass-Through

When the server uses AgentCore backends (memory, identity, code interpreter, browser), it needs AWS credentials to call AgentCore APIs. **The server does not use its own credentials.** Instead, your agent passes AWS credentials through the client, and the server forwards them to AgentCore on every request.

### How it works

```
┌────────────┐  X-AWS-* headers  ┌──────────────────┐  boto3 session  ┌─────────────────┐
│   Agent     │─────────────────▶│ Agentic Primitives Gateway │───────────────▶│  AWS Bedrock    │
│  (client)   │                  │     Server       │                │   AgentCore     │
│             │                  │                  │                │                 │
│  sends AWS  │                  │  extracts creds  │                │  Memory,        │
│  creds as   │                  │  from headers,   │                │  Identity,      │
│  headers    │                  │  creates boto3   │                │  Code Interp.,  │
│             │                  │  session per req │                │  Browser        │
└────────────┘                   └──────────────────┘                └─────────────────┘
```

Each request creates a fresh `boto3.Session` from the headers, so different agents can use different AWS identities through the same server.

### Passing credentials at construction time

```python
from agentic_primitives_gateway_client import AgenticPlatformClient

client = AgenticPlatformClient(
    "http://agentic-primitives-gateway:8000",
    aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
    aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    aws_session_token="FwoGZXIvYXdzEBAaDH...",   # optional, for temp creds
    aws_region="us-east-1",                        # optional
)
```

### Auto-resolve from environment (EKS Pod Identity, IRSA, instance profiles)

The recommended approach for EKS workloads. Resolves fresh credentials from boto3's credential chain on **every request**, so temporary tokens from Pod Identity or IRSA are always current even after automatic refresh.

```bash
pip install agentic-primitives-gateway-client[aws]
```

```python
from agentic_primitives_gateway_client import AgenticPlatformClient

# Automatically resolves credentials on every request
client = AgenticPlatformClient(
    "http://agentic-primitives-gateway:8000",
    aws_from_environment=True,
)

# Optionally override the region
client = AgenticPlatformClient(
    "http://agentic-primitives-gateway:8000",
    aws_from_environment=True,
    aws_region="us-west-2",
)

# Or use a specific AWS profile
client = AgenticPlatformClient(
    "http://agentic-primitives-gateway:8000",
    aws_from_environment=True,
    aws_profile="my-profile",
)
```

This works with any credential source boto3 supports:
- **EKS Pod Identity** — credentials injected via `AWS_CONTAINER_CREDENTIALS_FULL_URI`
- **IRSA (IAM Roles for Service Accounts)** — credentials via `AWS_WEB_IDENTITY_TOKEN_FILE`
- **EC2 instance profiles** — credentials from IMDS
- **Environment variables** — `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`
- **AWS profiles** — from `~/.aws/credentials`

### Passing credentials from boto3 (snapshot)

If you want to snapshot credentials once (e.g., you're managing refresh yourself):

```python
import boto3
from agentic_primitives_gateway_client import AgenticPlatformClient

session = boto3.Session()
creds = session.get_credentials().get_frozen_credentials()

client = AgenticPlatformClient(
    "http://agentic-primitives-gateway:8000",
    aws_access_key_id=creds.access_key,
    aws_secret_access_key=creds.secret_key,
    aws_session_token=creds.token,
    aws_region=session.region_name,
)
```

### Updating credentials (e.g., after STS token refresh)

```python
# Credentials can be updated on the fly
client.set_aws_credentials(
    access_key_id=new_access_key,
    secret_access_key=new_secret_key,
    session_token=new_session_token,
)

# Or cleared entirely (server falls back to its environment's chain)
client.clear_aws_credentials()
```

### Using AssumeRole for scoped access

```python
import boto3
from agentic_primitives_gateway_client import AgenticPlatformClient

sts = boto3.client("sts")
assumed = sts.assume_role(
    RoleArn="arn:aws:iam::123456789012:role/agent-role",
    RoleSessionName="my-agent",
)
temp = assumed["Credentials"]

client = AgenticPlatformClient(
    "http://agentic-primitives-gateway:8000",
    aws_access_key_id=temp["AccessKeyId"],
    aws_secret_access_key=temp["SecretAccessKey"],
    aws_session_token=temp["SessionToken"],
)
```

### Inside AgentCore Runtime

If your agent runs inside AgentCore Runtime, you can combine credential pass-through with workload identity tokens for the Identity primitive:

```python
from bedrock_agentcore import BedrockAgentCoreContext
from agentic_primitives_gateway_client import AgenticPlatformClient

# Get the runtime's workload token
workload_token = BedrockAgentCoreContext.get_workload_access_token()

# AWS credentials are available from the runtime environment
import boto3
session = boto3.Session()
creds = session.get_credentials().get_frozen_credentials()

client = AgenticPlatformClient(
    "http://agentic-primitives-gateway:8000",
    aws_access_key_id=creds.access_key,
    aws_secret_access_key=creds.secret_key,
    aws_session_token=creds.token,
)

# Use the workload token for identity operations
github_token = await client.get_token(
    provider_name="github",
    scopes=["repo"],
    context={"agent_identity_token": workload_token},
)
```

---

## Framework Integration Examples

### Strands Agents

```python
from strands import Agent
from agentic_primitives_gateway_client import AgenticPlatformClient

platform = AgenticPlatformClient(
    "http://agentic-primitives-gateway:8000",
    aws_from_environment=True,
)

async def remember(agent: Agent, key: str, content: str):
    """Store something the agent learned."""
    await platform.store_memory(
        namespace=f"agent:{agent.name}",
        key=key,
        content=content,
    )

async def recall(agent: Agent, query: str) -> str:
    """Search the agent's memory."""
    results = await platform.search_memory(
        namespace=f"agent:{agent.name}",
        query=query,
        top_k=5,
    )
    return "\n".join(r["record"]["content"] for r in results["results"])


# Use as tool functions in your Strands agent
agent = Agent(
    name="research-bot",
    tools=[remember, recall],
)
```

### LangChain / LangGraph

```python
from langchain_core.tools import tool
from agentic_primitives_gateway_client import AgenticPlatformClient

platform = AgenticPlatformClient("http://agentic-primitives-gateway:8000")

@tool
async def store_memory(namespace: str, key: str, content: str) -> str:
    """Store a piece of information in the agent's memory."""
    record = await platform.store_memory(namespace, key, content)
    return f"Stored: {record['key']}"

@tool
async def search_memory(namespace: str, query: str) -> str:
    """Search the agent's memory for relevant information."""
    results = await platform.search_memory(namespace, query, top_k=5)
    hits = results["results"]
    if not hits:
        return "No relevant memories found."
    return "\n".join(f"- {h['record']['content']}" for h in hits)

# Use in a LangGraph agent
from langgraph.prebuilt import create_react_agent

agent = create_react_agent(
    model,
    tools=[store_memory, search_memory],
)
```

### CrewAI

```python
from crewai import Agent, Task, Crew
from crewai.tools import BaseTool
from agentic_primitives_gateway_client import AgenticPlatformClient

platform = AgenticPlatformClient("http://agentic-primitives-gateway:8000")

class MemorySearchTool(BaseTool):
    name: str = "memory_search"
    description: str = "Search the agent's long-term memory"

    async def _arun(self, query: str) -> str:
        results = await platform.search_memory("agent:crew", query, top_k=5)
        return "\n".join(r["record"]["content"] for r in results["results"])

researcher = Agent(
    role="Researcher",
    goal="Find relevant information",
    tools=[MemorySearchTool()],
)
```

### Plain asyncio

```python
import asyncio
from agentic_primitives_gateway_client import AgenticPlatformClient

async def main():
    async with AgenticPlatformClient("http://localhost:8000") as client:
        # Store facts
        await client.store_memory("global", "fact-1", "Water boils at 100°C")
        await client.store_memory("global", "fact-2", "Light travels at ~300,000 km/s")

        # Search
        results = await client.search_memory("global", "temperature")
        for hit in results["results"]:
            print(hit["record"]["content"])

        # Health check
        status = await client.readyz()
        print("Platform status:", status["status"])

asyncio.run(main())
```

---

## Client API Reference

### `AgenticPlatformClient`

```python
AgenticPlatformClient(
    base_url: str = "http://localhost:8000",
    timeout: float = 30.0,
    aws_access_key_id: str | None = None,
    aws_secret_access_key: str | None = None,
    aws_session_token: str | None = None,
    aws_region: str | None = None,
    aws_from_environment: bool = False,
    aws_profile: str | None = None,
    provider: str | None = None,
    **httpx_kwargs,
)
```

Async context manager. All methods are `async`.

| Method | Parameters | Description |
|--------|-----------|-------------|
| `set_aws_credentials()` | `access_key_id, secret_access_key, session_token=None, region=None` | Set/update AWS credentials for all future requests |
| `clear_aws_credentials()` | — | Remove AWS credentials from future requests |
| `set_provider()` | `name` | Set the default provider for all primitives |
| `set_provider_for()` | `primitive, name` | Set the provider for a specific primitive |
| `clear_provider()` | — | Remove all provider routing overrides |
| `set_service_credentials()` | `service, credentials` | Set credentials for a service (e.g., Langfuse, AgentCore) |
| `clear_service_credentials()` | `service=None` | Remove service credentials (one or all) |

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| **Providers** | | | |
| `list_providers()` | — | `dict` | Discover available providers per primitive |
| **Health** | | | |
| `healthz()` | — | `dict` | Liveness check |
| `readyz()` | — | `dict` | Readiness check with provider status |
| **Memory** | | | |
| `store_memory()` | `namespace, key, content, metadata=None` | `dict` | Store/upsert a memory |
| `retrieve_memory()` | `namespace, key` | `dict` | Get a memory by key |
| `list_memories()` | `namespace, limit=100, offset=0` | `dict` | List memories in a namespace |
| `search_memory()` | `namespace, query, top_k=10, filters=None` | `dict` | Semantic search |
| `delete_memory()` | `namespace, key` | `None` | Delete a memory |
| **Identity** | | | |
| `get_token()` | `provider_name, scopes=None, context=None` | `dict` | Get an access token |
| `get_api_key()` | `provider_name, context=None` | `dict` | Get an API key |
| `list_identity_providers()` | — | `dict` | List identity providers |
| **Code Interpreter** | | | |
| `start_code_session()` | `session_id=None, language="python", config=None` | `dict` | Start a code session |
| `stop_code_session()` | `session_id` | `None` | Stop a session |
| `execute_code()` | `session_id, code, language="python"` | `dict` | Execute code |
| `list_code_sessions()` | — | `dict` | List sessions |
| `upload_file()` | `session_id, filename, content` | `dict` | Upload a file to a session |
| `download_file()` | `session_id, filename` | `bytes` | Download a file from a session |
| **Browser** | | | |
| `start_browser_session()` | `session_id=None, viewport=None, config=None` | `dict` | Start a browser session |
| `stop_browser_session()` | `session_id` | `None` | Stop a session |
| `get_browser_session()` | `session_id` | `dict` | Get session info |
| `list_browser_sessions()` | — | `dict` | List sessions |
| `get_live_view_url()` | `session_id` | `dict` | Get live view URL |
| **Observability** | | | |
| `ingest_trace()` | `trace` | `dict` | Ingest a trace |
| `ingest_log()` | `log_entry` | `dict` | Ingest a log entry |
| `query_traces()` | `filters=None` | `dict` | Query traces |
| **Gateway** | | | |
| `completions()` | `model_request` | `dict` | Route an LLM request |
| `list_models()` | — | `dict` | List available models |
| **Tools** | | | |
| `register_tool()` | `tool_def` | `dict` | Register a tool |
| `list_tools()` | — | `dict` | List tools |
| `invoke_tool()` | `tool_name, params` | `dict` | Invoke a tool |
| `search_tools()` | `query, max_results=10` | `dict` | Semantic search for tools |

### `AgenticPlatformError`

```python
AgenticPlatformError(status_code: int, detail: str)
```

Raised for any HTTP response with status >= 400. Properties: `status_code`, `detail`.
