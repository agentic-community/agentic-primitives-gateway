# Agentic Primitives Gateway

FastAPI service providing pluggable primitives (memory, observability, llm, tools, identity, code_interpreter, browser, policy, evaluations) for AI agent infrastructure. Includes a declarative agents subsystem that runs LLM tool-call loops server-side. Separate async Python client in `client/`.

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
  - `routes/` — FastAPI routers, one per primitive plus health, agents, and credentials; `_helpers.py` provides `@handle_provider_errors` decorator and `require_principal()`
  - `enforcement/` — Policy enforcement layer: `base.py` (PolicyEnforcer ABC), `noop.py` (default allow-all), `cedar.py` (local Cedar evaluation via cedarpy), `middleware.py` (Starlette middleware mapping requests to Cedar principals/actions/resources)
  - `auth/` — Authentication subsystem: `base.py` (AuthBackend ABC), `models.py` (AuthenticatedPrincipal), `noop.py`, `api_key.py`, `jwt.py` (OIDC/JWKS), `middleware.py` (AuthenticationMiddleware), `access.py` (check_access, require_access)
  - `credentials/` — Per-user credential resolution subsystem: `base.py` (CredentialResolver ABC), `models.py` (ResolvedCredentials, APG_PREFIX), `noop.py` (default no-op), `oidc.py` (OIDC resolver with Admin API + userinfo fallback, convention-based apg.* mapping), `cache.py` (in-memory LRU), `middleware.py` (CredentialResolutionMiddleware), `writer/` (CredentialWriter ABC, `noop.py`, `keycloak.py` with Admin API + User Profile auto-declaration)
  - `audit/` — Governance event subsystem: `base.py` (AuditSink ABC), `models.py` (AuditEvent + AuditAction taxonomy), `router.py` (fan-out router with per-sink async queues + graceful shutdown), `emit.py` (`emit_audit_event()` helper), `middleware.py` (emits `http.request` events), `redaction.py` (metadata + log-line secret scrubbing), `log_formatter.py` (JSON formatter + sanitization filter), `sinks/{noop,stdout_json}.py` (Phase A; file/redis/observability sinks follow)
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
      - `catalog.py` — `ToolDefinition`, `_TOOL_CATALOG`, `build_tool_list`, `to_llm_tools`, `execute_tool`
      - `delegation.py` — Agent-as-tool delegation (`_build_agent_tools`, `MAX_AGENT_DEPTH`)
- `ui/` — React + Vite + TypeScript + Tailwind CSS web UI
  - `src/components/` — Reusable components (ChatMessage, ToolCallBlock, SubAgentBlock, ArtifactBlock, MemoryPanel, ToolsPanel, CollapsibleSection, etc.)
  - `src/pages/` — Dashboard, AgentList (CRUD + edit), AgentChat (streaming + sub-agents + session resume), TeamList, TeamRun (streaming + event replay + background resume), PolicyManager, PrimitiveExplorer, Settings
  - `src/hooks/` — Data fetching hooks built on generic `useFetch<T>`, `useAutoScroll`
  - `src/lib/` — Shared utilities (cn, theme with CODE_THEME/PROSE_CLASSES, SSE parser)
  - `src/api/` — API client + TypeScript types
  - Production build outputs to `src/agentic_primitives_gateway/static/`
  - FastAPI serves the built SPA at `/ui/` with client-side routing fallback
- `client/` — Separate `agentic-primitives-gateway-client` package (httpx-based, no server dependency)
- `tests/` — Server unit/system tests (pytest, async); 1800+ tests
- `client/tests/` — Client unit tests (100 tests)
- `configs/` — YAML presets: **quickstart** (Bedrock + in-memory), **agentcore** (all AWS managed), **selfhosted** (open-source backends), **production** (selfhosted + auth + policies), plus specialized configs (local, kitchen-sink, e2e variants)
- `examples/` — Example agents (langchain, strands)
- `deploy/helm/` — Kubernetes Helm chart

