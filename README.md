# Agentic Primitives Gateway -- Operator Manual

Agentic Primitives Gateway is a Kubernetes-deployed REST API service that abstracts agent infrastructure primitives behind a unified API. Agent developers call this service without knowing backend implementations. Platform operators swap backends via configuration. Requests can dynamically select which backend to use via header-based provider routing.

## Architecture

```
+---------------------------------------------------------------------+
|                    Agentic Primitives Gateway                       |
|                                                                     |
|  +----------+ +----------+ +----------+ +----------+ +----------+   |
|  |  Memory   | | Identity | |  Code    | | Browser  | |  Tools   |  |
|  |  Routes   | |  Routes  | |Interpret.| |  Routes  | |  Routes  |  |
|  +-----+----+ +-----+----+ |  Routes  | +-----+----+ +-----+----+  |
|        |            |       +-----+----+       |            |       |
|  +-----+----+ +-----+----+       |       +----+-----+ +----+-----+ |
|  |Observ.   | |Gateway   |       |       |          | |          |  |
|  |Routes    | |Routes    |       |       |          | |          |  |
|  +-----+----+ +-----+----+       |       |          | |          |  |
|        |            |             |       |          | |          |  |
|  +-----v------------v-------------v-------v----------v-v----------+ |
|  |               RequestContextMiddleware                         | |
|  |       (AWS creds + provider routing from headers)              | |
|  +-----+------------+-------------+-------+----------+------------+ |
|        |            |             |       |          |              |
|  +-----v------------v-------------v-------v----------v------------+ |
|  |                    Provider Registry                           | |
|  |  (loads named backends from config; resolves per-request)      | |
|  +--+-------+-------+-------+-------+--------+------+------------+ |
|     |       |       |       |       |        |      |              |
+-----+-------+-------+-------+-------+--------+------+--------------+
      |       |       |       |       |        |      |
 +----v----+ +v--------+ +v----+ +v----+ +v------+ +v--+ +v-----------+
 | Memory  | |Identity  | |Code | |Brwsr| |Obsrvb.| |Gwy| |  Tools    |
 |---------| |----------| |Intrp| |-----| |-------| |---| |-----------|
 | Noop    | |Noop      | |Noop | |Noop | |Noop   | |Nop| | Noop      |
 | InMem   | |AgntCore  | |Agnt | |Agnt | |Lang   | |   | | AgntCore  |
 | Mem0    | |Keycloak  | |Core | |Core | | fuse  | |   | | MCP       |
 | AgntCore| |Entra     | |     | |     | |AgntCre| |   | |  Registry |
 |         | |Okta      | |     | |     | |       | |   | |           |
 +---------+ +----------+ +-----+ +-----+ +-------+ +---+ +-----------+
```

## Primitives

| Primitive | Description | Available Backends |
|-----------|-------------|--------------------|
| **Memory** | Store, retrieve, and search agent memories | `NoopMemoryProvider`, `InMemoryProvider`, `Mem0MemoryProvider` (Milvus), `AgentCoreMemoryProvider` |
| **Identity** | Workload identity tokens, OAuth2 token exchange (M2M + 3LO), API key retrieval, credential provider and workload identity management | `NoopIdentityProvider`, `AgentCoreIdentityProvider`, `KeycloakIdentityProvider`, `EntraIdentityProvider`, `OktaIdentityProvider` |
| **Code Interpreter** | Sandboxed code execution sessions | `NoopCodeInterpreterProvider`, `AgentCoreCodeInterpreterProvider` |
| **Browser** | Cloud-based browser automation | `NoopBrowserProvider`, `AgentCoreBrowserProvider` |
| **Observability** | Trace and log ingestion/querying | `NoopObservabilityProvider`, `LangfuseObservabilityProvider`, `AgentCoreObservabilityProvider` |
| **Gateway** | LLM request routing | `NoopGatewayProvider` |
| **Tools** | Tool registration and invocation | `NoopToolsProvider`, `AgentCoreGatewayProvider`, `MCPRegistryProvider` |

All seven primitives are fully implemented and wired to their respective providers.

## API Reference

### Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/healthz` | Liveness probe. Returns `{"status": "ok"}`. |
| `GET` | `/readyz` | Readiness probe. Checks all provider healthchecks. Returns 200 or 503. |

