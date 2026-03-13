# Agentic Primitives Gateway

FastAPI service providing pluggable primitives (memory, observability, gateway, tools, identity, code_interpreter, browser, policy, evaluations) for AI agent infrastructure. Includes a declarative agents subsystem that runs LLM tool-call loops server-side. Separate async Python client in `client/`.

## Project Structure

- `src/agentic_primitives_gateway/` — Server package
  - `main.py` — FastAPI app creation, lifespan, error handlers, router registration, UI serving
  - `middleware.py` — `RequestContextMiddleware` (extracts AWS creds, service creds, provider routing from headers)
  - `config.py` — Pydantic-settings, YAML config loading with env var expansion
  - `registry.py` — Dynamic provider loading, per-request resolution via context
  - `context.py` — Request-scoped contextvars (AWS creds, service creds, provider overrides)
  - `metrics.py` — Prometheus MetricsProxy wrapping all providers
  - `watcher.py` — Config file hot-reload watcher
  - `models/` — Pydantic request/response models and StrEnum definitions (`enums.py`)
  - `primitives/` — Abstract base classes + backend implementations per primitive; `_sync.py` provides `SyncRunnerMixin` for executor-based async wrappers
  - `routes/` — FastAPI routers, one per primitive plus health and agents; `_helpers.py` provides `@handle_provider_errors` decorator and `require_principal()`
  - `enforcement/` — Policy enforcement layer: `base.py` (PolicyEnforcer ABC), `noop.py` (default allow-all), `cedar.py` (local Cedar evaluation via cedarpy), `middleware.py` (Starlette middleware mapping requests to Cedar principals/actions/resources)
  - `auth/` — Authentication subsystem: `base.py` (AuthBackend ABC), `models.py` (AuthenticatedPrincipal), `noop.py`, `api_key.py`, `jwt.py` (OIDC/JWKS), `middleware.py` (AuthenticationMiddleware), `access.py` (check_access, require_access)
  - `routes/_background.py` — `BackgroundRunManager` (asyncio.Task + Queue decoupling), `EventStore` ABC, `RedisEventStore`, `sse_response()` helper, `reconnect_event_generator()` for SSE reconnection
  - `agents/` — Declarative agent orchestration
    - `runner.py` — `AgentRunner` with `_RunContext` dataclass; `run()` (non-streaming) and `run_stream()` (SSE) share init/request/finalize via helpers
    - `store.py` — `AgentStore` ABC + `FileAgentStore` (JSON persistence, YAML seed with overwrite)
    - `redis_store.py` — `RedisAgentStore` + `RedisTeamStore` (Redis hash-backed, optional)
    - `session_registry.py` — `SessionRegistry` ABC + `InMemorySessionRegistry` + `RedisSessionRegistry`
    - `namespace.py` — Shared namespace resolution for agent memory (knowledge vs session scoping)
    - `team_runner.py` — `TeamRunner` orchestrates multi-agent team execution (plan → execute → synthesize)
    - `team_store.py` — `TeamStore` ABC + `FileTeamStore`
    - `team_agent_loop.py` — `run_agent_with_tools()` and `run_agent_with_tools_stream()` with `invocation_id` tracking for per-invocation token attribution
    - `base_store.py` — Generic `SpecStore[T]`, `FileSpecStore[T]`, `RedisSpecStore[T]` base classes; agent/team stores inherit from these
    - `checkpoint.py` — `CheckpointStore` ABC, `RedisCheckpointStore`, `ReplicaHeartbeat` (heartbeat + orphan scanning), `recover_orphaned_runs()`, `_recovery_tasks` tracking
    - `checkpoint_utils.py` — `serialize_auth_context()`, `restore_auth_context()`, `apply_provider_overrides()`, `restore_provider_overrides()` — shared between AgentRunner and TeamRunner
    - `tools/` — Tool system package
      - `handlers.py` — Handler functions per primitive (memory, browser, code_interpreter, tools, identity)
      - `catalog.py` — `ToolDefinition`, `_TOOL_CATALOG`, `build_tool_list`, `to_gateway_tools`, `execute_tool`
      - `delegation.py` — Agent-as-tool delegation (`_build_agent_tools`, `MAX_AGENT_DEPTH`)