## Architecture

Each primitive has an abstract base class (`primitives/*/base.py`) with multiple backend implementations (noop, in_memory, agentcore, mem0, langfuse, etc.). The registry dynamically loads provider classes via `importlib` at startup from config. Requests flow through `RequestContextMiddleware` (in `middleware.py`) that extracts credentials and provider routing headers into contextvars, then through `AuthenticationMiddleware` (in `auth/middleware.py`) that validates credentials and sets `AuthenticatedPrincipal` in a contextvar, then through `CredentialResolutionMiddleware` (in `credentials/middleware.py`) that resolves per-user credentials from OIDC user attributes via the Admin API, then through `PolicyEnforcementMiddleware` for Cedar policy evaluation, then routes call `registry.{primitive}` which resolves the correct backend.

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
./run.sh                # quickstart (Bedrock + in-memory)
./run.sh agentcore      # all AWS managed
./run.sh selfhosted     # open-source backends (Milvus, Langfuse, Jupyter, Selenium)
./run.sh mixed     # both AgentCore + self-hosted + JWT auth + Cedar + credential resolution
```

## Test Commands

```bash
# All server tests (1800+ unit/system + integration)
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

- **StrEnum for fixed vocabularies** — `models/enums.py` defines Primitive, LogLevel, SessionStatus, TokenType, CodeLanguage, HealthStatus, ServerCredentialMode. Use enum members, not bare strings.
- **Provider pattern** — New backends implement the primitive's ABC, get registered in config YAML. Provider classes are referenced by fully-qualified dotted path.
- **Request-scoped context** — AWS credentials, service credentials (`X-Cred-{Service}-{Key}`), and provider overrides (`X-Provider-*`) are stored per-request in contextvars.
- **MetricsProxy** — All provider instances are wrapped transparently for Prometheus instrumentation.
- **Config normalization** — Single-provider format (`backend` + `config`) auto-converts to multi-provider format (`default` + `backends`).
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
- **BedrockConverseProvider** — `primitives/llm/bedrock.py` translates between internal message format and Bedrock Converse API. Supports tool_use and streaming via `converse_stream()`. Uses `SyncRunnerMixin` + `get_boto3_session()`.
- **SeleniumGridBrowserProvider** — `primitives/browser/selenium_grid.py` provides self-hosted browser automation via Selenium WebDriver.
- **JupyterCodeInterpreterProvider** — `primitives/code_interpreter/jupyter.py` provides code execution via Jupyter Server or Enterprise Gateway. Uses WebSocket for execution and kernel-based file I/O (works without the Contents REST API).
- **AgentCorePolicyProvider** — `primitives/policy/agentcore.py` provides Cedar-based policy management via `bedrock-agentcore-control`. Supports engine CRUD, policy CRUD, and policy generation. Uses `SyncRunnerMixin` + `get_boto3_session()`.
- **AgentCoreEvaluationsProvider** — `primitives/evaluations/agentcore.py` provides LLM-as-a-judge evaluations via dual clients: `bedrock-agentcore-control` for evaluator CRUD and `bedrock-agentcore` for runtime evaluation. Uses `SyncRunnerMixin`.
- **LangfuseEvaluationsProvider** — `primitives/evaluations/langfuse.py` provides evaluations via Langfuse score configs (evaluator CRUD) and scores (evaluate). Maps evaluators to Score Configs, evaluate to Scores. Delete archives instead of hard-deleting. Per-request credentials via `X-Cred-Langfuse-*`. Uses `SyncRunnerMixin` + `FernLangfuse` REST client.
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
- **Credentials is NOT a primitive** — `credentials/` is a separate subsystem (like `auth/`, `enforcement/`). `CredentialResolutionMiddleware` resolves per-user credentials from OIDC user attributes and populates the same contextvars that `X-Cred-*` headers populate. Providers work unchanged. Resolution priority: explicit headers → OIDC-resolved → server ambient. Default is `NoopCredentialResolver` (no per-user resolution).
- **Convention-based credential mapping** — All gateway credentials use `apg.{service}.{key}` naming (e.g. `apg.langfuse.public_key`). The OIDC resolver auto-discovers all `apg.*` attributes from the user and maps them to `service_credentials[service][key]`. No explicit attribute mapping config is needed.
- **ServerCredentialMode** — `allow_server_credentials` is a three-way enum (`never`/`fallback`/`always`).
- **Keycloak credential writer** — Uses the Admin REST API via a service account (requires `manage-users` + `manage-realm` roles). Auto-declares new `apg.*` attributes in Keycloak's User Profile config. Falls back to Account API if admin creds unavailable. Only reads/writes `apg.*` attributes.
- **Credential resolver Admin API mode** — When admin credentials are configured, the OIDC resolver reads user attributes directly from the Keycloak Admin API instead of userinfo. This bypasses the need for protocol mappers. Falls back to userinfo when admin creds are unavailable.
- **Audit is NOT a primitive** — `audit/` is a separate subsystem (like `auth/`, `enforcement/`, `credentials/`). A single `AuditRouter` fans `AuditEvent` records out to N pluggable `AuditSink`s with per-sink async queues + isolated failure semantics + graceful shutdown. `StdoutJsonSink` is always-on by default (k8s-native log shipping story). Call sites use `emit_audit_event(action, outcome, …)` which auto-fills `request_id` / `correlation_id` / actor fields from contextvars. `AUDIT_SINK_ALIASES` in `config.py` map short names to sink classes. Audit events and app logs are separate streams (stdout JSON sink writes directly, not through `logging`).
- **Audit at the ABC/proxy boundary, not per-provider** — primitive call audit lives at cross-cutting chokepoints, not duplicated in every provider. `MetricsProxy` in `metrics.py` emits a generic `provider.call` event for every wrapped primitive method (both coroutines and async generators) alongside its Prometheus metrics. LLM-specific enrichment (`llm.generate` + `gateway_llm_requests_total` + `gateway_llm_tokens_total`) is injected by `LLMProvider.__init_subclass__` via `primitives/llm/_audit.py`, which wraps `route_request` / `route_request_stream` on every subclass automatically. Provider implementations stay clean.
- **Correlation ID** — `correlation_id` contextvar in `context.py` is set by `RequestContextMiddleware` from the `x-correlation-id` header (falling back to `request_id`). Returned as a response header. Threaded across sub-agent + background runs to stitch multi-step workflows together in audit + logs.
- **JSON logs + sanitization** — `logging.format: json` enables `JsonLogFormatter` in `audit/log_formatter.py`; `logging.sanitize: true` (default) attaches `LogSanitizationFilter` that scrubs Bearer tokens, AWS access keys, JWTs, and `apg.*` key=value pairs from application log lines.
- **Audit UI viewer** — `/ui/audit` (admin-only) renders audit events with live tail + paginated history. Backed by `routes/audit.py` (`GET /api/v1/audit/{status,events,events/stream}`). Admin-gating comes from `GET /api/v1/auth/whoami`; React uses `AdminGuard` + sidebar conditional rendering. Empty state points at `docs/guides/observability.md` when no reader is configured.
- **Audit read/write split (AuditReader protocol)** — writing is `AuditSink` (every backend); reading is the optional `AuditReader` protocol in `audit/base.py` (`describe` / `count` / `list_events` / `tail`). `RedisStreamAuditSink` implements both; write-only sinks (stdout, file, observability) don't. `routes/audit.py` picks the first `isinstance(sink, AuditReader)` from `router.sinks` — backend-agnostic so a future `PostgresAuditSink` or `SQLiteAuditSink` plugs in without route changes. `tail()` yields `AuditEvent | None` (None = keepalive tick); the route translates these to SSE `data:` / `: keepalive` frames.
- **`audit_mutation` context manager** — `audit/emit.py` exports `audit_mutation(action, *, resource_type, resource_id, metadata)` as an async context manager for any mutation route. Emits one success event on clean exit, one failure event (with `metadata.error_type`) on exception, and captures `duration_ms` automatically. Handlers refine `audit.resource_id` / `audit.metadata` post-operation (e.g. setting a freshly-minted `version_id`). Use this for every CREATE/UPDATE/DELETE route to stay consistent with agents/teams/policy/credentials emits.
- **Router-level audit filter** — `audit.filter` in config (`AuditFilterConfig`) lets operators drop noisy events before fan-out: `exclude_actions` (exact action), `exclude_action_categories` (first `.`-segment), and `sample_rates` (per-action fractional keep rate). Dropped events increment `gateway_audit_events_dropped_total{sink="__router__",reason="filtered"}`. Wire-everything + filter-at-config is the default strategy so compliance-critical events (auth/policy/version/fork/credential) stay unfiltered while `provider.call` / `tool.call` / `memory.record.*` can be tamed per-deployment.
- **Versioned identities — `(owner_id, name)` is the primary key** — `agents/versioned_base.py` provides a generic `VersionedSpecStore[SpecT, VersionT]` with file + Redis impls in `versioned_agent_store.py` / `versioned_team_store.py`. Every mutation produces an immutable `AgentVersion` / `TeamVersion`; a single deployed pointer per identity is flipped atomically. Bare `/agents/{name}` resolves caller-ns → system (never shared); qualified `/agents/{owner}:{name}` is required for shared access. Sub-agent delegation resolves in the *parent agent's* namespace; team workers resolve in the *team's* namespace. Fork clones the deployed version into the caller's ns with `forked_from` lineage and auto-qualifies sub-refs against the source owner. Memory keys are owner-scoped so forks have isolated history.
- **Governance approval gate** — `governance.require_admin_approval_for_deploy: bool` (default off) flips new versions to `draft` + forces `POST /versions` → `/propose` → admin `/approve` → `/deploy`. PUT returns 409 under the gate. YAML-seeded specs always bypass on first load.

