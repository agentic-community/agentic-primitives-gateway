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

  knowledge:
    default: "llamaindex"
    backends:
      llamaindex:
        backend: "agentic_primitives_gateway.primitives.knowledge.llamaindex.LlamaIndexKnowledgeProvider"
        config:
          store_type: vector          # vector | graph | hybrid
          embed_model:
            provider: bedrock
            config:
              model_name: amazon.titan-embed-text-v2:0
          llm:                         # only used by query() — synthesis routes through registry.llm
            backend_name: bedrock
            model: us.anthropic.claude-sonnet-4-20250514-v1:0
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

`allow_server_credentials` controls how the gateway resolves credentials when clients don't provide them via headers:

| Mode | Behavior |
|------|----------|
| `never` (default) | Require per-user or header-provided credentials. Fail if missing. |
| `fallback` | Try per-user OIDC credentials first, then fall back to server ambient. |
| `always` | Always use server credentials (dev mode). |

```yaml
allow_server_credentials: fallback
```

### Per-User Credential Resolution (OIDC)

In multi-tenant deployments, different users need different backend credentials. The credentials subsystem resolves per-user credentials from OIDC user attributes and populates the same contextvars that `X-Cred-*` headers populate. Providers work unchanged.

**Convention-based naming:** Use `apg.{service}.{key}` format (e.g., `apg.langfuse.public_key`). The resolver auto-discovers all `apg.*` attributes and maps them to `service_credentials[service][key]`.

```yaml
allow_server_credentials: fallback

credentials:
  resolver: oidc            # "noop" (default) or "oidc"
  oidc:
    aws:
      enabled: false        # Phase 4: STS AssumeRoleWithWebIdentity
  writer:
    backend: keycloak       # "noop" (default) or "keycloak"
    config:
      admin_client_id: "${KC_ADMIN_CLIENT_ID}"
      admin_client_secret: "${KC_ADMIN_CLIENT_SECRET}"
  cache:
    ttl_seconds: 300
    max_entries: 10000
```

**Keycloak setup:**

1. Create a confidential client with Service Accounts Roles enabled
2. Assign `realm-management` roles: `manage-users` + `manage-realm`
3. Set credentials via the gateway UI Settings page (`/ui/settings`)

The resolver reads user attributes directly from the Keycloak Admin API (when admin credentials are configured), bypassing the need for protocol mappers. Falls back to the userinfo endpoint when admin credentials are unavailable.

Credential resolution order:

1. **Explicit headers** (`X-AWS-*`, `X-Cred-*`) always win
2. **OIDC-resolved credentials**: per-user attributes from the identity provider
3. **Server ambient credentials**: from environment (when mode is `fallback` or `always`)

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
- **`shared_with: []`** means private; only the owner can access. This is the default for API-created resources.
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

When using `redis`, the store also creates a `RedisEventStore` (for background run event persistence) and a `RedisSessionRegistry` (for cross-replica browser/code_interpreter session tracking). No additional config needed; these are wired up automatically.

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

## Checkpoint Configuration

Checkpointing makes agent and team runs durable across server restarts. When enabled, run state is saved to Redis before each LLM call, allowing another replica to resume on crash.

```yaml
agents:
  checkpointing:
    enabled: true
    redis_url: "redis://localhost:6379/0"
```

The same configuration applies to teams:

```yaml
teams:
  checkpointing:
    enabled: true
    redis_url: "redis://localhost:6379/0"
```

Checkpointing requires a Redis store backend (`store.backend: redis`) and a `RedisEventStore` for event persistence. When the store backend is already `redis`, the checkpoint store reuses the same connection.

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

The four primary configs cover different deployment stages:

| Config | Command | What it does |
|---|---|---|
| `quickstart.yaml` | `./run.sh` | Bedrock LLM + in-memory. No infra needed beyond AWS creds. |
| `agentcore.yaml` | `./run.sh agentcore` | All AWS managed (AgentCore + Bedrock). Needs Redis. |
| `selfhosted.yaml` | `./run.sh selfhosted` | Open-source backends (Milvus, Langfuse, Jupyter, Selenium). Needs Redis. |
| `mixed.yaml` | `./run.sh mixed` | Both AgentCore + self-hosted backends + JWT + Cedar + credentials. |

## Full Config Reference (mixed.yaml)

The `mixed.yaml` config demonstrates every configuration feature. Each section is annotated below.

### Authentication

