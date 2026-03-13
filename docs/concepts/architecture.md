# Architecture

## Overview

The gateway is a FastAPI service with three layers:

1. **Middleware** -- extracts credentials, routes providers, enforces policies
2. **Routes** -- one router per primitive + agents + teams
3. **Provider Registry** -- loads backend implementations, resolves per-request

```
Request → CORS → RequestContextMiddleware → AuthenticationMiddleware → PolicyEnforcementMiddleware → Route → Registry → Provider
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

3. PolicyEnforcementMiddleware (enforcement/middleware.py)
   - Maps path + method → Cedar action (e.g., "memory:store_memory")
   - Evaluates: permit(User::"alice", Action::"memory:store_memory", resource)?
   - 403 if denied

4. Route handler (routes/memory.py)
   - Calls registry.memory.store(namespace, key, content)

5. Registry (registry.py)
   - Reads provider override from contextvars: "mem0"
   - Returns the mem0 provider instance (wrapped in MetricsProxy)

6. Provider (primitives/memory/mem0_provider.py)
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

`AuthenticationMiddleware` runs after `RequestContextMiddleware` and before `PolicyEnforcementMiddleware`. It validates the incoming request's credentials -- JWT token, API key, or noop (dev mode) -- based on the configured `auth.backend`, and sets an `AuthenticatedPrincipal` in a contextvar. The principal carries `principal_id`, `principal_type`, `groups`, and `scopes`.

Route handlers check resource-level access via `require_access()` (verifies the caller can view/use a resource based on ownership or sharing) and `require_owner_or_admin()` (verifies the caller owns the resource or has admin scope). These helpers read the principal from the contextvar and raise 403 if access is denied.

For background tasks (agent runs, team runs), the principal flows from the originating request into the `asyncio.Task` via `copy_context()`, so authorization checks remain valid even after the HTTP connection closes.

## Agent Subsystem

Agents sit above primitives as an orchestration layer:

```
AgentRunner.run(spec, message)
  → _init_context()       # overrides, tools, memory context
  → while loop:
      → registry.gateway.route_request(...)  # LLM call
      → _exec_tools_parallel(...)            # tool execution
  → _finalize()           # cleanup, store turn, trace
```

Key design decisions:

- **`_RunContext` dataclass** holds all mutable state, shared between `run()` and `run_stream()`
- **Tool handlers** are bound with `functools.partial` to inject namespace/session_id
- **Agent-as-tool** delegation allows agents to call other agents (depth-limited)
- **Provider overrides** are saved/restored around sub-agent calls

## Team Subsystem

Teams add a coordination layer on top of agents:

```
TeamRunner.run(team_spec, message)
  → Phase 1: Planner decomposes into tasks
  → Phase 2: Workers execute tasks concurrently (with replanning)
  → Phase 3: Synthesizer combines results
```

See [Teams](teams.md) for the full replanning loop documentation.

## Background Run Infrastructure

Streaming endpoints decouple the run from the HTTP connection using `BackgroundRunManager` (`routes/_background.py`):

```
Client → SSE Response ← Queue ← asyncio.Task (background)
         (may disconnect)        (always completes, calls _finalize)
```

Components:

- **`BackgroundRunManager`** -- tracks active runs in a local dict. Optional `EventStore` persists events/status to Redis for cross-replica visibility.
- **`RedisEventStore`** -- stores run status and event logs in Redis lists with TTL auto-expiry.
- **`SessionRegistry`** -- tracks active browser/code_interpreter sessions. `InMemorySessionRegistry` (default) or `RedisSessionRegistry` (multi-replica). Used for observability and orphan cleanup.

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

- **`agents/checkpoint.py`** -- `Checkpoint` model and `CheckpointStore` ABC with Redis implementation. Stores run state (messages, tool results, turn count) as JSON in Redis with TTL expiry.
- **`agents/checkpoint_utils.py`** -- Shared helpers for checkpoint save/load/delete used by both `AgentRunner` and `TeamRunner`.
- **`agents/base_store.py`** -- Generic base classes for agent and team stores, providing shared factory methods (`create_background_run_manager()`, `create_session_registry()`) and common CRUD patterns.

## Shared Route Helpers

Route modules share common utilities from `routes/_helpers.py`:

- **`require_principal()`** -- extracts and validates the `AuthenticatedPrincipal` from the request context, returning 401 if missing.
- **`reconnect_event_generator()`** -- builds an async generator that replays stored events from the event store and polls for new events, used by both agent and team SSE reconnection endpoints.
- **`@handle_provider_errors`** -- decorator that converts `NotImplementedError` to 501 and `KeyError` to 404.

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
│   ├── namespace.py      # Knowledge namespace resolution
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
    ├── agents.py         # Agent CRUD, chat, sessions, reconnect, cancel
    ├── teams.py          # Team CRUD, runs, events, reconnect, cancel
    └── ...               # Other routers
```