## Style

- Python 3.11+, `from __future__ import annotations` in every file
- Pydantic v2 models for all request/response types
- Async throughout (providers, routes, client)
- hatchling for packaging
- **Ruff** for linting + formatting, **mypy** for type checking (configured in root `pyproject.toml`)
- Pre-commit hooks enforce Ruff on every commit: `pre-commit install`
- `make lint` to check, `make format` to auto-fix, `make typecheck` for mypy
- `auth/` follows the same subsystem patterns as `enforcement/` (ABC + pluggable backends + middleware)

## Security Principles

This project follows a **security-first** design. Every feature must maintain these properties.

### Authentication & Authorization

- **All routes require authentication** unless explicitly exempt. New routers MUST use `dependencies=[Depends(require_principal)]`. New endpoints MUST call `require_principal()` or use a router-level dependency.
- **Exempt paths are explicit and minimal.** The only exempt paths are health probes (`/healthz`, `/readyz`), metrics (`/metrics`), OpenAPI docs (`/docs`, `/redoc`, `/openapi.json`), auth config (`/auth/config`), UI static files (`/ui`), and A2A discovery (`/.well-known/agent.json`). Adding a new exempt path requires justification.
- **Noop auth grants admin access** — it exists only for local development. Production deployments MUST use `jwt` or `api_key` auth backends.
- **Cedar enforcement is default-deny** when active. No loaded policies = all requests blocked.
- **Resource ownership** is enforced at the route level. Agents and teams have `owner_id` + `shared_with`. Mutations (update, delete) require `require_owner_or_admin()`. Read access uses `require_access()` which checks ownership, admin scope, sharing, and group membership.
- **Session ownership** is tracked for browser and code_interpreter sessions. Only the creator (or admin) can interact with a session via `SessionOwnershipStore.require_owner()`.

