# Architecture

## Overview

The gateway is a FastAPI service with three layers:

1. **Middleware**: extracts credentials, routes providers, enforces policies
2. **Routes**: one router per primitive + agents + teams
3. **Provider Registry**: loads backend implementations, resolves per-request

```
Request → CORS → RequestContextMiddleware → AuthenticationMiddleware → CredentialResolutionMiddleware → PolicyEnforcementMiddleware → Route → Registry → Provider
```

## Request Flow

```
Client sends: POST /api/v1/memory/my-ns
  Headers: X-AWS-Access-Key-Id, X-Provider-Memory: mem0

1. RequestContextMiddleware (middleware.py)
   - Extracts AWS credentials → contextvars
   - Extracts provider routing → contextvars
   - Generates request ID

2. AuthenticationMiddleware (auth/middleware.py)
   - Validates credentials (JWT token, API key, or noop pass-through)
   - Sets AuthenticatedPrincipal in a contextvar (principal_id, type, groups, scopes)
   - 401 if credentials are invalid or missing (when not using noop)

3. CredentialResolutionMiddleware (credentials/middleware.py)
   - Resolves per-user service credentials from OIDC user attributes
   - Reads apg.* attributes from Keycloak Admin API (or userinfo fallback)
   - Maps apg.{service}.{key} → service_credentials[service][key]
   - Skips if explicit X-Cred-* headers are present (headers always win)
   - Caches resolved credentials in-memory per user (configurable TTL)

4. PolicyEnforcementMiddleware (enforcement/middleware.py)
   - Maps path + method → Cedar action (e.g., "memory:store_memory")
   - Evaluates: permit(User::"alice", Action::"memory:store_memory", resource)?
   - 403 if denied

5. Route handler (routes/memory.py)
   - Calls registry.memory.store(namespace, key, content)

6. Registry (registry.py)
   - Reads provider override from contextvars: "mem0"
   - Returns the mem0 provider instance (wrapped in MetricsProxy)

7. Provider (primitives/memory/mem0_provider.py)
   - Creates boto3 session from request's AWS credentials
   - Calls mem0 with Bedrock embedder
   - Returns result
```

## Provider Pattern

Each primitive has:

- **Abstract base class** (`primitives/{name}/base.py`) defining the interface
- **Multiple implementations** (`noop.py`, `in_memory.py`, `agentcore.py`, etc.)
- **Config-driven loading** via fully-qualified class paths in YAML

```python
# Config YAML
memory:
  default: "mem0"
  backends:
    mem0:
      backend: "agentic_primitives_gateway.primitives.memory.mem0_provider.Mem0MemoryProvider"
      config:
        vector_store: ...
```

The registry loads classes via `importlib`, instantiates them with the config dict, wraps them in `MetricsProxy` for Prometheus instrumentation, and stores them by name.

## Authentication

`AuthenticationMiddleware` runs after `RequestContextMiddleware` and before `PolicyEnforcementMiddleware`. It validates the incoming request's credentials, JWT token, API key, or noop (dev mode), based on the configured `auth.backend`, and sets an `AuthenticatedPrincipal` in a contextvar. The principal carries `principal_id`, `principal_type`, `groups`, and `scopes`.

Route handlers check resource-level access via `require_access()` (verifies the caller can view/use a resource based on ownership or sharing) and `require_owner_or_admin()` (verifies the caller owns the resource or has admin scope). These helpers read the principal from the contextvar and raise 403 if access is denied.

For background tasks (agent runs, team runs), the principal flows from the originating request into the `asyncio.Task` via `copy_context()`, so authorization checks remain valid even after the HTTP connection closes.

## Credential Resolution

The `credentials/` subsystem resolves per-user service credentials from OIDC user attributes. It sits between authentication and policy enforcement in the middleware stack.

**Convention-based mapping:** All gateway credentials use `apg.{service}.{key}` naming. The resolver auto-discovers `apg.*` attributes and maps them to `service_credentials[service][key]`. No explicit attribute mapping config is needed.

**Two resolution modes** (tried in order):

1. **Admin API** (preferred): Reads user attributes directly from the Keycloak Admin REST API via a service account. Returns all attributes; no protocol mappers needed.
2. **Userinfo** (fallback): Fetches from the OIDC userinfo endpoint using the caller's access token. Only returns claims with protocol mappers.