### Provider Discovery

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/providers` | List available providers for each primitive. |

Example response:

```json
{
  "memory": {"default": "mem0", "available": ["mem0", "agentcore", "in_memory"]},
  "identity": {"default": "noop", "available": ["noop", "agentcore"]},
  "code_interpreter": {"default": "noop", "available": ["noop", "agentcore"]},
  "browser": {"default": "noop", "available": ["noop", "agentcore"]},
  "observability": {"default": "noop", "available": ["noop"]},
  "gateway": {"default": "noop", "available": ["noop"]},
  "tools": {"default": "noop", "available": ["noop"]}
}
```

### Memory (`/api/v1/memory`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/{namespace}` | Store a memory. Body: `{"key": "...", "content": "...", "metadata": {}}`. Returns 201. |
| `GET` | `/{namespace}/{key}` | Retrieve a memory by key. Returns 404 if not found. |
| `GET` | `/{namespace}` | List memories. Query params: `limit` (1--1000, default 100), `offset` (default 0). |
| `POST` | `/{namespace}/search` | Semantic search. Body: `{"query": "...", "top_k": 10, "filters": {}}`. |
| `DELETE` | `/{namespace}/{key}` | Delete a memory. Returns 204 on success, 404 if not found. |

Namespace conventions: `agent:<agent-id>`, `user:<user-id>`, `session:<session-id>`, `global`.

### Identity (`/api/v1/identity`)

**Token operations (data plane):**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/token` | Exchange a workload token for an external service OAuth2 token. Supports M2M and 3-legged (USER_FEDERATION) flows. |
| `POST` | `/api-key` | Retrieve a stored API key for a credential provider. |
| `POST` | `/workload-token` | Obtain a workload identity token for the agent, optionally scoped to a user. |
| `POST` | `/auth/complete` | Confirm user authorization for a 3-legged OAuth flow. Returns 204. |

**Credential provider management (control plane):**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/credential-providers` | List registered credential providers (OAuth2 and API key). |
| `POST` | `/credential-providers` | Register a new credential provider. Returns 201. |
| `GET` | `/credential-providers/{name}` | Get credential provider details. |
| `PUT` | `/credential-providers/{name}` | Update a credential provider. |
| `DELETE` | `/credential-providers/{name}` | Delete a credential provider. Returns 204. |

**Workload identity management (control plane):**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/workload-identities` | Register a new workload (agent) identity. Returns 201. |
| `GET` | `/workload-identities` | List workload identities. |
| `GET` | `/workload-identities/{name}` | Get workload identity details. |
| `PUT` | `/workload-identities/{name}` | Update a workload identity. |
| `DELETE` | `/workload-identities/{name}` | Delete a workload identity. Returns 204. |

Control plane endpoints return 501 if not supported by the configured provider.

### Code Interpreter (`/api/v1/code-interpreter`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/sessions` | Start a sandboxed execution session. Returns 201. |
| `DELETE` | `/sessions/{session_id}` | Stop a session. Returns 204. |
| `GET` | `/sessions` | List active sessions. |
| `POST` | `/sessions/{session_id}/execute` | Execute code in a session. |
| `POST` | `/sessions/{session_id}/files` | Upload a file to a session (multipart). |
| `GET` | `/sessions/{session_id}/files/{filename}` | Download a file from a session (binary). |

### Browser (`/api/v1/browser`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/sessions` | Start a browser session. Returns 201. |
| `DELETE` | `/sessions/{session_id}` | Stop a session. Returns 204. |
| `GET` | `/sessions/{session_id}` | Get session info. |
| `GET` | `/sessions` | List sessions. |
| `GET` | `/sessions/{session_id}/live-view` | Get a live view URL for a session. |

### Observability (`/api/v1/observability`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/traces` | Ingest a trace. Returns 202. |
| `POST` | `/logs` | Ingest a log entry. Returns 202. |
| `GET` | `/traces` | Query traces. |

### Gateway (`/api/v1/gateway`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/completions` | Route an LLM completion request. |
| `GET` | `/models` | List available models. |

### Tools (`/api/v1/tools`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/` | Register a tool. Returns 201. |
| `GET` | `/` | List registered tools. |
| `POST` | `/{name}/invoke` | Invoke a tool by name. |

