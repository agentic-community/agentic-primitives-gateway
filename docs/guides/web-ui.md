# Web UI

The gateway includes a React SPA served at `/ui/` with dashboard, agent management, chat, policy management, and API exploration.

## Pages

### Dashboard (`/ui/`)

Shows system health, readiness checks, and available providers per primitive.

### Agent List (`/ui/agents`)

- View all configured agents with their models and enabled primitives
- **Create** new agents with the form (model, system prompt, primitives selector)
- **Edit** existing agents inline (click "Edit" to expand the form)
- **Delete** agents
- **Chat** with any agent (click "Chat" to navigate)

### Agent Chat (`/ui/agents/{name}/chat`)

Interactive streaming chat with an agent. Features:

- **Token streaming** -- text appears word by word as the LLM generates
- **Tools panel** -- collapsible section showing available tools and their providers
- **Memory panel** -- collapsible section showing stored memories with refresh
- **Tool call badges** -- shows which tools were called during a turn
- **Sub-agent activity** -- live streaming from delegated sub-agents with status indicators
- **Artifact blocks** -- collapsible code + output blocks from tool executions
- **Session management** -- session ID shown in header, copy to clipboard

### Policy Manager (`/ui/policies`)

Create and manage Cedar policy engines and policies.

### Primitive Explorer (`/ui/explorer`)

Interactive API explorer that:

- Lists all primitives and their available providers
- Shows all endpoints per primitive
- Lets you fill parameters and execute requests
- Displays responses inline

## Development

```bash
# Install dependencies
cd ui && npm install

# Development with hot reload (proxies API to :8000)
npm run dev
# Opens at http://localhost:5173/ui/

# Production build (served by FastAPI)
npm run build
# Visit http://localhost:8000/ui/
```

## Architecture

| Directory | Contents |
|-----------|----------|
| `src/pages/` | Page components (Dashboard, AgentList, AgentChat, etc.) |
| `src/components/` | Reusable components (ChatMessage, ToolCallBlock, SubAgentBlock, ArtifactBlock, MemoryPanel, ToolsPanel, CollapsibleSection, etc.) |
| `src/hooks/` | Data fetching hooks built on generic `useFetch<T>` |
| `src/lib/` | Shared utilities -- `cn` (class names), `theme` (CODE_THEME, PROSE_CLASSES), `sse` (parseSSE) |
| `src/api/` | `client.ts` (REST + SSE streaming), `types.ts` (TypeScript types) |

The production build outputs to `src/agentic_primitives_gateway/static/` which FastAPI serves at `/ui/` with client-side routing fallback.