- `ui/` — React + Vite + TypeScript + Tailwind CSS web UI
  - `src/components/` — Reusable components (ChatMessage, ToolCallBlock, SubAgentBlock, ArtifactBlock, MemoryPanel, ToolsPanel, CollapsibleSection, etc.)
  - `src/pages/` — Dashboard, AgentList (CRUD + edit), AgentChat (streaming + sub-agents + session resume), TeamList, TeamRun (streaming + event replay + background resume), PolicyManager, PrimitiveExplorer
  - `src/hooks/` — Data fetching hooks built on generic `useFetch<T>`, `useAutoScroll`
  - `src/lib/` — Shared utilities (cn, theme with CODE_THEME/PROSE_CLASSES, SSE parser)
  - `src/api/` — API client + TypeScript types
  - Production build outputs to `src/agentic_primitives_gateway/static/`
  - FastAPI serves the built SPA at `/ui/` with client-side routing fallback
- `client/` — Separate `agentic-primitives-gateway-client` package (httpx-based, no server dependency)
- `tests/` — Server unit/system tests (pytest, async); 1350+ tests
- `client/tests/` — Client unit tests (100 tests)
- `configs/` — YAML presets (local, local-jwt, agentcore, agentcore-redis, kitchen-sink, milvus-langfuse, agents-agentcore, agents-mem0-langfuse, agents-mixed)
- `examples/` — Example agents (langchain, strands)
- `deploy/helm/` — Kubernetes Helm chart

## Architecture

Each primitive has an abstract base class (`primitives/*/base.py`) with multiple backend implementations (noop, in_memory, agentcore, mem0, langfuse, etc.). The registry dynamically loads provider classes via `importlib` at startup from config. Requests flow through `RequestContextMiddleware` (in `middleware.py`) that extracts credentials and provider routing headers into contextvars, then through `AuthenticationMiddleware` (in `auth/middleware.py`) that validates credentials and sets `AuthenticatedPrincipal` in a contextvar, then through `PolicyEnforcementMiddleware` for Cedar policy evaluation, then routes call `registry.{primitive}` which resolves the correct backend.

The agents subsystem sits above the primitives. An agent spec (system prompt + model + enabled tools + hooks) defines a declarative agent. The `AgentRunner` (in `agents/runner.py`) uses a `_RunContext` dataclass to share state across phases. `run()` and `run_stream()` share initialization, request building, session management, and finalization — only LLM calling and tool execution differ. Streaming uses SSE with token-by-token delivery and real-time sub-agent event forwarding. Tool calls within a turn execute in parallel via `asyncio.gather` (non-streaming) or `asyncio.Queue` (streaming). Agents can delegate to other agents as tools (agent-as-tool pattern) with configurable depth limiting (`MAX_AGENT_DEPTH=3`). Agent specs are stored in `FileAgentStore` (JSON persistence) or `RedisAgentStore` (Redis hash) and seeded from YAML config on startup (config overwrites existing agents).

The teams subsystem (`agents/team_runner.py`) orchestrates multi-agent execution: a planner decomposes requests into tasks, workers claim and execute them in parallel, a re-planner evaluates results and creates follow-ups, and a synthesizer produces a final response. Task boards are managed by the tasks primitive (`InMemoryTasksProvider` for dev, `RedisTasksProvider` for multi-replica with atomic Lua-scripted claiming).

Streaming endpoints use `BackgroundRunManager` (`routes/_background.py`) to decouple runs from HTTP connections. The runner executes in an `asyncio.Task` that feeds events into a queue; the SSE response reads from the queue. If the client disconnects, the task completes independently and stores the result. An optional `RedisEventStore` persists events and status to Redis for cross-replica visibility. `SessionRegistry` tracks active browser/code_interpreter sessions for observability and orphan cleanup.

## Build & Run

```bash
# Server
pip install -e ".[dev]"
python -m pytest tests/ -v

# Client (separate package)
cd client && pip install -e ".[dev]"
python -m pytest tests/ -v

# Run locally
./run.sh local
# or: AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE=configs/local.yaml uvicorn agentic_primitives_gateway.main:app --reload
```

## Test Commands

```bash
# All server tests (1350+ unit/system + integration)
python -m pytest tests/ -v

# All client tests (100 tests)
cd client && python -m pytest tests/ -v
```

