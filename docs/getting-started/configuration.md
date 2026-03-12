# Configuration

The gateway is configured via YAML files with environment variable expansion.

## Config File Location

Set via environment variable:

```bash
AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE=configs/kitchen-sink.yaml
```

## Provider Configuration

Each primitive has a `default` provider and a `backends` map:

```yaml
providers:
  memory:
    default: "in_memory"           # Used when no override is specified
    backends:
      in_memory:
        backend: "agentic_primitives_gateway.primitives.memory.in_memory.InMemoryProvider"
        config: {}
      mem0:
        backend: "agentic_primitives_gateway.primitives.memory.mem0_provider.Mem0MemoryProvider"
        config:
          vector_store:
            provider: milvus
            config:
              collection_name: agentic_memories
              url: "http://${MILVUS_HOST:=localhost}:${MILVUS_PORT:=19530}"
          llm:
            provider: aws_bedrock
            config:
              model: us.anthropic.claude-sonnet-4-20250514-v1:0
          embedder:
            provider: aws_bedrock
            config:
              model: amazon.titan-embed-text-v2:0
```

### Environment Variable Expansion

Use `${VAR:=default}` syntax in YAML:

```yaml
region: "${AWS_REGION:=us-east-1}"
url: "http://${MILVUS_HOST:=localhost}:${MILVUS_PORT:=19530}"
```

### Per-Request Provider Routing

Clients can override which backend to use per-request via headers:

```bash
# Use mem0 for this specific request
curl -H "X-Provider-Memory: mem0" http://localhost:8000/api/v1/memory/ns

# Override all primitives
curl -H "X-Provider: agentcore" http://localhost:8000/api/v1/memory/ns
```

## Credential Pass-Through

### AWS Credentials

```bash
curl -H "X-AWS-Access-Key-Id: AKIA..." \
     -H "X-AWS-Secret-Access-Key: ..." \
     -H "X-AWS-Session-Token: ..." \
     -H "X-AWS-Region: us-east-1" \
     http://localhost:8000/api/v1/memory/ns
```

### Service Credentials

Generic key-value credentials for any service:

```bash
curl -H "X-Cred-Langfuse-Public-Key: pk-..." \
     -H "X-Cred-Langfuse-Secret-Key: sk-..." \
     http://localhost:8000/api/v1/observability/traces
```

### Server Credential Fallback

By default, the gateway requires client credentials. To allow the server's own credentials as a fallback:

```yaml
allow_server_credentials: true
```

## Authentication

The gateway supports pluggable authentication backends configured via the `auth` block.

### Backends

```yaml
# Noop (default) — dev mode, full access
auth:
  backend: noop

# API key — static keys mapped to principals
auth:
  backend: api_key
  api_keys:
    - key: "sk-dev-12345"
      principal_id: "dev-user"
      principal_type: "user"
      groups: ["engineering"]
      scopes: ["admin"]

# JWT — OIDC token validation
auth:
  backend: jwt
  jwt:
    issuer: "https://keycloak.example.com/realms/my-realm"
    audience: ""
    client_id: "my-app-ui"
    algorithms: ["RS256"]
    claims_mapping:
      groups: "groups"
      scopes: "scope"
```

### Resource Ownership

All agents, teams, and related resources track ownership for access control:

- **`owner_id`** is set automatically from the authenticated principal on create.
- **`shared_with: []`** means private -- only the owner can access. This is the default for API-created resources.
- **`shared_with: ["*"]`** means all authenticated users can view/use the resource. This is the default for config-seeded resources.
- The **owner** can edit and delete the resource. Users in **shared groups** can view and use it. Users with the **admin** scope bypass all access checks.

## Store Backend Configuration

Agent specs, team specs, and associated components (background run managers, session registries) are managed by pluggable store backends.

### Store Configuration

Each subsystem (agents, teams) has a `store` block with `backend` and `config`:

```yaml
agents:
  store:
    backend: file                     # "file", "redis", or dotted class path
    config:
      path: "agents.json"            # Backend-specific kwargs
```

### Available Backends

| Alias | Class | Config keys | Description |
|-------|-------|-------------|-------------|
| `file` | `FileAgentStore` / `FileTeamStore` | `path` | JSON file persistence (default, single-replica) |
| `redis` | `RedisAgentStore` / `RedisTeamStore` | `redis_url` | Redis hash storage (multi-replica) |

Custom backends can be specified by dotted class path instead of an alias.

### Redis Backend

When using `redis`, the store also creates a `RedisEventStore` (for background run event persistence) and a `RedisSessionRegistry` (for cross-replica browser/code_interpreter session tracking). No additional config needed -- these are wired up automatically.

```yaml
agents:
  store:
    backend: redis
    config:
      redis_url: "redis://my-redis:6379/0"

teams:
  store:
    backend: redis
    config:
      redis_url: "redis://my-redis:6379/0"

providers:
  tasks:
    default: redis
    backends:
      redis:
        backend: "agentic_primitives_gateway.primitives.tasks.redis.RedisTasksProvider"
        config:
          redis_url: "redis://my-redis:6379/0"
```

### Tasks Provider

The tasks provider (for team task boards) is configured separately as a standard provider:

| Backend | Description |
|---------|-------------|
| `NoopTasksProvider` | Returns empty results (default) |
| `InMemoryTasksProvider` | In-process task board with asyncio.Lock (dev) |
| `RedisTasksProvider` | Redis-backed with atomic Lua scripts (multi-replica) |

## Agent Configuration

```yaml
agents:
  store:
    backend: file
    config:
      path: "agents.json"
  specs:
    research-assistant:
      model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
      description: "A research assistant with long-term memory"
      system_prompt: |
        You are a research assistant with long-term memory.
      primitives:
        memory:
          enabled: true
          namespace: "agent:{agent_name}"
        browser:
          enabled: true
      provider_overrides:
        browser: "selenium_grid"
      hooks:
        auto_memory: true
        auto_trace: false
      max_turns: 20
      temperature: 1.0
```

Agents defined in config are seeded into the store on startup. Config values **overwrite** existing agents with the same name.

## Team Configuration

```yaml
teams:
  store:
    backend: file
    config:
      path: "teams.json"
  specs:
    research-team:
      description: "Researches and codes collaboratively"
      planner: "planner"
      synthesizer: "synthesizer"
      workers: ["researcher", "coder"]
      global_max_turns: 100
      global_timeout_seconds: 300
```

See [Teams](../concepts/teams.md) for full documentation.

## Policy Enforcement

```yaml
enforcement:
  backend: "agentic_primitives_gateway.enforcement.cedar.CedarPolicyEnforcer"
  config:
    policy_refresh_interval: 30
  seed_policies:
    - description: "Allow all"
      policy_body: 'permit(principal, action, resource);'
```

## Preset Configs

| File | Description |
|------|-------------|
| `local.yaml` | All noop/in-memory providers, no external deps |
| `kitchen-sink.yaml` | All providers registered, agent team example, Cedar enforcement |
| `agentcore.yaml` | All primitives backed by AWS Bedrock AgentCore |
| `agentcore-redis.yaml` | AgentCore + Redis stores for multi-replica |
| `milvus-langfuse.yaml` | mem0 + Milvus memory, Langfuse observability |
| `agents-agentcore.yaml` | Agents with AgentCore backends |
| `agents-mem0-langfuse.yaml` | Agents with mem0 memory, Langfuse tracing |
| `agents-mixed.yaml` | Mixed providers per primitive |
| `local-jwt.yaml` | Local providers with JWT authentication enabled |

## Hot Reload

The gateway watches the config file for changes (useful with Kubernetes ConfigMaps):

```yaml
# Config changes are detected automatically
# Providers are swapped atomically under the GIL
# Old providers are closed gracefully
```