### Multi-Tenant by Design

- **Request-scoped credential isolation.** AWS credentials, service credentials, provider routing, and authenticated principals are stored in Python `contextvars` (`context.py`). No shared mutable state between requests, even under concurrent load.
- **User-scoped memory.** Declarative agents always append `:u:{user_id}` to memory namespaces and actor IDs via `resolve_knowledge_namespace()` and `resolve_actor_id()`. Two users on the same agent have fully isolated memory and conversation history.
- **Per-user credential resolution.** When OIDC credential resolution is enabled, each user's backend credentials (Langfuse keys, MCP tokens) are read from their OIDC attributes — not shared server credentials.
- **Credential pass-through.** The gateway does not use shared service credentials by default. `allow_server_credentials` is a three-way enum (`never`/`fallback`/`always`) — `never` is the most secure, `fallback` allows server creds when per-user creds are unavailable.

### Multi-Replica Capable

- **No local-only state in production.** File-backed stores (`FileAgentStore`, `FileTeamStore`) are dev-only. Production uses Redis-backed stores for agents, teams, tasks, sessions, events, and checkpoints.
- **Startup safety guard.** `_warn_replica_unsafe_config()` warns when Redis is enabled for some stores but local-only backends are used for others.
- **Atomic operations.** `HSETNX` for agent/team creation prevents race conditions. Distributed locking (`SET NX`) for checkpoint recovery prevents multiple replicas claiming the same orphan.
- **Cross-replica visibility.** Session ownership, background run events, and run status are persisted to Redis when configured.

