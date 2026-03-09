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
| **Gateway** | LLM request routing with tool_use support and streaming | Noop, Bedrock Converse |
| **Tools** | External tool registration, invocation, search, MCP servers | Noop, AgentCore, MCP Registry |
| **Policy** | Cedar policy engine and policy CRUD, optional generation | Noop, AgentCore |
| **Evaluations** | LLM-as-a-judge evaluators, online eval configs | Noop, AgentCore |
| **Tasks** | Shared task board for team coordination | Noop, InMemory |

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

## Adding a New Backend

See [Adding a Provider](../guides/adding-a-provider.md) for the step-by-step guide.

## Provider Selection

Providers are selected in this priority order:

1. **Per-request header**: `X-Provider-Memory: mem0`
2. **Agent spec override**: `provider_overrides: {memory: mem0}`
3. **Config default**: `memory.default: in_memory`

This enables gradual migration -- run both backends simultaneously, route specific agents to the new one.
