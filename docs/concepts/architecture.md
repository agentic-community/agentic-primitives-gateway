# Architecture

## Overview

The gateway is a FastAPI service with three layers:

1. **Middleware** -- extracts credentials, routes providers, enforces policies
2. **Routes** -- one router per primitive + agents + teams
3. **Provider Registry** -- loads backend implementations, resolves per-request

```
Request → RequestContextMiddleware → PolicyEnforcementMiddleware → Route → Registry → Provider
```

## Request Flow

```
Client sends: POST /api/v1/memory/my-ns
  Headers: X-AWS-Access-Key-Id, X-Provider-Memory: mem0

1. RequestContextMiddleware (middleware.py)
   - Extracts AWS credentials → contextvars
   - Extracts provider routing → contextvars
   - Generates request ID

2. PolicyEnforcementMiddleware (enforcement/middleware.py)
   - Maps path + method → Cedar action (e.g., "memory:store_memory")
   - Evaluates: permit(Agent::"anonymous", Action::"memory:store_memory", resource)?
   - 403 if denied

3. Route handler (routes/memory.py)
   - Calls registry.memory.store(namespace, key, content)

4. Registry (registry.py)
   - Reads provider override from contextvars: "mem0"
   - Returns the mem0 provider instance (wrapped in MetricsProxy)

5. Provider (primitives/memory/mem0_provider.py)
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

## File Organization

```
src/agentic_primitives_gateway/
├── main.py              # App, lifespan, error handlers, routers
├── middleware.py         # RequestContextMiddleware
├── config.py            # YAML config + Pydantic settings
├── context.py           # Request-scoped contextvars
├── registry.py          # Provider loading + resolution
├── metrics.py           # MetricsProxy (Prometheus)
├── watcher.py           # Config hot-reload
├── agents/
│   ├── runner.py         # AgentRunner + _RunContext
│   ├── namespace.py      # Knowledge namespace resolution
│   ├── store.py          # FileAgentStore
│   ├── team_runner.py    # TeamRunner
│   ├── team_prompts.py   # Prompt builders for teams
│   ├── team_agent_loop.py# Generic LLM loop for team agents
│   └── tools/            # Tool catalog, handlers, delegation
├── enforcement/          # Cedar policy enforcement
├── models/               # Pydantic models per primitive
├── primitives/           # Provider implementations
└── routes/               # FastAPI routers
```