Interactive API docs are available at `/docs` (Swagger UI) when the server is running.

---

## Configuration

Configuration is loaded from three sources in order of priority:

1. **Environment variables** (highest priority) -- prefixed with `AGENTIC_PRIMITIVES_GATEWAY_`, nested with `__`
2. **YAML config file** -- path set by `AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE` env var
3. **Defaults** -- in-memory/noop providers for all primitives

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `AGENTIC_PRIMITIVES_GATEWAY_HOST` | Bind address | `0.0.0.0` |
| `AGENTIC_PRIMITIVES_GATEWAY_PORT` | Bind port | `8000` |
| `AGENTIC_PRIMITIVES_GATEWAY_LOG_LEVEL` | Log level (`debug`, `info`, `warning`, `error`) | `info` |
| `AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE` | Path to YAML config file | -- |
| `AGENTIC_PRIMITIVES_GATEWAY_ALLOW_SERVER_CREDENTIALS` | Allow server-side credential fallback | `false` |

### Server Credential Fallback

By default, the server requires clients to pass their own credentials (AWS, Langfuse, etc.) via request headers. If a client doesn't provide credentials, the request fails with a clear error.

To allow the server to use its own credentials as a fallback:

```yaml
allow_server_credentials: true
```

Or via environment variable:

```bash
AGENTIC_PRIMITIVES_GATEWAY_ALLOW_SERVER_CREDENTIALS=true
```

When enabled, the credential resolution order is:

1. **Client headers** (always preferred) -- `X-AWS-*`, `X-Cred-Langfuse-*`, etc.
2. **Server credentials** (fallback) -- from the server's environment:
   - AWS: `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, IRSA, Pod Identity, instance profiles
   - Langfuse: `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`/`LANGFUSE_BASE_URL` env vars, or `public_key`/`secret_key` in provider config
   - Other services: their respective env vars or provider config values

### YAML Config File (Multi-Provider Format)

The config file supports multiple named backends per primitive. Each primitive has a `default` key and a `backends` map:

```yaml
providers:
  memory:
    default: "mem0"
    backends:
      mem0:
        backend: "agentic_primitives_gateway.primitives.memory.mem0_provider.Mem0MemoryProvider"
        config:
          vector_store:
            provider: milvus
            config:
              collection_name: agentic_memories
              url: "http://milvus:19530"
          llm:
            provider: aws_bedrock
            config:
              model: us.anthropic.claude-sonnet-4-20250514-v1:0
          embedder:
            provider: aws_bedrock
            config:
              model: amazon.titan-embed-text-v2:0
      agentcore:
        backend: "agentic_primitives_gateway.primitives.memory.agentcore.AgentCoreMemoryProvider"
        config:
          memory_id: "your-memory-id"
          region: "us-east-1"
      in_memory:
        backend: "agentic_primitives_gateway.primitives.memory.in_memory.InMemoryProvider"
        config: {}

  identity:
    default: "agentcore"
    backends:
      agentcore:
        backend: "agentic_primitives_gateway.primitives.identity.agentcore.AgentCoreIdentityProvider"
        config:
          region: "us-east-1"
      keycloak:
        backend: "agentic_primitives_gateway.primitives.identity.keycloak.KeycloakIdentityProvider"
        config:
          server_url: "http://keycloak:8080"
          realm: "agents"
          client_id: "agentic-gateway"
          client_secret: "${KEYCLOAK_CLIENT_SECRET}"
      entra:
        backend: "agentic_primitives_gateway.primitives.identity.entra.EntraIdentityProvider"
        config:
          tenant_id: "${AZURE_TENANT_ID}"
          client_id: "${AZURE_CLIENT_ID}"
          client_secret: "${AZURE_CLIENT_SECRET}"
      okta:
        backend: "agentic_primitives_gateway.primitives.identity.okta.OktaIdentityProvider"
        config:
          domain: "${OKTA_DOMAIN}"
          client_id: "${OKTA_CLIENT_ID}"
          client_secret: "${OKTA_CLIENT_SECRET}"
          api_token: "${OKTA_API_TOKEN}"
      noop:
        backend: "agentic_primitives_gateway.primitives.identity.noop.NoopIdentityProvider"
        config: {}

  code_interpreter:
    default: "agentcore"
    backends:
      agentcore:
        backend: "agentic_primitives_gateway.primitives.code_interpreter.agentcore.AgentCoreCodeInterpreterProvider"
        config:
          region: "us-east-1"
      noop:
        backend: "agentic_primitives_gateway.primitives.code_interpreter.noop.NoopCodeInterpreterProvider"
        config: {}

  browser:
    default: "agentcore"
    backends:
      agentcore:
        backend: "agentic_primitives_gateway.primitives.browser.agentcore.AgentCoreBrowserProvider"
        config:
          region: "us-east-1"
      noop:
        backend: "agentic_primitives_gateway.primitives.browser.noop.NoopBrowserProvider"
        config: {}

  observability:
    default: "noop"
    backends:
      noop:
        backend: "agentic_primitives_gateway.primitives.observability.noop.NoopObservabilityProvider"
        config: {}
      langfuse:
        backend: "agentic_primitives_gateway.primitives.observability.langfuse.LangfuseObservabilityProvider"
        config: {}
      agentcore:
        backend: "agentic_primitives_gateway.primitives.observability.agentcore.AgentCoreObservabilityProvider"
        config:
          region: "us-east-1"
          service_name: "agentic-primitives-gateway"

  gateway:
    default: "noop"
    backends:
      noop:
        backend: "agentic_primitives_gateway.primitives.gateway.noop.NoopGatewayProvider"
        config: {}

  tools:
    default: "noop"
    backends:
      noop:
        backend: "agentic_primitives_gateway.primitives.tools.noop.NoopToolsProvider"
        config: {}
      agentcore:
        backend: "agentic_primitives_gateway.primitives.tools.agentcore.AgentCoreGatewayProvider"
        config: {}
      mcp_registry:
        backend: "agentic_primitives_gateway.primitives.tools.mcp_registry.MCPRegistryProvider"
        config: {}