## Lint & Format

```bash
make lint          # Check for lint/format errors (no auto-fix)
make format        # Auto-fix lint issues and reformat
make typecheck     # Run mypy type checker
make check         # Full check: lint + typecheck + tests

pre-commit install         # One-time: install git hooks
pre-commit run --all-files # Run all hooks on entire repo
```

## Key Patterns

- **StrEnum for fixed vocabularies** — `models/enums.py` defines Primitive, LogLevel, SessionStatus, TokenType, CodeLanguage, HealthStatus. Use enum members, not bare strings.
- **Provider pattern** — New backends implement the primitive's ABC, get registered in config YAML. Provider classes are referenced by fully-qualified dotted path.
- **Request-scoped context** — AWS credentials, service credentials (`X-Cred-{Service}-{Key}`), and provider overrides (`X-Provider-*`) are stored per-request in contextvars.
- **MetricsProxy** — All provider instances are wrapped transparently for Prometheus instrumentation.
- **Config normalization** — Legacy single-provider format (`backend` + `config`) auto-converts to multi-provider format (`default` + `backends`).
- **SyncRunnerMixin** — `primitives/_sync.py` provides a shared `_run_sync` method. All providers wrapping synchronous client libraries inherit from it instead of duplicating the executor boilerplate.
- **Client is independent** — `client/` has no imports from the server package. It's a thin HTTP wrapper; validation happens server-side.
- **Enforcement is NOT a primitive** — `enforcement/` is a separate subsystem (like `agents/`) that evaluates requests against policies at the middleware level. `PolicyEnforcementMiddleware` maps requests to Cedar principals/actions/resources and delegates to a `PolicyEnforcer` implementation. Default is `NoopPolicyEnforcer` (all allowed). `CedarPolicyEnforcer` uses `cedarpy` for local evaluation with background policy refresh from `registry.policy`. Default-deny when Cedar is active: no loaded policies = all denied.
- **Authentication is NOT a primitive** — `auth/` is a separate subsystem (like `enforcement/`). `AuthenticationMiddleware` validates credentials (JWT, API key, or noop) and sets `AuthenticatedPrincipal` in a contextvar. Routes call `require_access()` / `require_owner_or_admin()` for resource-level checks. Auth config is in `Settings.auth` with pluggable backends via `AUTH_BACKEND_ALIASES`.
- **Resource ownership** — `AgentSpec` and `TeamSpec` have `owner_id` and `shared_with` fields. `shared_with: []` = private (default), `shared_with: ["*"]` = all authenticated users. Config-seeded resources get `["*"]` injected by seed functions. `list_for_user(principal)` on stores filters by ownership/groups.
- **User-scoped memory** — Both knowledge namespace and conversation history include `:u:{user_id}` via `resolve_knowledge_namespace(spec, principal)` and `resolve_actor_id(agent_name, principal)`. Two users on the same agent have fully isolated memory.
- **Noop auth = admin** — `NoopAuthBackend` returns a principal with `scopes={"admin"}`. Dev mode gets full access. API key/JWT backends return 401 for missing credentials.
- **UI OIDC** — React SPA uses `oidc-client-ts` for Authorization Code + PKCE flow. `GET /auth/config` (exempt from auth) provides OIDC settings. `setApiAuthToken()` injects Bearer token into all API calls.
- **Agents are NOT primitives** — They're a higher-level orchestration layer in `agents/` that composes primitives. Not registered in the provider registry.
- **Agent tool system** — `agents/tools/` is a package: `handlers.py` (primitive handler functions), `catalog.py` (ToolDefinition + _TOOL_CATALOG + builder/executor), `delegation.py` (agent-as-tool with depth limiting). `functools.partial` binds namespace/session_id so the LLM doesn't specify them.
- **Agent-as-tool delegation** — Agents can call other agents as tools. A coordinator agent with `primitives.agents.tools: ["researcher", "coder"]` gets `call_researcher` and `call_coder` tools. Sub-agent runs are full `run()`/`run_stream()` calls with depth tracking. `MAX_AGENT_DEPTH=3` prevents infinite recursion.
- **Streaming** — `run_stream()` yields SSE events (`token`, `tool_call_start`, `tool_call_result`, `sub_agent_token`, `sub_agent_tool`, `done`). Bedrock streaming uses `converse_stream()` with an `asyncio.Queue` bridge for async iteration. Sub-agent events are forwarded to the parent stream in real-time.
- **Provider overrides in runner** — `_apply_overrides`/`_restore_overrides` save and restore the parent's provider overrides around sub-agent execution so each agent uses its own configured providers.
- **Knowledge vs session namespace** — `agents/namespace.py` provides `resolve_knowledge_namespace()` which strips `{session_id}` from the template. Memory tools use the agent-scoped namespace; conversation history uses `(actor_id, session_id)` directly.
- **Route error handling** — `routes/_helpers.py` provides `@handle_provider_errors(detail, not_found=)` decorator to convert `NotImplementedError` → 501 and `KeyError` → 404. Used on ~31 endpoints; endpoints with Pydantic request bodies use manual try/except (FastAPI signature inspection limitation). Also provides `require_principal()` extracted from duplicated `_principal()` functions in agents/teams routes.
- **BedrockConverseProvider** — `primitives/gateway/bedrock.py` translates between internal message format and Bedrock Converse API. Supports tool_use and streaming via `converse_stream()`. Uses `SyncRunnerMixin` + `get_boto3_session()`.
- **SeleniumGridBrowserProvider** — `primitives/browser/selenium_grid.py` provides self-hosted browser automation via Selenium WebDriver.
- **JupyterCodeInterpreterProvider** — `primitives/code_interpreter/jupyter.py` provides code execution via Jupyter Server or Enterprise Gateway. Uses WebSocket for execution and kernel-based file I/O (works without the Contents REST API).
- **AgentCorePolicyProvider** — `primitives/policy/agentcore.py` provides Cedar-based policy management via `bedrock-agentcore-control`. Supports engine CRUD, policy CRUD, and policy generation. Uses `SyncRunnerMixin` + `get_boto3_session()`.
- **AgentCoreEvaluationsProvider** — `primitives/evaluations/agentcore.py` provides LLM-as-a-judge evaluations via dual clients: `bedrock-agentcore-control` for evaluator CRUD and `bedrock-agentcore` for runtime evaluation. Uses `SyncRunnerMixin`.
- **Background run manager** — `routes/_background.py` provides `BackgroundRunManager` which decouples streaming runs from HTTP connections via `asyncio.Task` + `Queue`. Runs complete even if the client disconnects. Optional `EventStore` (e.g. `RedisEventStore`) persists events/status to Redis for cross-replica visibility. `sse_response()` helper creates `StreamingResponse` from a queue. `reconnect_event_generator()` replays stored events for SSE reconnection.
- **Redis stores** — `agents/redis_store.py` provides `RedisAgentStore` and `RedisTeamStore` (inheriting from generic `RedisSpecStore[T]` in `base_store.py`) with Redis hash storage. `primitives/tasks/redis.py` provides `RedisTasksProvider` with atomic Lua-scripted `claim_task`, `update_task`, and `add_note`. All Redis is optional — enabled via `store.backend: redis` in config.
- **Session registry** — `agents/session_registry.py` provides `SessionRegistry` ABC with `InMemorySessionRegistry` (default) and `RedisSessionRegistry`. Tracks active browser/code_interpreter sessions for observability and orphan cleanup. Injected into `AgentRunner` and `TeamRunner` via `set_session_registry()`.
- **Multi-session/run support** — Agents support multiple conversation sessions per agent; teams support multiple runs per team. `GET /agents/{name}/sessions` lists sessions, `GET /teams/{name}/runs` lists runs. Sessions/runs can be deleted via `DELETE` endpoints.
- **Pluggable store backends** — `AgentsConfig` and `TeamsConfig` have a `store` field with `backend` (alias or dotted path) and `config` (kwargs). Aliases: `file` → `FileAgentStore`, `redis` → `RedisAgentStore`. Custom backends can also be specified by dotted path. Stores implement factory methods `create_background_run_manager()` and `create_session_registry()` so `main.py` doesn't need backend-specific logic. `AGENT_STORE_ALIASES` and `TEAM_STORE_ALIASES` in `config.py` map short names to classes.
- **Generic store base classes** — `agents/base_store.py` provides `SpecStore[T]` ABC, `FileSpecStore[T]`, and `RedisSpecStore[T]` generic implementations. `AgentStore`/`TeamStore` and their File/Redis variants inherit from these, eliminating duplicated CRUD, seed, and list_for_user logic. TypeVar `T` is bound to Pydantic `BaseModel`.
- **Checkpointing** — `agents/checkpoint.py` provides `CheckpointStore` ABC and `RedisCheckpointStore` for durable run persistence. `ReplicaHeartbeat` refreshes a TTL key every 15s and scans for orphaned checkpoints every 60s. `recover_orphaned_runs()` uses distributed locking (`SET NX`) with shuffled order so multiple replicas don't all claim the same checkpoints. Checkpoints store full `_RunContext` + credentials (via `serialize_auth_context()`). `checkpointing_enabled: bool` on specs controls opt-in.
- **Shared checkpoint utilities** — `agents/checkpoint_utils.py` provides `serialize_auth_context()` and `restore_auth_context()` to capture/restore the authenticated principal + AWS credentials + service credentials during checkpoint save/resume. Also provides `apply_provider_overrides()` and `restore_provider_overrides()` used by both runners.
- **Cooperative cancellation** — Team runs use `asyncio.Event` per run (`_cancel_events` dict) checked at every turn boundary and before each tool execution in `team_agent_loop.py`. Agent runs use `BackgroundRunManager.cancel()` + `cancel_recovery_task()` for both local and recovered runs. Cancel endpoints also soft-cancel via Redis: mark tasks as failed, delete checkpoint, set status to cancelled.
- **SSE reconnection** — `routes/_background.py` provides `reconnect_event_generator()` used by both agent and team reconnect endpoints. Replays stored events from `EventStore`, polls every 0.2s for new events, throttles token-type events with 5ms delays for smooth replay. Closes on `done`/`cancelled` events or after seeing running→idle transition.
- **Partial token recovery on resume** — On checkpoint resume, `AgentRunner._recover_partial_tokens()` reads token events from the Redis event store and injects them as a `[RESUME CONTEXT]` system prompt hint so the model continues from where it left off. For teams, `run_agent_with_tools_stream()` emits `invocation_id` per call and `invocation_start` events, allowing `TeamRunner._recover_partial_tokens()` to filter tokens by specific agent invocation.
- **Shared route helpers** — `routes/_helpers.py` provides `require_principal()` (extracted from duplicated `_principal()` functions in agents/teams routes) and `@handle_provider_errors` decorator. `routes/_background.py` provides `reconnect_event_generator()` shared by both SSE reconnect endpoints.

