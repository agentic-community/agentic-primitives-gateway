# Agentic Primitives Gateway

FastAPI service providing pluggable primitives (memory, observability, gateway, tools, identity, code_interpreter, browser) for AI agent infrastructure. Separate async Python client in `client/`.

## Project Structure

- `src/agentic_primitives_gateway/` — Server package
  - `main.py` — FastAPI app, RequestContextMiddleware, router registration
  - `config.py` — Pydantic-settings, YAML config loading with env var expansion
  - `registry.py` — Dynamic provider loading, per-request resolution via context
  - `context.py` — Request-scoped contextvars (AWS creds, service creds, provider overrides)
  - `metrics.py` — Prometheus MetricsProxy wrapping all providers
  - `models/` — Pydantic request/response models and StrEnum definitions (`enums.py`)
  - `primitives/` — Abstract base classes + backend implementations per primitive
  - `routes/` — FastAPI routers, one per primitive plus health
- `client/` — Separate `agentic-primitives-gateway-client` package (httpx-based, no server dependency)
- `tests/` — Server integration tests (pytest, async)
- `client/tests/` — Client unit tests
- `configs/` — YAML presets (local, agentcore, kitchen-sink, milvus-langfuse)
- `examples/` — Example agents (langchain, strands)
- `deploy/helm/` — Kubernetes Helm chart

## Architecture

Each primitive has an abstract base class (`primitives/*/base.py`) with multiple backend implementations (noop, in_memory, agentcore, mem0, langfuse, etc.). The registry dynamically loads provider classes via `importlib` at startup from config. Requests flow through middleware that extracts credentials and provider routing headers into contextvars, then routes call `registry.{primitive}` which resolves the correct backend.

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
# All server tests (55 tests)
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
- **Client is independent** — `client/` has no imports from the server package. It's a thin HTTP wrapper; validation happens server-side.

## Style

- Python 3.11+, `from __future__ import annotations` in every file
- Pydantic v2 models for all request/response types
- Async throughout (providers, routes, client)
- hatchling for packaging
- **Ruff** for linting + formatting, **mypy** for type checking (configured in root `pyproject.toml`)
- Pre-commit hooks enforce Ruff on every commit: `pre-commit install`
- `make lint` to check, `make format` to auto-fix, `make typecheck` for mypy