```

Each backend entry has:
- `backend` -- fully qualified dotted path to the provider class
- `config` -- dict passed as `**kwargs` to the provider constructor

### Legacy Single-Provider Format

For backward compatibility, the legacy single-provider format is still supported:

```yaml
providers:
  tools:
    backend: "agentic_primitives_gateway.primitives.tools.noop.NoopToolsProvider"
    config: {}
```

When this format is detected (a `backend` key without a `backends` key), it is automatically converted to the multi-provider format with a single backend named `"default"`.

---

## Header-Based Provider Routing

Requests can select which named backend to use at runtime via HTTP headers. This allows different agents or users to route to different backends without changing server configuration.

### Headers

| Header | Scope | Description |
|--------|-------|-------------|
| `X-Provider` | All primitives | Set the default provider name for all primitives on this request. |
| `X-Provider-Memory` | Memory only | Override the provider for memory operations. |
| `X-Provider-Identity` | Identity only | Override the provider for identity operations. |
| `X-Provider-Code-Interpreter` | Code Interpreter only | Override the provider for code interpreter operations. |
| `X-Provider-Browser` | Browser only | Override the provider for browser operations. |
| `X-Provider-Observability` | Observability only | Override the provider for observability operations. |
| `X-Provider-Gateway` | Gateway only | Override the provider for gateway operations. |
| `X-Provider-Tools` | Tools only | Override the provider for tools operations. |

### Resolution Order

1. Primitive-specific header (e.g., `X-Provider-Memory`)
2. Global header (`X-Provider`)
3. Configured default for the primitive

### Examples

Route all primitives to the `agentcore` backend:

```bash
curl -H "X-Provider: agentcore" http://localhost:8000/api/v1/memory/global
```

Route memory to `in_memory` but let everything else use the configured default:

```bash
curl -H "X-Provider-Memory: in_memory" http://localhost:8000/api/v1/memory/global
```

Route memory to `mem0` while routing identity to `agentcore`:

```bash
curl -H "X-Provider-Memory: mem0" \
     -H "X-Provider-Identity: agentcore" \
     http://localhost:8000/api/v1/memory/global
