# Web UI

The gateway includes a React SPA served at `/ui/` with dashboard, agent management, chat, team management, policy management, and API exploration.

## Pages

### Dashboard (`/ui/`)

Shows system health, readiness checks, and available providers per primitive. Features:

- **Connection status indicator** -- green/yellow/red dot under the APG title in the sidebar, reflecting the current health check state
- **Tri-state provider health** -- each provider shows `ok` (green), `reachable` (yellow, needs user credentials), or `down` (red). Only `down` providers are considered unhealthy.
- **Credential status banner** -- when OIDC credential resolution is active, the dashboard shows a banner indicating whether user credentials are configured, what credential types are required by active providers (e.g., AWS, Langfuse), and the server credential fallback mode (`never`/`fallback`/`always`)

### Agent List (`/ui/agents`)

- View all configured agents with their models and enabled primitives
- **Create** new agents with the form (model, system prompt, primitives selector, **per-primitive provider dropdown** when multiple providers are available)
- **Edit** existing agents inline (click "Edit" to expand the form)
- **Delete** agents
- **Export** agents as standalone Python scripts (click "Export" to download)
- **Chat** with any agent (click "Chat" to navigate)

### Agent Chat (`/ui/agents/{name}/chat`)

Interactive streaming chat with an agent. Features:

- **Token streaming** -- text appears word by word as the LLM generates
- **Tools panel** -- collapsible section showing available tools and their providers
- **Memory panel** -- collapsible section showing stored memories with refresh
- **Tool call badges** -- shows which tools were called during a turn
- **Sub-agent activity** -- live streaming from delegated sub-agents with status indicators
- **Artifact blocks** -- collapsible code + output blocks from tool executions
- **Session persistence** -- session ID stored in localStorage and synced to URL `?session_id=`
- **Multi-session support** -- header shows all sessions for the agent, switch between them, create new, delete old
- **Background run resume** -- if you navigate away or refresh mid-stream, the run continues server-side. On return, the UI polls for completion and restores the conversation.
- **Background indicator** -- "Agent is working in the background..." with pulsing dot when a run is active
- **SSE reconnection** -- if the stream drops (server restart, network error), the UI automatically reconnects with a retry loop, replaying missed events for a seamless experience

### Team List (`/ui/teams`)

- View all configured teams with their planner, synthesizer, and workers
- **Create** new teams (includes shared memory namespace field)
- **Edit** existing teams (shared memory checkbox to enable/disable `shared_memory_namespace`)
- **Delete** teams
- **Export** teams as standalone Python scripts (click "Export" to download)
- **Run** any team (click "Run" to navigate)

### Team Run (`/ui/teams/{name}/run`)

Interactive team execution with real-time task board. Features:

- **Task board** -- shows all tasks with status badges, assigned workers, and streaming content
- **Activity log** -- real-time log of worker activity, task claims, completions
- **Synthesized response** -- final response with expand and save-as-markdown options
- **Run persistence** -- run ID stored in localStorage and synced to URL `?run_id=`
- **Multi-run support** -- header shows past runs, switch between them, create new, delete old
- **Event replay** -- on page refresh, all recorded events are replayed to reconstruct the full UI state (task board, activity log, streaming content, response)
- **Background run resume** -- "Team is working in the background..." indicator with polling

### Policy Manager (`/ui/policies`)

Create and manage Cedar policy engines and policies.

### Settings (`/ui/settings`)

Per-user credential management. Features:

- **Credential resolution status** -- shows whether OIDC resolution is active
- **Stored credentials** -- displays existing `apg.*` credentials grouped by service with masked values
- **Edit/delete** -- hover over a stored credential to reveal edit and delete buttons. Edit replaces the value inline; delete removes it from Keycloak.
- **Add new credentials** -- key-value editor with `apg.` prefix shown inline. Use `service.key_name` format (e.g., `langfuse.public_key`, `mcp_registry.token`).
- **Naming convention tip** -- shows how key names map to `service_credentials` for providers

### Primitive Explorer (`/ui/explorer`)

Interactive API explorer that:

- Lists all primitives and their available providers
- Shows all endpoints per primitive
- Lets you fill parameters and execute requests
- Displays responses inline

## Authentication

When JWT auth is configured on the gateway, the UI uses an OIDC Authorization Code + PKCE flow to authenticate users.

**Login flow:**

1. On first visit, the UI fetches `/auth/config` to discover the OIDC provider settings
2. The browser redirects to the Identity Provider (IdP) login page
3. After login, the IdP redirects back to the UI callback, which exchanges the authorization code for tokens
4. The Bearer token is attached to all subsequent API calls automatically

**UI changes when auth is active:**

- The sidebar shows the logged-in username and a logout button
- Session IDs in localStorage are scoped per user (`{userId}:{agentName}`) so multiple users on the same browser do not share sessions

**IdP requirements:**

- Requires a **public** OIDC client (in Keycloak: set "Client authentication" to OFF)

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
| `src/pages/` | Page components (Dashboard, AgentList, AgentChat, TeamList, TeamRun, PolicyManager, PrimitiveExplorer, Settings) |
| `src/components/` | Reusable components (ChatMessage, ToolCallBlock, SubAgentBlock, ArtifactBlock, MemoryPanel, ToolsPanel, CollapsibleSection, etc.) |
| `src/hooks/` | Data fetching hooks (`useFetch<T>`, `useAutoScroll`) |
| `src/lib/` | Shared utilities -- `cn` (class names), `theme` (CODE_THEME, PROSE_CLASSES), `sse` (generic `parseSSE<T>`) |
| `src/api/` | `client.ts` (REST + SSE via `sseStream()` factory), `types.ts` (TypeScript types) |

The production build outputs to `src/agentic_primitives_gateway/static/` which FastAPI serves at `/ui/` with client-side routing fallback.