### Defense in Depth

- **Layered middleware stack** (outermost to innermost): CORS → RequestContext → Audit → Auth → CredentialResolution → PolicyEnforcement → route handler. Each layer enforces a different security boundary.
- **Auth validates identity.** Enforcement evaluates authorization. Ownership checks guard individual resources. All three must pass.
- **Frozen principal.** `AuthenticatedPrincipal` is a frozen dataclass — once set by auth middleware, it cannot be mutated downstream.
- **Audit trail.** Every authentication attempt, policy decision, credential resolution, resource access denial, and session lifecycle event is logged with request/correlation IDs for forensic tracing. Audit events include `AUTH_SUCCESS`, `AUTH_FAILURE`, `POLICY_ALLOW`, `POLICY_DENY`, `CREDENTIAL_RESOLVE`, `RESOURCE_ACCESS_DENIED`, etc.
- **Log sanitization.** The `LogSanitizationFilter` and audit redaction system strip credentials, tokens, and secrets from log output. Credential-bearing headers (`X-AWS-Secret-Access-Key`, `X-Cred-*`) are never logged in plaintext.

### Build & Supply Chain Security

- **Pinned GitHub Actions.** All CI actions use commit SHAs, not mutable tags (e.g., `actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5` not `actions/checkout@v4`).
- **Secret scanning.** TruffleHog runs on every commit via pre-commit hook (`--only-verified --fail`) and in CI. Accidentally committed secrets are scrubbed with `git filter-repo`.
- **Dependency version constraints.** All dependencies in `pyproject.toml` have minimum version pins (`>=`). Upper bounds are set where breaking API changes are known (e.g., `langfuse>=3.0.0,<4.0.0`).
- **Docker image attestation.** The build workflow uses `actions/attest-build-provenance` to generate SLSA provenance for published images.
- **Workflow permissions.** CI workflows set explicit `permissions` blocks — `contents: read` by default, elevated only where needed.

### Secure Coding Practices