```

If an unknown provider name is specified, the server returns HTTP 400 with the list of available backends for that primitive.

---

## Swapping Backends

To change which backend a primitive uses, update the config. The platform dynamically imports provider classes at startup. With the multi-provider format, you can configure multiple backends and switch between them at runtime via headers, or set a different default.

### Memory: In-Memory (dev/test)

```yaml
memory:
  default: "in_memory"
  backends:
    in_memory:
      backend: "agentic_primitives_gateway.primitives.memory.in_memory.InMemoryProvider"
      config: {}
```

No external dependencies. Data lives in process memory and is lost on restart.

### Memory: mem0 + Milvus (production)

Requires the `mem0` optional dependencies:

```bash
pip install agentic-primitives-gateway[mem0]
```

```yaml
memory:
  default: "mem0"
  backends:
    mem0:
      backend: "agentic_primitives_gateway.primitives.memory.mem0_provider.Mem0MemoryProvider"
      config:
        vector_store:
          provider: milvus
          config:
            collection_name: agentic_memories
            url: "http://milvus:19530"
        llm:
          provider: aws_bedrock
          config:
            model: us.anthropic.claude-sonnet-4-20250514-v1:0
        embedder:
          provider: aws_bedrock
          config:
            model: amazon.titan-embed-text-v2:0
```

mem0 uses Bedrock for its LLM calls (memory extraction, deduplication). AWS credentials are forwarded from the client via the `X-AWS-*` headers. The `vector_store.provider` can be changed to `weaviate`, `qdrant`, `chroma`, or any other backend that mem0 supports -- the platform does not need to change.

### Memory / Identity / Code Interpreter / Browser / Observability: AWS Bedrock AgentCore

Requires the `agentcore` optional dependencies:

```bash
pip install agentic-primitives-gateway[agentcore]
```

```yaml
memory:
  default: "agentcore"
  backends:
    agentcore:
      backend: "agentic_primitives_gateway.primitives.memory.agentcore.AgentCoreMemoryProvider"
      config:
        memory_id: "your-memory-id"
        region: "us-east-1"

identity:
  default: "agentcore"
  backends:
    agentcore:
      backend: "agentic_primitives_gateway.primitives.identity.agentcore.AgentCoreIdentityProvider"
      config:
        region: "us-east-1"

code_interpreter:
  default: "agentcore"
  backends:
    agentcore:
      backend: "agentic_primitives_gateway.primitives.code_interpreter.agentcore.AgentCoreCodeInterpreterProvider"
      config:
        region: "us-east-1"

browser:
  default: "agentcore"
  backends:
    agentcore:
      backend: "agentic_primitives_gateway.primitives.browser.agentcore.AgentCoreBrowserProvider"
      config:
        region: "us-east-1"
```

AgentCore providers use **per-request credential pass-through**. The server does not use its own AWS credentials. Instead, each client request sends AWS credentials via headers, and the server forwards them to AgentCore. See [AWS Credential Pass-Through](#aws-credential-pass-through) below.

---

## Extending with Custom Providers

### 1. Implement the abstract base class

All provider ABCs are in `agentic_primitives_gateway.primitives.base`. Each defines async methods and a `healthcheck()` method.

Example -- a Redis-backed memory provider:

```python
# my_company/providers/redis_memory.py
from agentic_primitives_gateway.primitives.base import MemoryProvider
from agentic_primitives_gateway.models.memory import MemoryRecord, SearchResult

class RedisMemoryProvider(MemoryProvider):
    def __init__(self, redis_url: str = "redis://localhost:6379", **kwargs):
        self._redis = Redis.from_url(redis_url)

    async def store(self, namespace, key, content, metadata=None):
        # ...implement...

    async def retrieve(self, namespace, key):
        # ...implement...

    async def search(self, namespace, query, top_k=10, filters=None):
        # ...implement...

    async def delete(self, namespace, key):
        # ...implement...

    async def list_memories(self, namespace, filters=None, limit=100, offset=0):
        # ...implement...

    async def healthcheck(self):
        return self._redis.ping()
```

### 2. Configure the backend

```yaml
memory:
  default: "redis"
  backends:
    redis:
      backend: "my_company.providers.redis_memory.RedisMemoryProvider"
      config:
        redis_url: "redis://redis:6379/0"