**Credential resolution chain** (priority order):

1. Explicit headers (`X-AWS-*`, `X-Cred-*`) always win
2. OIDC-resolved credentials: per-user attributes from the identity provider
3. Server ambient credentials: env vars, IRSA, provider config (when `allow_server_credentials: fallback` or `always`)

**Checkpoint integration:** OIDC-resolved credentials are captured in checkpoint data via `serialize_auth_context()`. On recovery, `restore_auth_context()` restores them into contextvars. Providers work unchanged.

The credential status endpoint (`GET /api/v1/credentials/status`) reports the active resolution source, required credential types for active providers, and the server credential fallback mode.

## Authenticated Provider Status

The `GET /api/v1/providers/status` endpoint runs behind the full middleware stack (auth + credential resolution + policy enforcement). Unlike `/readyz` (which runs healthchecks in thread pools without user context), this endpoint runs each provider's `healthcheck()` on the main event loop with the authenticated user's resolved credentials in context. Providers that show `reachable` on `/readyz` may show `ok` here if the user has valid credentials.

## Agent Subsystem

Agents sit above primitives as an orchestration layer:

```
AgentRunner.run(spec, message)
  → _init_context()       # overrides, tools, memory context
  → while loop:
      → registry.llm.route_request(...)  # LLM call
      → _exec_tools_parallel(...)            # tool execution
  → _finalize()           # cleanup, store turn, trace
```

Key design decisions:

- **`_RunContext` dataclass** holds all mutable state, shared between `run()` and `run_stream()`
- **Tool handlers** read per-primitive context (memory namespace, session IDs, team_run_id, agent_role, shared pools) from contextvars set by the runner at the start of each run and reset at the end. `functools.partial` is used only for agent-as-tool delegation (call-stack state like `agent_store` / `agent_runner` / `depth`).
- **Agent-as-tool** delegation allows agents to call other agents (depth-limited)
- **Provider overrides** are saved/restored around sub-agent calls
- **Shared memory pools** (`PrimitiveConfig.shared_namespaces`) inject pool-based tools at build time
- **Export** generates standalone Python scripts with the full tool-call loop

## Team Subsystem

Teams add a coordination layer on top of agents:

```
TeamRunner.run(team_spec, message)
  → Phase 1: Planner decomposes into tasks (with dependency graphs)
  → Phase 2: Workers execute tasks concurrently (with replanning)
  → Phase 3: Synthesizer combines results
```

Key features:

- **Shared memory** (`shared_memory_namespace`): workers can share findings via a team-scoped namespace
- **Dependency-aware execution**: tasks with `depends_on` wait for dependencies before becoming available
- **Task retry**: individual failed tasks can be retried without re-running the entire team
- **Export**: teams can be exported as standalone Python scripts with dependency-wave execution

See [Teams](teams.md) for the full replanning loop documentation.

## Background Run Infrastructure

Streaming endpoints decouple the run from the HTTP connection using `BackgroundRunManager` (`routes/_background.py`):

```
Client → SSE Response ← Queue ← asyncio.Task (background)
         (may disconnect)        (always completes, calls _finalize)
```

Components:

- **`BackgroundRunManager`**: tracks active runs in a local dict. Optional `EventStore` persists events/status to Redis for cross-replica visibility.
- **`RedisEventStore`**: stores run status and event logs in Redis lists with TTL auto-expiry.
- **`SessionRegistry`**: tracks active browser/code_interpreter sessions. `InMemorySessionRegistry` (default) or `RedisSessionRegistry` (multi-replica). Used for observability and orphan cleanup.

## Pluggable Store Backends

Agent/team specs and task boards are stored in pluggable backends:

```
Config (YAML)                    main.py
  store:                          _load_class(alias → dotted path)
    backend: "redis"      →       RedisAgentStore(**config)
    config:                        ↓
      redis_url: "..."            store.create_background_run_manager()
                                  store.create_session_registry()
```

Each store implements factory methods (`create_background_run_manager()`, `create_session_registry()`) so `main.py` doesn't need backend-specific logic. File stores return `None` (use defaults); Redis stores return Redis-backed instances.

