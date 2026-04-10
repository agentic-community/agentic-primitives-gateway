# Primitives

The nine infrastructure primitives that recur across every agent system.

## Overview

| Primitive | What it does | Backends |
|-----------|-------------|----------|
| **Memory** | Key-value storage, semantic search, conversation events, sessions, branches | Noop, InMemory, Mem0+Milvus, AgentCore |
| **Identity** | OAuth2 tokens, API keys, workload identity, credential providers | Noop, AgentCore, Keycloak, Entra, Okta |
| **Code Interpreter** | Sandboxed code execution with session management | Noop, AgentCore, Jupyter |
| **Browser** | Remote browser automation (navigate, click, type, screenshot) | Noop, AgentCore, Selenium Grid |
| **Observability** | Traces, logs, LLM generation tracking, scoring, sessions | Noop, Langfuse, AgentCore |
| **LLM** | LLM request routing with tool_use support and streaming | Noop, Bedrock Converse |
| **Tools** | External tool registration, invocation, search, MCP servers | Noop, AgentCore, MCP Registry |
| **Policy** | Cedar policy engine and policy CRUD, optional generation | Noop, AgentCore |
| **Evaluations** | LLM-as-a-judge evaluators, online eval configs | Noop, AgentCore |
| **Tasks** | Shared task board for team coordination | Noop, InMemory, Redis |

## How Providers Work

Each primitive follows the same pattern:

1. **Abstract base class** defines the interface (e.g., `MemoryProvider`)
2. **Backend implementations** fulfill the interface (e.g., `Mem0MemoryProvider`)
3. **Registry** loads and resolves providers at runtime from config
4. **MetricsProxy** wraps every provider transparently for Prometheus metrics

```python
# All memory providers implement:
class MemoryProvider(ABC):
    async def store(self, namespace, key, content, metadata=None) -> MemoryRecord: ...
    async def retrieve(self, namespace, key) -> MemoryRecord | None: ...
    async def search(self, namespace, query, top_k=10) -> list[SearchResult]: ...
    async def delete(self, namespace, key) -> bool: ...
    async def list_memories(self, namespace, limit=100) -> list[MemoryRecord]: ...
```

## Shared Memory

The memory primitive supports shared namespaces for inter-agent knowledge sharing:

- **Level 1 -- Team-scoped shared memory:** Teams set `shared_memory_namespace` on the `TeamSpec`. All workers in the team receive `share_finding`, `read_shared`, `search_shared`, and `list_shared` tools that operate on a single team-wide namespace. This enables workers to share findings during a team run.

- **Level 2 -- Agent-level shared pools:** Individual agents set `shared_namespaces` on their `PrimitiveConfig.memory`. Each pool name resolves to a separate user-scoped namespace. Agents receive `share_to`, `read_from_pool`, `search_pool`, and `list_pool` tools that accept a `pool` parameter. This enables cross-agent knowledge sharing outside of a team context.

Both levels use the same underlying memory provider and are user-scoped (`{namespace}:u:{user_id}`).

## Healthcheck

Each provider implements a `healthcheck()` method that returns one of three states:

| Status | Meaning |
|--------|---------|
| `ok` | Provider is fully healthy |
| `reachable` | Server is up but needs user-provided credentials |
| `down` | Provider is unreachable or errored |

The readiness probe (`/readyz`) runs all healthchecks in parallel with a 5-second timeout per provider. The authenticated status endpoint (`/api/v1/providers/status`) runs with the user's resolved credentials, so providers in `reachable` state may show as `ok` there.

## Adding a New Backend

See [Adding a Provider](../guides/adding-a-provider.md) for the step-by-step guide.

## Provider Selection

Providers are selected in this priority order:

1. **Per-request header**: `X-Provider-Memory: mem0`
2. **Agent spec override**: `provider_overrides: {memory: mem0}`
3. **Config default**: `memory.default: in_memory`

This enables gradual migration -- run both backends simultaneously, route specific agents to the new one.
