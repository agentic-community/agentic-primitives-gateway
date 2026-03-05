# Agentic Primitives Gateway

FastAPI service providing pluggable primitives (memory, observability, gateway, tools, identity, code_interpreter, browser, policy, evaluations) for AI agent infrastructure. Includes a declarative agents subsystem that runs LLM tool-call loops server-side. Separate async Python client in `client/`.

## Project Structure

- `src/agentic_primitives_gateway/` — Server package
  - `main.py` — FastAPI app, RequestContextMiddleware, router registration
  - `config.py` — Pydantic-settings, YAML config loading with env var expansion
  - `registry.py` — Dynamic provider loading, per-request resolution via context
  - `context.py` — Request-scoped contextvars (AWS creds, service creds, provider overrides)
  - `metrics.py` — Prometheus MetricsProxy wrapping all providers
  - `models/` — Pydantic request/response models and StrEnum definitions (`enums.py`)
  - `primitives/` — Abstract base classes + backend implementations per primitive; `_sync.py` provides `SyncRunnerMixin` for executor-based async wrappers
  - `routes/` — FastAPI routers, one per primitive plus health and agents
  - `enforcement/` — Policy enforcement layer: `base.py` (PolicyEnforcer ABC), `noop.py` (default allow-all), `cedar.py` (local Cedar evaluation via cedarpy), `middleware.py` (Starlette middleware mapping requests to Cedar principals/actions/resources)
  - `agents/` — Declarative agent orchestration: `runner.py` (LLM tool-call loop), `tools.py` (tool registry), `store.py` (persistence)
- `client/` — Separate `agentic-primitives-gateway-client` package (httpx-based, no server dependency)
- `tests/` — Server integration tests (pytest, async)
- `client/tests/` — Client unit tests
- `configs/` — YAML presets (local, agentcore, kitchen-sink, milvus-langfuse, agents-agentcore, agents-mem0-langfuse, agents-mixed)
- `examples/` — Example agents (langchain, strands)
- `deploy/helm/` — Kubernetes Helm chart

## Architecture

Each primitive has an abstract base class (`primitives/*/base.py`) with multiple backend implementations (noop, in_memory, agentcore, mem0, langfuse, etc.). The registry dynamically loads provider classes via `importlib` at startup from config. Requests flow through middleware that extracts credentials and provider routing headers into contextvars, then routes call `registry.{primitive}` which resolves the correct backend.

The agents subsystem sits above the primitives. An agent spec (system prompt + model + enabled tools + hooks) defines a declarative agent. The `AgentRunner` runs the LLM tool-call loop using `registry.gateway.route_request()` for LLM calls and executes tool calls directly against primitives via the registry. Agent specs are stored in `FileAgentStore` (JSON persistence) and can be seeded from YAML config.

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
# All server tests (634 unit/system + 42 integration)
python -m pytest tests/ -v

# All client tests (30 tests)
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
- **Agents are NOT primitives** — They're a higher-level orchestration layer in `agents/` that composes primitives. Not registered in the provider registry.
- **Agent tool handlers** — `agents/tools.py` defines a static tool catalog with `functools.partial` to bind namespace/session_id so the LLM doesn't need to specify them.
- **BedrockConverseProvider** — `primitives/gateway/bedrock.py` translates between internal message format and Bedrock Converse API. Supports tool_use. Uses `SyncRunnerMixin` + `get_boto3_session()`.
- **SeleniumGridBrowserProvider** — `primitives/browser/selenium_grid.py` provides self-hosted browser automation via Selenium WebDriver.
- **DaytonaCodeInterpreterProvider** — `primitives/code_interpreter/daytona.py` provides sandboxed code execution via the Daytona SDK. Uses `SyncRunnerMixin`.
- **JupyterCodeInterpreterProvider** — `primitives/code_interpreter/jupyter.py` provides code execution via Jupyter Server or Enterprise Gateway. Uses WebSocket for execution and kernel-based file I/O (works without the Contents REST API).
- **AgentCorePolicyProvider** — `primitives/policy/agentcore.py` provides Cedar-based policy management via `bedrock-agentcore-control`. Supports engine CRUD, policy CRUD, and policy generation. Uses `SyncRunnerMixin` + `get_boto3_session()`.
- **AgentCoreEvaluationsProvider** — `primitives/evaluations/agentcore.py` provides LLM-as-a-judge evaluations via dual clients: `bedrock-agentcore-control` for evaluator CRUD and `bedrock-agentcore` for runtime evaluation. Uses `SyncRunnerMixin`.

## Style

- Python 3.11+, `from __future__ import annotations` in every file
- Pydantic v2 models for all request/response types
- Async throughout (providers, routes, client)
- hatchling for packaging
- **Ruff** for linting + formatting, **mypy** for type checking (configured in root `pyproject.toml`)
- Pre-commit hooks enforce Ruff on every commit: `pre-commit install`
- `make lint` to check, `make format` to auto-fix, `make typecheck` for mypy