Available backends:

| Alias | Agent Store | Team Store | Tasks Provider |
|-------|------------|------------|---------------|
| `file` | `FileAgentStore` | `FileTeamStore` | `InMemoryTasksProvider` |
| `redis` | `RedisAgentStore` | `RedisTeamStore` | `RedisTasksProvider` |

Custom backends: use a dotted class path instead of an alias.

## Checkpoint System

The checkpoint system enables durable agent and team runs that survive server crashes. When checkpointing is enabled, the runner saves state to Redis before each LLM call. On recovery, another replica loads the checkpoint and resumes.

Key components:

- **`agents/checkpoint.py`**: `Checkpoint` model and `CheckpointStore` ABC with Redis implementation. Stores run state (messages, tool results, turn count) as JSON in Redis with TTL expiry.
- **`agents/checkpoint_utils.py`**: Shared helpers for checkpoint save/load/delete used by both `AgentRunner` and `TeamRunner`.
- **`agents/base_store.py`**: Generic base classes for agent and team stores, providing shared factory methods (`create_background_run_manager()`, `create_session_registry()`) and common CRUD patterns.

## Shared Route Helpers

Route modules share common utilities from `routes/_helpers.py`:

- **`require_principal()`**: extracts and validates the `AuthenticatedPrincipal` from the request context, returning 401 if missing.
- **`reconnect_event_generator()`**: builds an async generator that replays stored events from the event store and polls for new events, used by both agent and team SSE reconnection endpoints.
- **`@handle_provider_errors`**: decorator that converts `NotImplementedError` to 501 and `KeyError` to 404.

## File Organization

```
src/agentic_primitives_gateway/
├── main.py              # App, lifespan, error handlers, routers
├── middleware.py         # RequestContextMiddleware
├── config.py            # YAML config + Pydantic settings + store aliases
├── context.py           # Request-scoped contextvars
├── registry.py          # Provider loading + resolution
├── metrics.py           # MetricsProxy (Prometheus)
├── watcher.py           # Config hot-reload
├── agents/
│   ├── runner.py         # AgentRunner + _RunContext
│   ├── namespace.py      # Memory namespace resolution (per-user + shared pools)
│   ├── export.py         # Export agents/teams as standalone Python scripts
│   ├── store.py          # AgentStore ABC + FileAgentStore
│   ├── base_store.py     # Generic store base classes
│   ├── checkpoint.py     # Checkpoint model + CheckpointStore
│   ├── checkpoint_utils.py # Shared checkpoint save/load/delete helpers
│   ├── redis_store.py    # RedisAgentStore + RedisTeamStore
│   ├── session_registry.py # SessionRegistry ABC + InMemory/Redis
│   ├── team_runner.py    # TeamRunner
│   ├── team_store.py     # TeamStore ABC + FileTeamStore
│   ├── team_prompts.py   # Prompt builders for teams
│   ├── team_agent_loop.py# Generic LLM loop for team agents
│   └── tools/            # Tool catalog, handlers, delegation
├── credentials/          # Per-user credential resolution (OIDC)
│   ├── oidc.py          # Admin API + userinfo resolver
│   ├── middleware.py    # CredentialResolutionMiddleware
│   ├── cache.py         # In-memory LRU cache
│   └── writer/          # Keycloak Admin API writer
├── enforcement/          # Cedar policy enforcement
├── models/               # Pydantic models per primitive
├── primitives/
│   ├── tasks/
│   │   ├── in_memory.py  # InMemoryTasksProvider (dev)
│   │   └── redis.py      # RedisTasksProvider (multi-replica)
│   └── ...               # Other provider implementations
└── routes/
    ├── _background.py    # BackgroundRunManager, EventStore, RedisEventStore
    ├── _helpers.py       # @handle_provider_errors, require_principal, reconnect_event_generator
    ├── agents.py         # Agent CRUD, chat, sessions, export, reconnect, cancel
    ├── teams.py          # Team CRUD, runs, export, events, reconnect, cancel, task retry
    ├── credentials.py    # Credential read/write/delete/status
    ├── health.py         # Liveness, readiness, auth config, authenticated provider status
    └── ...               # Other routers
```