```

The class is loaded via `importlib.import_module`, so it must be importable from the Python path. If packaging as a separate wheel, install it alongside `agentic-primitives-gateway`.

### 3. Provider contract

Every provider must:
- Accept `**kwargs` in `__init__` (config values are passed as keyword arguments)
- Implement all abstract methods from the base class
- Return the expected types (Pydantic models for memory, dicts for others)
- Implement `healthcheck()` -- called by the `/readyz` endpoint

---

## Running Locally

### Prerequisites

- Python 3.11+

### Install and run

```bash
# Install the server with dev dependencies
pip install -e ".[dev]"

# Run with default in-memory providers
uvicorn agentic_primitives_gateway.main:app --reload

# Or with a config file
AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE=config.yaml uvicorn agentic_primitives_gateway.main:app --reload
```

Open http://localhost:8000/docs for the Swagger UI.

### Run tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

The test suite contains 322 tests covering all primitives, provider routing, and AWS credential pass-through.

---

## Client Library

The client is a separate package located at `client/` in the repository. Install it with:

```bash
pip install agentic-primitives-gateway-client
```

Or install from the local checkout:

```bash
pip install -e client/
```

Usage:

```python
from agentic_primitives_gateway_client import AgenticPlatformClient

client = AgenticPlatformClient(
    "http://agentic-primitives-gateway:8000",
    aws_access_key_id="AKIA...",
    aws_secret_access_key="...",
    aws_session_token="...",       # optional
    aws_region="us-east-1",        # optional
)
```

Credentials can be updated on the fly (e.g., after token refresh):

```python
client.set_aws_credentials(
    access_key_id=new_key,
    secret_access_key=new_secret,
    session_token=new_token,
)
```

---

## Deploying to Kubernetes

### Build the Docker image

```bash
docker build -t agentic-primitives-gateway:latest .

# To include mem0/Milvus support:
# Add mem0ai and pymilvus to the Dockerfile or use a build arg

# To include AgentCore support:
# Add bedrock-agentcore to the Dockerfile or use a build arg
```

### Deploy with Helm

```bash
cd deploy/helm

# Deploy with defaults
helm install agentic-primitives-gateway ./agentic-primitives-gateway

# Deploy with custom values
helm install agentic-primitives-gateway ./agentic-primitives-gateway -f my-values.yaml

# Upgrade after config changes
helm upgrade agentic-primitives-gateway ./agentic-primitives-gateway -f my-values.yaml
```

### Helm Values Reference

```yaml
replicaCount: 1

image:
  repository: agentic-primitives-gateway
  tag: latest
  pullPolicy: IfNotPresent

service:
  type: ClusterIP    # ClusterIP, NodePort, or LoadBalancer
  port: 8000

resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    cpu: 500m
    memory: 512Mi

# Allow server-side credential fallback (default: false)
allow_server_credentials: false

