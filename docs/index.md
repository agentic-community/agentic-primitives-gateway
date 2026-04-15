# Agentic Primitives Gateway

A unified API for agent infrastructure primitives. Agent developers code against the API. Platform operators choose backends via configuration. The two concerns are fully decoupled.

## What It Does

AI agents need infrastructure: memory, identity, code execution, browser automation, observability, LLM routing, tools, policy enforcement, and evaluations. The gateway provides all nine as a single REST API with pluggable backends.

```
Agent (any framework)  -->  Gateway REST API  -->  Backend Provider
                                                    (mem0, AgentCore, Langfuse,
                                                     Selenium, Jupyter, etc.)
```

## Key Features

- **10 primitives** with multiple backend providers each
- **Declarative agents** with server-side LLM tool-call loops
- **Agent-as-tool delegation** — agents call other agents through the same tool interface
- **Self-creating agents** — a meta-agent creates specialist agents at runtime, delegates work, and cleans up
- **Agent teams** with task boards, continuous replanning, and parallel execution
- **Token streaming** via SSE for real-time UI updates
- **Background run persistence** — runs continue if the client disconnects; reconnect and resume
- **Multi-session/run support** — multiple conversations per agent, multiple runs per team
- **Pluggable store backends** — file (default) or Redis for multi-replica deployments
- **Multi-user authentication** with pluggable backends (noop, API key, JWT/OIDC)
- **Resource ownership and group-based access control** — users see only their own agents, sessions, and teams
- **User-scoped memory isolation** — memory namespaces are automatically prefixed per user
- **Policy enforcement** via Cedar with auto-discovered actions
- **Per-request provider routing** via headers (`X-Provider-Memory: mem0`)
- **Credential pass-through** preserving caller identity
- **Web UI** with OIDC login flow, agent management, streaming chat, team execution, session/run management, and API exploration
- **Hot-reload** config via Kubernetes ConfigMap watcher

## Quick Links

- [Quickstart](getting-started/quickstart.md): get running in 2 minutes
- [Architecture](concepts/architecture.md): how the pieces fit together
- [Agents Guide](concepts/agents.md): declarative agents with tool calling
- [Teams Guide](concepts/teams.md): multi-agent collaboration with task boards
- [API Reference](api/overview.md): full endpoint documentation