- **Input validation at system boundaries.** All request/response types are Pydantic models. FastAPI validates input before route handlers execute. Internal code trusts validated types.
- **No credential storage in provider instances.** Providers resolve credentials per-request via `get_service_credentials_or_defaults()` or `get_boto3_session()` from contextvars. Provider `__init__` may store config-level defaults but never per-user secrets.
- **Path traversal prevention.** Static file serving uses `resolve()` + `startswith()` containment checks. User-controlled paths are validated before filesystem access.
- **Exception handlers don't leak internals.** The global exception handler returns generic error messages. Stack traces go to server logs (with sanitization), not to clients.
- **Thread-safe async bridges.** `SyncRunnerMixin` provides `_run_sync()` for wrapping blocking SDK calls in `loop.run_in_executor()`. Sync client wrappers use dedicated `httpx.Client` instances (not `asyncio.new_event_loop()` bridges that conflict with framework event loops).

## Web UI

React + Vite SPA served at `/ui/`. Supports OIDC authentication via `oidc-client-ts` (Authorization Code + PKCE); unauthenticated mode when auth is disabled. Pages: Dashboard (health, providers, agents), Agent List (CRUD with inline edit), Agent Chat (token-streaming, sub-agent activity, tool artifacts, session resume with polling, multi-session picker), Team List (CRUD), Team Run (streaming task board, event replay on reconnect, background run indicator, multi-run picker), Policy Manager, Primitive Explorer, Settings (per-user credential management). Shared `useFetch<T>` hook, `useAutoScroll` hook, `CollapsibleSection` component, `sseStream()` fetch wrapper, and centralized theme constants (`CODE_THEME`, `PROSE_CLASSES`) reduce duplication.

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

## Post-Task Checklist

After completing ANY task or feature, verify ALL of these before considering it done:

### CI Must Pass
1. `make format` — auto-fix lint/format issues
2. `make lint` — verify no remaining issues
3. `make typecheck` — mypy must pass with zero errors
4. `python -m pytest tests/ -v` — all server tests pass
5. `cd client && python -m pytest tests/ -v` — all client tests pass

### Client Must Match Server
6. Any new server routes must have corresponding client methods in `client/src/agentic_primitives_gateway_client/client.py`
7. New client methods must have tests in `client/tests/`

### Test Coverage Must Stay High
8. Server coverage target: 85%+ (enforced by CI via `--cov-fail-under=85`)
9. All new code paths must have unit tests — providers, routes, translation functions, edge cases
10. Integration tests for providers that talk to external services

### Security Must Be Maintained
11. New routes MUST have authentication (`Depends(require_principal)`) — add a test that verifies 401 without credentials
12. New resources MUST have ownership checks (`require_access` for reads, `require_owner_or_admin` for mutations)
13. No hardcoded secrets, credentials, or tokens in code or config defaults — use `${ENV_VAR:=}` patterns
14. Credential-bearing data must not appear in logs — verify audit redaction covers new fields
15. Providers must resolve credentials per-request from context, not store them in instance state

### Documentation Must Be Updated
16. **`README.md`** — update architecture diagram AND primitives table if providers/features changed
17. **`docs/`** — update relevant mkdocs pages (api/, concepts/, guides/) for any new or changed functionality
18. **`CLAUDE.md`** — add one-line reference for new providers/patterns in Key Patterns section
19. **Examples** — update or add examples that demonstrate the feature

## Adding a New Provider

When adding a new provider to any primitive, update ALL of these:

1. Provider implementation in `src/agentic_primitives_gateway/primitives/<primitive>/<name>.py`
   - Resolve credentials per-request via `get_service_credentials_or_defaults()` or `get_boto3_session()` — never store per-user secrets in `__init__`
   - Use `SyncRunnerMixin` for blocking SDK calls
2. Unit tests in `tests/unit/primitives/<primitive>/test_<name>.py`
3. Integration tests in `tests/integration/<primitive>/test_<name>.py`
4. `pyproject.toml` — optional deps group if new packages, pin minimum version
5. `configs/kitchen-sink.yaml` — add backend entry
6. `CLAUDE.md` — add one-line provider reference in Key Patterns
7. `README.md` — update architecture diagram AND primitives table
8. `docs/` — update relevant API reference and primitive guide
9. Any relevant config YAML presets
10. Any relevant example agents
11. Update the client if new functionality is exposed