# Provider configuration -- rendered into a ConfigMap mounted at
# /etc/agentic-primitives-gateway/config.yaml
providers:
  memory:
    default: "mem0"
    backends:
      mem0:
        backend: "agentic_primitives_gateway.primitives.memory.mem0_provider.Mem0MemoryProvider"
        config:
          vector_store:
            provider: milvus
            config:
              collection_name: agentic_memories
              url: "http://milvus:19530"
          llm:
            provider: aws_bedrock
            config:
              model: us.anthropic.claude-sonnet-4-20250514-v1:0
          embedder:
            provider: aws_bedrock
            config:
              model: amazon.titan-embed-text-v2:0
      in_memory:
        backend: "agentic_primitives_gateway.primitives.memory.in_memory.InMemoryProvider"
        config: {}
      # agentcore:
      #   backend: "agentic_primitives_gateway.primitives.memory.agentcore.AgentCoreMemoryProvider"
      #   config:
      #     region: "us-east-1"
  observability:
    default: "noop"
    backends:
      noop:
        backend: "agentic_primitives_gateway.primitives.observability.noop.NoopObservabilityProvider"
        config: {}
      # langfuse:
      #   backend: "agentic_primitives_gateway.primitives.observability.langfuse.LangfuseObservabilityProvider"
      #   config: {}
      # agentcore:
      #   backend: "agentic_primitives_gateway.primitives.observability.agentcore.AgentCoreObservabilityProvider"
      #   config:
      #     region: "us-east-1"
  identity:
    default: "noop"
    backends:
      noop:
        backend: "agentic_primitives_gateway.primitives.identity.noop.NoopIdentityProvider"
        config: {}
      # agentcore:
      #   backend: "agentic_primitives_gateway.primitives.identity.agentcore.AgentCoreIdentityProvider"
      #   config:
      #     region: "us-east-1"
  code_interpreter:
    default: "noop"
    backends:
      noop:
        backend: "agentic_primitives_gateway.primitives.code_interpreter.noop.NoopCodeInterpreterProvider"
        config: {}
      # agentcore:
      #   backend: "agentic_primitives_gateway.primitives.code_interpreter.agentcore.AgentCoreCodeInterpreterProvider"
      #   config:
      #     region: "us-east-1"
  browser:
    default: "noop"
    backends:
      noop:
        backend: "agentic_primitives_gateway.primitives.browser.noop.NoopBrowserProvider"
        config: {}
      # agentcore:
      #   backend: "agentic_primitives_gateway.primitives.browser.agentcore.AgentCoreBrowserProvider"
      #   config:
      #     region: "us-east-1"
  gateway:
    default: "noop"
    backends:
      noop:
        backend: "agentic_primitives_gateway.primitives.gateway.noop.NoopGatewayProvider"
        config: {}
  tools:
    default: "noop"
    backends:
      noop:
        backend: "agentic_primitives_gateway.primitives.tools.noop.NoopToolsProvider"
        config: {}
```

### How It Works in K8s

1. The Helm chart creates a **ConfigMap** from the `providers` values, rendered as a YAML file.
2. The **Deployment** mounts this ConfigMap at `/etc/agentic-primitives-gateway/config.yaml` and sets `AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE` to point to it.
3. On startup, the app loads the config file and initializes all providers.
4. **Liveness probe** hits `/healthz` -- always returns 200 if the process is alive.
5. **Readiness probe** hits `/readyz` -- returns 200 only if all provider healthchecks pass.
6. The ConfigMap has a **checksum annotation** on the pod spec, so changing provider config triggers a rolling restart.

### AWS Credential Pass-Through

The server **does not use its own AWS credentials** for AgentCore calls. Instead, credentials are passed through from the client on every request via HTTP headers:

| Header | Required | Description |
|--------|----------|-------------|
| `X-AWS-Access-Key-Id` | Yes (for AgentCore) | AWS access key ID |
| `X-AWS-Secret-Access-Key` | Yes (for AgentCore) | AWS secret access key |
| `X-AWS-Session-Token` | No | STS session token (for temporary credentials) |
| `X-AWS-Region` | No | Override the provider's default region |

**How it works:**

1. The `RequestContextMiddleware` in `main.py` extracts these headers on every request.
2. The credentials are stored in a request-scoped `contextvars.ContextVar` (defined in `context.py`).
3. AgentCore providers call `get_boto3_session()` from `context.py` on each operation, which creates a `boto3.Session` with the caller's credentials.
4. If no credentials are in the headers, the providers fall back to the server environment's default credential chain (env vars, instance profile, etc.).

This means:
- **Each agent authenticates with its own AWS identity** -- no shared service credentials.
- **The server is stateless** with respect to AWS auth -- it is a pure pass-through.
- **Agents running in AgentCore Runtime** can forward their workload access tokens.
- **Agents running elsewhere** can use STS temporary credentials from `AssumeRole`.

### Service Credential Pass-Through

For non-AWS services (Langfuse, OpenAI, etc.), the platform supports a generic credential pass-through via `X-Cred-{Service}-{Key}` headers. The middleware parses these into per-service credential dicts that providers read from context.

| Header pattern | Parsed as |
|----------------|-----------|
| `X-Cred-Langfuse-Public-Key: pk-...` | `{"langfuse": {"public_key": "pk-..."}}` |
| `X-Cred-Langfuse-Secret-Key: sk-...` | `{"langfuse": {..., "secret_key": "sk-..."}}` |
| `X-Cred-Agentcore-Memory-Id: mem-123` | `{"agentcore": {"memory_id": "mem-123"}}` |
| `X-Cred-Agentcore-Gateway-Url: https://...` | `{"agentcore": {..., "gateway_url": "https://..."}}` |
| `X-Cred-Mcp-Registry-Token: eyJ...` | `{"mcp_registry": {"token": "eyJ..."}}` |
| `X-Cred-Mcp-Registry-Url: http://...` | `{"mcp_registry": {"url": "http://..."}}` |