```yaml
auth:
  backend: jwt                           # "noop" (dev), "api_key", or "jwt"
  jwt:
    issuer: "${JWT_ISSUER}"              # OIDC issuer URL (required)
    audience: "${JWT_AUDIENCE:-}"        # Expected audience (optional)
    client_id: "${JWT_CLIENT_ID:=agentic-gateway}"  # Public client for UI OIDC flow
    algorithms: ["RS256"]                # JWT signing algorithms
    claims_mapping:
      groups: "groups"                   # Claim name for group membership
      scopes: "scope"                    # Claim name for scopes (admin check)
```

With `jwt` auth, all API requests (except `/healthz`, `/auth/config`, `/ui/`) require a valid JWT. The UI uses OIDC Authorization Code + PKCE flow to get tokens. `is_admin` checks for `"admin"` in either scopes or groups.

### Cedar Policy Enforcement

```yaml
enforcement:
  backend: "agentic_primitives_gateway.enforcement.cedar.CedarPolicyEnforcer"
  config:
    policy_refresh_interval: 5           # Seconds between policy reloads
```

Default-deny when active. Policies are loaded from the policy primitive provider and evaluated on every request. Exempt paths: `/healthz`, `/readyz`, `/ui/`, `/api/v1/policy`, `/auth/config`.

### Providers (Multi-Backend)

Each primitive has a `default` and a `backends` map. Both run simultaneously — switch per-request via `X-Provider-*` headers:

```yaml
providers:
  memory:
    default: "mem0"                      # Used when no override is specified
    backends:
      mem0:                              # Self-hosted: mem0 + Milvus vector store
        backend: "agentic_primitives_gateway.primitives.memory.mem0_provider.Mem0MemoryProvider"
        config:
          vector_store:
            provider: milvus
            config:
              collection_name: agentic_memories
              url: "http://localhost:19530"
              embedding_model_dims: 1024
          llm:
            provider: aws_bedrock
            config:
              model: us.anthropic.claude-sonnet-4-20250514-v1:0
          embedder:
            provider: aws_bedrock
            config:
              model: amazon.titan-embed-text-v2:0
      agentcore:                         # AWS managed: AgentCore memory
        backend: "agentic_primitives_gateway.primitives.memory.agentcore.AgentCoreMemoryProvider"
        config:
          region: "us-east-1"
          memory_id: "${AGENTCORE_MEMORY_ID}"
```

Override at request time: `curl -H "X-Provider-Memory: agentcore" ...`

The same multi-backend pattern applies to all primitives: observability (langfuse/agentcore), code_interpreter (jupyter/agentcore), browser (selenium_grid/agentcore), etc.

### Credential Resolution

```yaml
allow_server_credentials: fallback       # "never", "fallback", or "always"

credentials:
  resolver: oidc                         # "noop" or "oidc"
  oidc:
    aws:
      enabled: false                     # Resolve AWS creds from OIDC attributes?
  writer:
    backend: keycloak                    # Write credentials back to Keycloak
    config:
      admin_client_id: "${KC_ADMIN_CLIENT_ID}"    # Service account client
      admin_client_secret: "${KC_ADMIN_CLIENT_SECRET}"
  cache:
    ttl_seconds: 300                     # Cache resolved credentials for 5min
    max_entries: 10000
```

**Resolution order:** explicit headers (`X-Cred-*`) → OIDC user attributes (`apg.*`) → server ambient credentials.

The `oidc` resolver reads `apg.{service}.{key}` attributes from the user's identity provider profile. For example, if user Alice has `apg.langfuse.public_key` in Keycloak, the Langfuse provider gets her key automatically. The Settings page in the UI writes credentials back via the `keycloak` writer.

### Agent and Team Stores

```yaml
agents:
  store:
    backend: redis                       # "file" or "redis"
    config:
      redis_url: "redis://localhost:6379/0"

teams:
  store:
    backend: redis
    config:
      redis_url: "redis://localhost:6379/0"
```

File stores persist to JSON (single-replica). Redis stores enable multi-replica deployments where any pod can serve any request.

### Environment Variable Expansion

All config values support `${VAR}`, `${VAR:=default}` (set if unset), and `${VAR:-default}` (use default but don't set) syntax. This makes configs portable across environments without editing YAML.

## Hot Reload

The gateway watches the config file for changes (useful with Kubernetes ConfigMaps):

```yaml
# Config changes are detected automatically
# Providers are swapped atomically under the GIL
# Old providers are closed gracefully
```