## Style

- Python 3.11+, `from __future__ import annotations` in every file
- Pydantic v2 models for all request/response types
- Async throughout (providers, routes, client)
- hatchling for packaging
- **Ruff** for linting + formatting, **mypy** for type checking (configured in root `pyproject.toml`)
- Pre-commit hooks enforce Ruff on every commit: `pre-commit install`
- `make lint` to check, `make format` to auto-fix, `make typecheck` for mypy
- `auth/` follows the same subsystem patterns as `enforcement/` (ABC + pluggable backends + middleware)

## Web UI

React + Vite SPA served at `/ui/`. Supports OIDC authentication via `oidc-client-ts` (Authorization Code + PKCE); unauthenticated mode when auth is disabled. Pages: Dashboard (health, providers, agents), Agent List (CRUD with inline edit), Agent Chat (token-streaming, sub-agent activity, tool artifacts, session resume with polling, multi-session picker), Team List (CRUD), Team Run (streaming task board, event replay on reconnect, background run indicator, multi-run picker), Policy Manager, Primitive Explorer. Shared `useFetch<T>` hook, `useAutoScroll` hook, `CollapsibleSection` component, `sseStream()` fetch wrapper, and centralized theme constants (`CODE_THEME`, `PROSE_CLASSES`) reduce duplication.

```bash
# Development (hot reload, proxies API to :8000)
cd ui && npm install && npm run dev   # http://localhost:5173/ui/

# Production build (served by FastAPI)
cd ui && npm run build                # http://localhost:8000/ui/

# Makefile shortcuts
make ui-install    # npm install
make ui-dev        # vite dev server
make ui-build      # production build
make ui-clean      # remove build artifacts and node_modules
```