Providers call `get_service_credentials("langfuse")` from `context.py` to read their credentials. If no credentials are in the headers, providers fall back to config-level defaults.

The client handles this via `set_service_credentials()`:

```python
# Langfuse observability
client.set_service_credentials("langfuse", {
    "public_key": "pk-...",
    "secret_key": "sk-...",
    "base_url": "https://cloud.langfuse.com",
})

# AgentCore Gateway (tools)
client.set_service_credentials("agentcore", {
    "gateway_url": "https://gw-id.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
    "gateway_token": "...",
})

# MCP Gateway Registry (tools)
client.set_service_credentials("mcp_registry", {
    "url": "http://mcp-registry:8080",
    "token": "eyJhbGciOiJSUzI1NiIs...",  # JWT token
})
```

### AgentCore Memory ID Resolution

The `AgentCoreMemoryProvider` resolves `memory_id` per-request in this order:

1. **Client header** `X-Cred-Agentcore-Memory-Id` (via `set_service_credentials("agentcore", {"memory_id": "..."})`)
2. **Config default** -- if `memory_id` is set in the provider's config block
3. **Error** -- raises a clear error instructing the user to provide a memory_id. AgentCore memory IDs must be created via the AgentCore console or API first.

---

## Project Structure

```
agentic-primitives-gateway/
├── src/agentic_primitives_gateway/
│   ├── main.py                     # FastAPI app, RequestContextMiddleware, provider discovery
│   ├── config.py                   # Settings (pydantic-settings), multi-provider config parsing
│   ├── context.py                  # Request-scoped AWS credentials and provider routing context vars
│   ├── registry.py                 # Provider registry -- loads named backends, resolves per-request
│   ├── routes/
│   │   ├── health.py               # /healthz, /readyz
│   │   ├── memory.py               # /api/v1/memory/* (5 endpoints)
│   │   ├── identity.py             # /api/v1/identity/* (3 endpoints)
│   │   ├── code_interpreter.py     # /api/v1/code-interpreter/* (6 endpoints)
│   │   ├── browser.py              # /api/v1/browser/* (5 endpoints)
│   │   ├── observability.py        # /api/v1/observability/* (3 endpoints)
│   │   ├── gateway.py              # /api/v1/gateway/* (2 endpoints)
│   │   └── tools.py                # /api/v1/tools/* (3 endpoints)
│   ├── models/                     # Pydantic request/response models per primitive
│   └── primitives/
│       ├── base.py                 # Abstract base classes for all 7 providers
│       ├── memory/
│       │   ├── noop.py             # No-op (logs only)
│       │   ├── in_memory.py        # Dict-based (dev/test)
│       │   ├── mem0_provider.py    # mem0 + Milvus
│       │   └── agentcore.py        # AWS Bedrock AgentCore
│       ├── identity/
│       │   ├── noop.py
│       │   └── agentcore.py        # AWS Bedrock AgentCore
│       ├── code_interpreter/
│       │   ├── noop.py
│       │   └── agentcore.py        # AWS Bedrock AgentCore
│       ├── browser/
│       │   ├── noop.py
│       │   └── agentcore.py        # AWS Bedrock AgentCore
│       ├── observability/
│       │   ├── noop.py
│       │   ├── langfuse.py         # Langfuse
│       │   └── agentcore.py        # AWS AgentCore via OpenTelemetry
│       ├── gateway/noop.py
│       └── tools/
│           ├── noop.py
│           ├── agentcore.py        # AWS AgentCore Gateway (MCP-compatible)
│           └── mcp_registry.py     # MCP Registry
├── client/                         # Standalone Python client (separate package: agentic-primitives-gateway-client)
├── tests/                          # Server tests (322 tests)
├── deploy/helm/agentic-primitives-gateway/   # Helm chart
├── Dockerfile                      # Multi-stage build
└── pyproject.toml
```
