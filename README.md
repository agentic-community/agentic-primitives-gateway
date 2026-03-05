# Agentic Primitives Gateway -- Operator Manual

Agentic Primitives Gateway is a REST API service that abstracts agent infrastructure primitives behind a unified API. Agent developers call this service without knowing backend implementations. Platform operators swap backends via configuration. Requests can dynamically select which backend to use via header-based provider routing.

## Architecture

```
+------------------------------------------------------------------------+
|                      Agentic Primitives Gateway                        |
|                                                                        |
|  +------------------------------------------------------------------+  |
|  |                     Agents Subsystem                              |  |
|  |  (Declarative specs, CRUD API, LLM tool-call loop, auto-hooks)   |  |
|  |  POST /api/v1/agents/{name}/chat → AgentRunner → primitives      |  |
|  +----+------------------------------+------------------------------+  |
|       |                              |                                 |
|  +---------+ +---------+ +---------+ +---------+ +---------+          |
|  | Memory  | |Identity | |  Code   | | Browser | |  Tools  |          |
|  | Routes  | | Routes  | |Interpret| | Routes  | | Routes  |          |
|  +----+----+ +----+----+ | Routes  | +----+----+ +----+----+          |
|       |           |      +----+----+      |           |               |
|  +----+----+ +----+----+     |       +----+----+ +----+----+          |
|  |Observ.  | |Gateway  |     |       | Policy  | | Evals   |          |
|  | Routes  | | Routes  |     |       | Routes  | | Routes  |          |
|  +----+----+ +----+----+     |       +----+----+ +----+----+          |
|       |           |           |       |         | |         |          |
|  +----v-----------v-----------v-------v---------v-v---------v--------+ |
|  |              PolicyEnforcementMiddleware                          | |
|  |  (Cedar evaluation via PolicyEnforcer ABC; exempt: health/docs)  | |
|  +----+----------+-----------+-------+---------+----------+----------+ |
|       |          |           |       |         |          |            |
|  +----v-----------v-----------v-------v---------v---------v----------+ |
|  |                  RequestContextMiddleware                         | |
|  |          (AWS creds + provider routing from headers)              | |
|  +----+----------+-----------+-------+---------+----------+----------+ |
|       |          |           |       |         |          |            |
|  +----v----------v-----------v-------v---------v----------v----------+ |
|  |                     Provider Registry                             | |
|  |     (loads named backends from config; resolves per-request)      | |
|  |              wrapped by MetricsProxy (Prometheus)                 | |
|  +--+-------+-------+-------+-------+--------+-------+------+-------+ |
|     |       |       |       |       |        |       |      |         |
+-----+-------+-------+-------+-------+--------+-------+------+---------+
      |       |       |       |       |        |       |      |
 +----v---+ +-v-------+ +v------+ +v----+ +v-----+ +v------+ +v------+ +v------+ +v----------+
 | Memory | |Identity | |Code   | |Brwsr| |Obsrv.| |Gateway| |Policy | | Evals | |  Tools   |
 |--------| |---------| |Interp | |-----| |------| |-------| |-------| |-------| |----------|
 | Noop   | |Noop     | |Noop   | |Noop | |Noop  | |Noop   | |Noop   | |Noop   | | Noop     |
 | InMem  | |AgntCore | |AgntCr | |Agnt | |Lang  | |Bedrock| |Agnt   | |Agnt   | | AgntCore |
 | Mem0   | |Keycloak | |Juptyr | |Core | |fuse  | |Convrs | |Core   | |Core   | | MCP      |
 | Agnt   | |Entra    | |       | |Seln | |Agnt  | |       | |       | |       | | Registry |
 | Core   | |Okta     | |       | |Grid | |Core  | |       | |       | |       | |          |
 +--------+ +---------+ +-------+ +-----+ +------+ +-------+ +-------+ +-------+ +----------+
```

## Primitives

| Primitive | Description | Available Backends |
|-----------|-------------|--------------------|
| **Memory** | Key-value memory, conversation events, session/branch management, memory resource lifecycle, strategy management | `NoopMemoryProvider`, `InMemoryProvider`, `Mem0MemoryProvider` (Milvus), `AgentCoreMemoryProvider` |
| **Identity** | Workload identity tokens, OAuth2 token exchange (M2M + 3LO), API key retrieval, credential provider and workload identity management | `NoopIdentityProvider`, `AgentCoreIdentityProvider`, `KeycloakIdentityProvider`, `EntraIdentityProvider`, `OktaIdentityProvider` |
| **Code Interpreter** | Sandboxed code execution sessions with execution history | `NoopCodeInterpreterProvider`, `AgentCoreCodeInterpreterProvider`, `JupyterCodeInterpreterProvider` |
| **Browser** | Cloud-based browser automation | `NoopBrowserProvider`, `AgentCoreBrowserProvider`, `SeleniumGridBrowserProvider` |
| **Observability** | Trace/log ingestion, LLM generation tracking, evaluation scoring, session management | `NoopObservabilityProvider`, `LangfuseObservabilityProvider`, `AgentCoreObservabilityProvider` |
| **Gateway** | LLM request routing with tool_use support | `NoopGatewayProvider`, `BedrockConverseProvider` |
| **Tools** | Tool registration, invocation, search, and MCP server management | `NoopToolsProvider`, `AgentCoreGatewayProvider`, `MCPRegistryProvider` |
| **Policy** | Cedar-based policy engine and policy management, optional policy generation | `NoopPolicyProvider`, `AgentCorePolicyProvider` |
| **Evaluations** | LLM-as-a-judge evaluator management and evaluation, optional online eval configs | `NoopEvaluationsProvider`, `AgentCoreEvaluationsProvider` |

All nine primitives are fully implemented and wired to their respective providers.

**Agents** sit above the primitives as a declarative orchestration layer. An agent is defined by a spec (system prompt, model, enabled primitives/tools, hooks) and the gateway runs the LLM tool-call loop internally. No external agent framework needed.

## API Reference

### Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/healthz` | Liveness probe. Returns `{"status": "ok"}`. |
| `GET` | `/readyz` | Readiness probe. Checks all provider healthchecks. Returns 200 or 503. |

### Provider Discovery

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/providers` | List available providers for each primitive. |

Example response:

```json
{
  "memory": {"default": "mem0", "available": ["mem0", "agentcore", "in_memory"]},
  "identity": {"default": "noop", "available": ["noop", "agentcore"]},
  "code_interpreter": {"default": "noop", "available": ["noop", "agentcore"]},
  "browser": {"default": "noop", "available": ["noop", "agentcore"]},
  "observability": {"default": "noop", "available": ["noop"]},
  "gateway": {"default": "noop", "available": ["noop"]},
  "tools": {"default": "noop", "available": ["noop"]}
}
```

### Memory (`/api/v1/memory`)

**Key-value memory (data plane):**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/{namespace}` | Store a memory. Body: `{"key": "...", "content": "...", "metadata": {}}`. Returns 201. |
| `GET` | `/{namespace}/{key}` | Retrieve a memory by key. Returns 404 if not found. |
| `GET` | `/{namespace}` | List memories. Query params: `limit` (1--1000, default 100), `offset` (default 0). |
| `POST` | `/{namespace}/search` | Semantic search. Body: `{"query": "...", "top_k": 10, "filters": {}}`. |
| `DELETE` | `/{namespace}/{key}` | Delete a memory. Returns 204 on success, 404 if not found. |

Namespace conventions: `agent:<agent-id>`, `user:<user-id>`, `session:<session-id>`, `global`.

**Conversation events:**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/sessions/{actor_id}/{session_id}/events` | Create a conversation event. Body: `{"messages": [{"text": "...", "role": "..."}], "metadata": {}}`. Returns 201. |
| `GET` | `/sessions/{actor_id}/{session_id}/events` | List events in a session. Query param: `limit` (1--1000, default 100). |
| `GET` | `/sessions/{actor_id}/{session_id}/events/{event_id}` | Get a specific event. Returns 404 if not found. |
| `DELETE` | `/sessions/{actor_id}/{session_id}/events/{event_id}` | Delete an event. Returns 204 on success, 404 if not found. |
| `GET` | `/sessions/{actor_id}/{session_id}/turns` | Get last K conversation turns. Query param: `k` (1--100, default 5). |

**Session management:**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/actors` | List actors that have sessions. |
| `GET` | `/actors/{actor_id}/sessions` | List sessions for an actor. |

**Branch management:**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/sessions/{actor_id}/{session_id}/branches` | Fork a conversation from a specific event. Body: `{"root_event_id": "...", "branch_name": "...", "messages": [...]}`. Returns 201. |
| `GET` | `/sessions/{actor_id}/{session_id}/branches` | List branches in a session. |

**Memory resource lifecycle (control plane):**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/resources` | Create a memory resource. Body: `{"name": "...", "strategies": [...], "description": "..."}`. Returns 201. |
| `GET` | `/resources` | List memory resources. |
| `GET` | `/resources/{memory_id}` | Get memory resource details. |
| `DELETE` | `/resources/{memory_id}` | Delete a memory resource. Returns 204. |

**Strategy management:**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/resources/{memory_id}/strategies` | List strategies on a memory resource. |
| `POST` | `/resources/{memory_id}/strategies` | Add a strategy. Body: `{"strategy": {...}}`. Returns 201. |
| `DELETE` | `/resources/{memory_id}/strategies/{strategy_id}` | Remove a strategy. Returns 204. |

Conversation events, session management, branch management, control plane, and strategy endpoints return 501 if not supported by the configured provider. The `InMemoryProvider` supports conversation events and session management. The `AgentCoreMemoryProvider` supports all operations.

### Identity (`/api/v1/identity`)

**Token operations (data plane):**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/token` | Exchange a workload token for an external service OAuth2 token. Supports M2M and 3-legged (USER_FEDERATION) flows. |
| `POST` | `/api-key` | Retrieve a stored API key for a credential provider. |
| `POST` | `/workload-token` | Obtain a workload identity token for the agent, optionally scoped to a user. |
| `POST` | `/auth/complete` | Confirm user authorization for a 3-legged OAuth flow. Returns 204. |

**Credential provider management (control plane):**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/credential-providers` | List registered credential providers (OAuth2 and API key). |
| `POST` | `/credential-providers` | Register a new credential provider. Returns 201. |
| `GET` | `/credential-providers/{name}` | Get credential provider details. |
| `PUT` | `/credential-providers/{name}` | Update a credential provider. |
| `DELETE` | `/credential-providers/{name}` | Delete a credential provider. Returns 204. |

**Workload identity management (control plane):**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/workload-identities` | Register a new workload (agent) identity. Returns 201. |
| `GET` | `/workload-identities` | List workload identities. |
| `GET` | `/workload-identities/{name}` | Get workload identity details. |
| `PUT` | `/workload-identities/{name}` | Update a workload identity. |
| `DELETE` | `/workload-identities/{name}` | Delete a workload identity. Returns 204. |

Control plane endpoints return 501 if not supported by the configured provider.

### Code Interpreter (`/api/v1/code-interpreter`)

**Session lifecycle:**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/sessions` | Start a sandboxed execution session. Returns 201. |
| `DELETE` | `/sessions/{session_id}` | Stop a session. Returns 204. |
| `GET` | `/sessions` | List active sessions. |
| `GET` | `/sessions/{session_id}` | Get session details (status, language, created_at). Returns 404 if not found. |

**Code execution:**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/sessions/{session_id}/execute` | Execute code in a session. |
| `GET` | `/sessions/{session_id}/history` | Get execution history for a session. Query param: `limit` (1--500, default 50). |

**File I/O:**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/sessions/{session_id}/files` | Upload a file to a session (multipart). |
| `GET` | `/sessions/{session_id}/files/{filename}` | Download a file from a session (binary). |

Session details and execution history endpoints return 501 if not supported by the configured provider. Both `NoopCodeInterpreterProvider` and `AgentCoreCodeInterpreterProvider` support session details. `AgentCoreCodeInterpreterProvider`, and `JupyterCodeInterpreterProvider` store execution history.

### Browser (`/api/v1/browser`)

**Session lifecycle:**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/sessions` | Start a browser session. Returns 201. |
| `DELETE` | `/sessions/{session_id}` | Stop a session. Returns 204. |
| `GET` | `/sessions/{session_id}` | Get session info. |
| `GET` | `/sessions` | List sessions. |
| `GET` | `/sessions/{session_id}/live-view` | Get a live view URL for a session. |

**Browser interaction:**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/sessions/{session_id}/navigate` | Navigate to a URL. Body: `{"url": "..."}`. |
| `GET` | `/sessions/{session_id}/screenshot` | Take a screenshot (returns PNG base64). |
| `GET` | `/sessions/{session_id}/content` | Get current page HTML content. |
| `POST` | `/sessions/{session_id}/click` | Click an element. Body: `{"selector": "..."}`. |
| `POST` | `/sessions/{session_id}/type` | Type text into an element. Body: `{"selector": "...", "text": "..."}`. |
| `POST` | `/sessions/{session_id}/evaluate` | Evaluate a JavaScript expression. Body: `{"expression": "..."}`. |

Browser interaction endpoints return 400 if the session is not found or the operation is not supported.

### Observability (`/api/v1/observability`)

**Trace and log ingestion (data plane):**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/traces` | Ingest a trace. Returns 202. |
| `POST` | `/logs` | Ingest a log entry. Returns 202. |
| `GET` | `/traces` | Query traces. Query params: `trace_id`, `limit` (1--1000, default 100). |

**Trace retrieval and updates:**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/traces/{trace_id}` | Get a single trace by ID. Returns 404 if not found. |
| `PUT` | `/traces/{trace_id}` | Update trace metadata after creation. Body: `{"name": "...", "tags": [...]}`. |

**LLM generation logging:**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/traces/{trace_id}/generations` | Log an LLM call. Body: `{"name": "...", "model": "...", "input": ..., "output": ..., "usage": {"prompt_tokens": N, "completion_tokens": N}}`. Returns 201. |

**Evaluation scoring:**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/traces/{trace_id}/scores` | Attach evaluation score. Body: `{"name": "...", "value": 0.95, "comment": "..."}`. Returns 201. |
| `GET` | `/traces/{trace_id}/scores` | List scores for a trace. |

**Session management:**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/sessions` | List observability sessions. Query params: `user_id`, `limit`. |
| `GET` | `/sessions/{session_id}` | Get session details. |

**Flush:**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/flush` | Force flush pending telemetry. Returns 202. |

Trace retrieval, updates, scoring, session management, and flush endpoints return 501 if not supported by the configured provider. The `LangfuseObservabilityProvider` supports all operations. The `AgentCoreObservabilityProvider` supports trace retrieval, LLM generation logging, and flush.

### Gateway (`/api/v1/gateway`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/completions` | Route an LLM completion request. Supports optional `tools`, `tool_choice`, and `system` fields. Response includes optional `tool_calls` and `stop_reason`. |
| `GET` | `/models` | List available models. |

**Available backends:**
- `NoopGatewayProvider` -- returns empty responses (dev/test)
- `BedrockConverseProvider` -- AWS Bedrock Converse API with full tool_use support. Config: `region`, `default_model`. Uses per-request AWS credentials via `get_boto3_session()`.

### Tools (`/api/v1/tools`)

**Tool operations (data plane):**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/` | Register a tool. Returns 201. |
| `GET` | `/` | List registered tools. |
| `GET` | `/search` | Search tools by query. Query params: `query`, `max_results` (1--100, default 10). |
| `POST` | `/{name}/invoke` | Invoke a tool by name. Body: `{"params": {}}`. |
| `GET` | `/{name}` | Get a single tool definition by name. Returns 404 if not found. |
| `DELETE` | `/{name}` | Delete a tool. Returns 204 on success. |

**Server management (MCP Registry):**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/servers` | List registered MCP servers with health status. |
| `POST` | `/servers` | Register a new MCP server. Body: `{"name": "...", "url": "...", "config": {}}`. Returns 201. |
| `GET` | `/servers/{server_name}` | Get details for a specific server. Returns 404 if not found. |

Tool retrieval, deletion, and server management endpoints return 501 if not supported by the configured provider. The `MCPRegistryProvider` supports all operations. The `AgentCoreGatewayProvider` supports tool retrieval only.

### Policy (`/api/v1/policy`)

**Policy engines:**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/engines` | Create a policy engine. Body: `{"name": "...", "description": "...", "config": {}}`. Returns 201. |
| `GET` | `/engines` | List policy engines. Query params: `max_results` (default 100), `next_token`. |
| `GET` | `/engines/{engine_id}` | Get a policy engine. |
| `DELETE` | `/engines/{engine_id}` | Delete a policy engine. Returns 204. |

**Policies (Cedar):**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/engines/{engine_id}/policies` | Create a policy. Body: `{"policy_body": "permit(...);", "description": "..."}`. Returns 201. |
| `GET` | `/engines/{engine_id}/policies` | List policies. Query params: `max_results`, `next_token`. |
| `GET` | `/engines/{engine_id}/policies/{policy_id}` | Get a policy. |
| `PUT` | `/engines/{engine_id}/policies/{policy_id}` | Update a policy. Body: `{"policy_body": "...", "description": "..."}`. |
| `DELETE` | `/engines/{engine_id}/policies/{policy_id}` | Delete a policy. Returns 204. |

**Policy generation (optional):**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/engines/{engine_id}/generations` | Start policy generation. Returns 201 or 501 if not supported. |
| `GET` | `/engines/{engine_id}/generations` | List policy generations. Returns 501 if not supported. |
| `GET` | `/engines/{engine_id}/generations/{generation_id}` | Get policy generation status. Returns 501 if not supported. |
| `GET` | `/engines/{engine_id}/generations/{generation_id}/assets` | List generation assets. Returns 501 if not supported. |

Policy generation endpoints return 501 if not supported by the configured provider. The `AgentCorePolicyProvider` supports all operations.

### Evaluations (`/api/v1/evaluations`)

**Evaluator management:**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/evaluators` | Create an evaluator. Body: `{"name": "...", "evaluator_type": "...", "config": {}, "description": "..."}`. Returns 201. |
| `GET` | `/evaluators` | List evaluators. Query params: `max_results` (default 100), `next_token`. |
| `GET` | `/evaluators/{evaluator_id}` | Get an evaluator. |
| `PUT` | `/evaluators/{evaluator_id}` | Update an evaluator. Body: `{"config": {}, "description": "..."}`. |
| `DELETE` | `/evaluators/{evaluator_id}` | Delete an evaluator. Returns 204. |

**Evaluate (data plane):**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/evaluate` | Run evaluation. Body: `{"evaluator_id": "...", "input_data": "...", "output_data": "...", "expected_output": "...", "metadata": {}}`. Built-in evaluators: `Builtin.Helpfulness`, `Builtin.Coherence`, `Builtin.Relevance`, `Builtin.Correctness`. |

**Online evaluation configs (optional):**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/online-configs` | Create an online eval config. Returns 201 or 501 if not supported. |
| `GET` | `/online-configs` | List online eval configs. Returns 501 if not supported. |
| `GET` | `/online-configs/{config_id}` | Get an online eval config. Returns 501 if not supported. |
| `DELETE` | `/online-configs/{config_id}` | Delete an online eval config. Returns 204 or 501 if not supported. |

Online evaluation config endpoints return 501 if not supported by the configured provider. The `AgentCoreEvaluationsProvider` supports all operations including online eval configs.

### Agents (`/api/v1/agents`)

Declarative agents that run LLM tool-call loops server-side. Define an agent with a system prompt, model, and enabled primitives -- the gateway handles tool execution, memory, and tracing automatically.

**Agent CRUD:**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/` | Create an agent from a spec. Returns 201. Returns 409 if name already exists. |
| `GET` | `/` | List all agents. |
| `GET` | `/{name}` | Get an agent spec. Returns 404 if not found. |
| `PUT` | `/{name}` | Update an agent (partial update). Returns 404 if not found. |
| `DELETE` | `/{name}` | Delete an agent. Returns 404 if not found. |

**Chat:**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/{name}/chat` | Chat with an agent. Body: `{"message": "...", "session_id": "..."}`. The gateway runs the full LLM tool-call loop and returns when done. |

Chat response:

```json
{
  "response": "The assistant's response text",
  "session_id": "auto-generated-or-provided",
  "agent_name": "research-assistant",
  "turns_used": 3,
  "tools_called": ["search_memory", "remember"],
  "metadata": {"trace_id": "..."}
}
```

**Agent spec fields:**

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Unique agent identifier. |
| `model` | string | Bedrock model ID (e.g., `us.anthropic.claude-sonnet-4-20250514-v1:0`). |
| `system_prompt` | string | System prompt for the LLM. |
| `description` | string | Human-readable description. |
| `primitives` | object | Which primitives/tools to enable. Keys: `memory`, `code_interpreter`, `browser`, `tools`, `identity`. Each value has `enabled` (bool), `tools` (list of tool names or null for all), `namespace` (string with `{agent_name}`, `{session_id}` placeholders). |
| `hooks` | object | `auto_memory` (bool): store conversation turns automatically. `auto_trace` (bool): trace LLM calls and tool executions to the observability provider. |
| `provider_overrides` | object | Per-primitive provider routing (same as `X-Provider-*` headers). |
| `max_turns` | int | Safety limit for the tool-call loop (default: 20). |
| `temperature` | float | LLM temperature (default: 1.0). |
| `max_tokens` | int | LLM max tokens (optional). |

**Available tools per primitive:**

| Primitive | Tools |
|-----------|-------|
| `memory` | `remember`, `recall`, `search_memory`, `forget`, `list_memories` |
| `code_interpreter` | `execute_code` |
| `browser` | `navigate`, `read_page`, `click`, `type_text`, `screenshot`, `evaluate_js` |
| `tools` | `search_tools`, `invoke_tool` |
| `identity` | `get_token`, `get_api_key` |

**Example -- create and chat with an agent:**

```bash
# Create
curl -X POST localhost:8000/api/v1/agents -H "Content-Type: application/json" -d '{
  "name": "my-assistant",
  "model": "us.anthropic.claude-sonnet-4-20250514-v1:0",
  "system_prompt": "You are a helpful assistant with memory.",
  "primitives": {"memory": {"enabled": true, "namespace": "agent:{agent_name}:{session_id}"}},
  "hooks": {"auto_memory": true, "auto_trace": false}
}'

# Chat
curl -X POST localhost:8000/api/v1/agents/my-assistant/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Remember that my favorite color is blue", "session_id": "s1"}'
```

Agents can also be defined in YAML config under the `agents.specs` key (see Configuration section).

Interactive API docs are available at `/docs` (Swagger UI) when the server is running.

---

## Policy Enforcement

The gateway includes a pluggable policy enforcement layer that evaluates every primitive call against Cedar policies. This is separate from the Policy CRUD primitive (`/api/v1/policy`) -- the CRUD routes are the **write path** (manage policies), the enforcer is the **read path** (evaluate at request time).

### How It Works

1. `RequestContextMiddleware` extracts identity from headers (`X-Agent-Id`, `X-Cred-*`, `X-AWS-*`)
2. `PolicyEnforcementMiddleware` maps the request to a Cedar principal/action/resource
3. The configured `PolicyEnforcer` evaluates the authorization request
4. If denied, the middleware returns 403; if allowed, the request proceeds to the route

### Enforcers

| Enforcer | Behavior |
|----------|----------|
| `NoopPolicyEnforcer` (default) | All requests allowed -- gateway works as before |
| `CedarPolicyEnforcer` | Local Cedar evaluation via `cedarpy`. Default-deny: no policies loaded = all denied |

### Configuration

```yaml
# Default: no enforcement
enforcement:
  backend: "agentic_primitives_gateway.enforcement.noop.NoopPolicyEnforcer"
  config: {}

# Cedar enforcement
enforcement:
  backend: "agentic_primitives_gateway.enforcement.cedar.CedarPolicyEnforcer"
  config:
    policy_refresh_interval: 30   # seconds between policy refreshes
    engine_id: "my-engine"        # optional: scope to a single policy engine
```

Requires the `cedar` optional dependencies:

```bash
pip install agentic-primitives-gateway[cedar]
```

### Principal Resolution

The middleware derives the Cedar principal from request headers in this order:

| Priority | Header | Cedar Principal |
|----------|--------|-----------------|
| 1 | `X-Agent-Id: my-agent` | `Agent::"my-agent"` |
| 2 | `X-Cred-{Service}-*` | `Service::"{service}"` |
| 3 | `X-AWS-Access-Key-Id: AKIA...` | `AWSPrincipal::"AKIA..."` |
| 4 | (none) | `Agent::"anonymous"` |

### Action Mapping

Actions are **auto-discovered from the app's registered routes** at startup. The middleware introspects every FastAPI route under `/api/v1/` and derives the Cedar action as `{primitive}:{endpoint_function_name}`. This means new routes are automatically enforced -- no static table to maintain.

Examples of auto-discovered actions:

| Route | Endpoint function | Cedar Action |
|-------|-------------------|-------------|
| `POST /api/v1/memory/{namespace}` | `store_memory` | `memory:store_memory` |
| `POST /api/v1/memory/{namespace}/search` | `search_memories` | `memory:search_memories` |
| `GET /api/v1/memory/{namespace}/{key}` | `retrieve_memory` | `memory:retrieve_memory` |
| `POST /api/v1/gateway/completions` | `route_completion` | `gateway:route_completion` |
| `POST /api/v1/tools/{name}/invoke` | `invoke_tool` | `tools:invoke_tool` |
| `POST /api/v1/agents/{name}/chat` | `chat_with_agent` | `agents:chat_with_agent` |

Routes outside `/api/v1/` are not enforced. The action rule cache is built once on the first request and reused for the lifetime of the process.

### Exempt Paths

These paths are never enforced: `/healthz`, `/readyz`, `/metrics`, `/docs`, `/redoc`, `/openapi.json`, `/api/v1/providers`, `/api/v1/policy`.

### Example Cedar Policies

```cedar
// Allow all agents to read memory
permit(principal, action == Action::"memory:retrieve_memory", resource);

// Allow a specific agent to do everything
permit(principal == Agent::"research-assistant", action, resource);

// Block a specific agent from code execution
forbid(principal == Agent::"untrusted", action == Action::"code_interpreter:execute_code", resource);
```

### Multi-Tenancy

- Policy evaluation is shared across all tenants. Cedar's principal/action/resource matching naturally scopes policies.
- `X-Agent-Id` is trusted as-is. In production multi-tenant deployments, validate it via an authenticating reverse proxy.
- Each pod runs its own enforcer reading from the same policy store. Updates propagate within one refresh interval.

---

## Configuration

Configuration is loaded from three sources in order of priority:

1. **Environment variables** (highest priority) -- prefixed with `AGENTIC_PRIMITIVES_GATEWAY_`, nested with `__`
2. **YAML config file** -- path set by `AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE` env var
3. **Defaults** -- in-memory/noop providers for all primitives

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `AGENTIC_PRIMITIVES_GATEWAY_HOST` | Bind address | `0.0.0.0` |
| `AGENTIC_PRIMITIVES_GATEWAY_PORT` | Bind port | `8000` |
| `AGENTIC_PRIMITIVES_GATEWAY_LOG_LEVEL` | Log level (`debug`, `info`, `warning`, `error`) | `info` |
| `AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE` | Path to YAML config file | -- |
| `AGENTIC_PRIMITIVES_GATEWAY_ALLOW_SERVER_CREDENTIALS` | Allow server-side credential fallback | `false` |

### Server Credential Fallback

By default, the server requires clients to pass their own credentials (AWS, Langfuse, etc.) via request headers. If a client doesn't provide credentials, the request fails with a clear error.

To allow the server to use its own credentials as a fallback:

```yaml
allow_server_credentials: true
```

Or via environment variable:

```bash
AGENTIC_PRIMITIVES_GATEWAY_ALLOW_SERVER_CREDENTIALS=true
```

When enabled, the credential resolution order is:

1. **Client headers** (always preferred) -- `X-AWS-*`, `X-Cred-Langfuse-*`, etc.
2. **Server credentials** (fallback) -- from the server's environment:
   - AWS: `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, IRSA, Pod Identity, instance profiles
   - Langfuse: `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`/`LANGFUSE_BASE_URL` env vars, or `public_key`/`secret_key` in provider config
   - Other services: their respective env vars or provider config values

### YAML Config File (Multi-Provider Format)

The config file supports multiple named backends per primitive. Each primitive has a `default` key and a `backends` map:

```yaml
providers:
  memory:
    default: "mem0"
    backends:
      mem0:
        backend: "agentic_primitives_gateway.primitives.memory.mem0_provider.Mem0MemoryProvider"
        config:
          vector_store:
            provider: milvus
            config:
              collection_name: agentic_memories
              url: "http://milvus:19530"
          llm:
            provider: aws_bedrock
            config:
              model: us.anthropic.claude-sonnet-4-20250514-v1:0
          embedder:
            provider: aws_bedrock
            config:
              model: amazon.titan-embed-text-v2:0
      agentcore:
        backend: "agentic_primitives_gateway.primitives.memory.agentcore.AgentCoreMemoryProvider"
        config:
          memory_id: "your-memory-id"
          region: "us-east-1"
      in_memory:
        backend: "agentic_primitives_gateway.primitives.memory.in_memory.InMemoryProvider"
        config: {}

  identity:
    default: "agentcore"
    backends:
      agentcore:
        backend: "agentic_primitives_gateway.primitives.identity.agentcore.AgentCoreIdentityProvider"
        config:
          region: "us-east-1"
      keycloak:
        backend: "agentic_primitives_gateway.primitives.identity.keycloak.KeycloakIdentityProvider"
        config:
          server_url: "http://keycloak:8080"
          realm: "agents"
          client_id: "agentic-gateway"
          client_secret: "${KEYCLOAK_CLIENT_SECRET}"
      entra:
        backend: "agentic_primitives_gateway.primitives.identity.entra.EntraIdentityProvider"
        config:
          tenant_id: "${AZURE_TENANT_ID}"
          client_id: "${AZURE_CLIENT_ID}"
          client_secret: "${AZURE_CLIENT_SECRET}"
      okta:
        backend: "agentic_primitives_gateway.primitives.identity.okta.OktaIdentityProvider"
        config:
          domain: "${OKTA_DOMAIN}"
          client_id: "${OKTA_CLIENT_ID}"
          client_secret: "${OKTA_CLIENT_SECRET}"
          api_token: "${OKTA_API_TOKEN}"
      noop:
        backend: "agentic_primitives_gateway.primitives.identity.noop.NoopIdentityProvider"
        config: {}

  code_interpreter:
    default: "agentcore"
    backends:
      agentcore:
        backend: "agentic_primitives_gateway.primitives.code_interpreter.agentcore.AgentCoreCodeInterpreterProvider"
        config:
          region: "us-east-1"
      noop:
        backend: "agentic_primitives_gateway.primitives.code_interpreter.noop.NoopCodeInterpreterProvider"
        config: {}

  browser:
    default: "agentcore"
    backends:
      agentcore:
        backend: "agentic_primitives_gateway.primitives.browser.agentcore.AgentCoreBrowserProvider"
        config:
          region: "us-east-1"
      noop:
        backend: "agentic_primitives_gateway.primitives.browser.noop.NoopBrowserProvider"
        config: {}

  observability:
    default: "noop"
    backends:
      noop:
        backend: "agentic_primitives_gateway.primitives.observability.noop.NoopObservabilityProvider"
        config: {}
      langfuse:
        backend: "agentic_primitives_gateway.primitives.observability.langfuse.LangfuseObservabilityProvider"
        config: {}
      agentcore:
        backend: "agentic_primitives_gateway.primitives.observability.agentcore.AgentCoreObservabilityProvider"
        config:
          region: "us-east-1"
          service_name: "agentic-primitives-gateway"

  gateway:
    default: "noop"
    backends:
      noop:
        backend: "agentic_primitives_gateway.primitives.gateway.noop.NoopGatewayProvider"
        config: {}

  tools:
    default: "noop"
    backends:
      noop:
        backend: "agentic_primitives_gateway.primitives.tools.noop.NoopToolsProvider"
        config: {}
      agentcore:
        backend: "agentic_primitives_gateway.primitives.tools.agentcore.AgentCoreGatewayProvider"
        config: {}
      mcp_registry:
        backend: "agentic_primitives_gateway.primitives.tools.mcp_registry.MCPRegistryProvider"
        config: {}
```

Each backend entry has:
- `backend` -- fully qualified dotted path to the provider class
- `config` -- dict passed as `**kwargs` to the provider constructor

### Legacy Single-Provider Format

For backward compatibility, the legacy single-provider format is still supported:

```yaml
providers:
  tools:
    backend: "agentic_primitives_gateway.primitives.tools.noop.NoopToolsProvider"
    config: {}
```

When this format is detected (a `backend` key without a `backends` key), it is automatically converted to the multi-provider format with a single backend named `"default"`.

### Agents Configuration

Agents can be defined in YAML config and are seeded into the agent store on startup. API-created agents are persisted to the `store_path` JSON file.

```yaml
agents:
  store_path: "agents.json"         # Persistence file for API-created agents
  default_model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
  max_turns: 20                     # Default max tool-call loop turns
  specs:                            # Agents seeded from config
    research-assistant:
      model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
      description: "Research assistant with memory and web browsing"
      system_prompt: |
        You are a research assistant with long-term memory.
        Always search memory before saying you don't know something.
      primitives:
        memory:
          enabled: true
          namespace: "agent:{agent_name}:{session_id}"
        browser:
          enabled: true
      hooks:
        auto_memory: true
        auto_trace: true
```

Pre-built agent configs are in `configs/`:
- `agents-agentcore.yaml` -- all primitives backed by AgentCore
- `agents-mem0-langfuse.yaml` -- mem0 + Milvus memory, Langfuse tracing, Selenium Grid browser
- `agents-mixed.yaml` -- mem0/Langfuse for memory/observability, AgentCore for code/browser/identity/tools

---

## Header-Based Provider Routing

Requests can select which named backend to use at runtime via HTTP headers. This allows different agents or users to route to different backends without changing server configuration.

### Headers

| Header | Scope | Description |
|--------|-------|-------------|
| `X-Provider` | All primitives | Set the default provider name for all primitives on this request. |
| `X-Provider-Memory` | Memory only | Override the provider for memory operations. |
| `X-Provider-Identity` | Identity only | Override the provider for identity operations. |
| `X-Provider-Code-Interpreter` | Code Interpreter only | Override the provider for code interpreter operations. |
| `X-Provider-Browser` | Browser only | Override the provider for browser operations. |
| `X-Provider-Observability` | Observability only | Override the provider for observability operations. |
| `X-Provider-Gateway` | Gateway only | Override the provider for gateway operations. |
| `X-Provider-Tools` | Tools only | Override the provider for tools operations. |

### Resolution Order

1. Primitive-specific header (e.g., `X-Provider-Memory`)
2. Global header (`X-Provider`)
3. Configured default for the primitive

### Examples

Route all primitives to the `agentcore` backend:

```bash
curl -H "X-Provider: agentcore" http://localhost:8000/api/v1/memory/global
```

Route memory to `in_memory` but let everything else use the configured default:

```bash
curl -H "X-Provider-Memory: in_memory" http://localhost:8000/api/v1/memory/global
```

Route memory to `mem0` while routing identity to `agentcore`:

```bash
curl -H "X-Provider-Memory: mem0" \
     -H "X-Provider-Identity: agentcore" \
     http://localhost:8000/api/v1/memory/global
```

If an unknown provider name is specified, the server returns HTTP 400 with the list of available backends for that primitive.

---

## Swapping Backends

To change which backend a primitive uses, update the config. The platform dynamically imports provider classes at startup. With the multi-provider format, you can configure multiple backends and switch between them at runtime via headers, or set a different default.

All AgentCore providers require `pip install agentic-primitives-gateway[agentcore]` and use **per-request credential pass-through** -- the server forwards client-supplied `X-AWS-*` headers to AgentCore. See [AWS Credential Pass-Through](#aws-credential-pass-through).

### Memory

#### In-Memory (dev/test)

```yaml
memory:
  backend: "agentic_primitives_gateway.primitives.memory.in_memory.InMemoryProvider"
  config: {}
```

No external dependencies. Data lives in process memory and is lost on restart. Supports key-value operations, conversation events, session management, and branch forking.

#### mem0 + Milvus

Requires: `pip install agentic-primitives-gateway[mem0]`

```yaml
memory:
  backend: "agentic_primitives_gateway.primitives.memory.mem0_provider.Mem0MemoryProvider"
  config:
    vector_store:
      provider: milvus
      config:
        collection_name: agentic_memories
        url: "http://milvus:19530"
    llm:
      provider: aws_bedrock
      config:
        model: us.anthropic.claude-sonnet-4-20250514-v1:0
    embedder:
      provider: aws_bedrock
      config:
        model: amazon.titan-embed-text-v2:0
```

mem0 provides semantic memory with automatic extraction and deduplication. Uses Bedrock for LLM calls and Milvus for vector storage. The `vector_store.provider` can be changed to `weaviate`, `qdrant`, `chroma`, or any other backend that mem0 supports.

#### AgentCore

```yaml
memory:
  backend: "agentic_primitives_gateway.primitives.memory.agentcore.AgentCoreMemoryProvider"
  config:
    region: "us-east-1"
```

AWS-managed memory with full support for all operations including memory resource lifecycle and strategy management. The `memory_id` can be set per-request via `X-Cred-Agentcore-Memory-Id` header or in provider config.

### Identity

#### AgentCore

```yaml
identity:
  backend: "agentic_primitives_gateway.primitives.identity.agentcore.AgentCoreIdentityProvider"
  config:
    region: "us-east-1"
```

AWS-managed identity with workload tokens, credential provider CRUD, and workload identity management. Supports M2M and 3-legged OAuth flows.

#### Keycloak

Requires: `pip install agentic-primitives-gateway[keycloak]`

```yaml
identity:
  backend: "agentic_primitives_gateway.primitives.identity.keycloak.KeycloakIdentityProvider"
  config:
    server_url: "http://keycloak:8080"
    realm: "agents"
    client_id: "agentic-gateway"
    client_secret: "${KEYCLOAK_CLIENT_SECRET}"
```

OpenID Connect identity provider using Keycloak. Supports token exchange, API keys via custom attributes, credential provider CRUD via Admin REST API, and workload identity management.

#### Microsoft Entra ID (Azure AD)

Requires: `pip install agentic-primitives-gateway[entra]`

```yaml
identity:
  backend: "agentic_primitives_gateway.primitives.identity.entra.EntraIdentityProvider"
  config:
    tenant_id: "${AZURE_TENANT_ID}"
    client_id: "${AZURE_CLIENT_ID}"
    client_secret: "${AZURE_CLIENT_SECRET}"
```

Microsoft Entra ID identity provider using MSAL and Microsoft Graph API. Supports client credential flows, token exchange, and application/service principal management.

#### Okta

Requires: `pip install agentic-primitives-gateway[okta]`

```yaml
identity:
  backend: "agentic_primitives_gateway.primitives.identity.okta.OktaIdentityProvider"
  config:
    domain: "${OKTA_DOMAIN}"
    client_id: "${OKTA_CLIENT_ID}"
    client_secret: "${OKTA_CLIENT_SECRET}"
    api_token: "${OKTA_API_TOKEN}"
    auth_server: "default"
```

Okta identity provider using OAuth2 endpoints and the Okta Management API. Supports token exchange, API key retrieval via user profiles, and application management.

### Code Interpreter

#### AgentCore

```yaml
code_interpreter:
  backend: "agentic_primitives_gateway.primitives.code_interpreter.agentcore.AgentCoreCodeInterpreterProvider"
  config:
    region: "us-east-1"
```

AWS-managed sandboxed code execution with session management, execution history, and file I/O.

#### Jupyter

Requires: `pip install agentic-primitives-gateway[jupyter]`

```yaml
code_interpreter:
  backend: "agentic_primitives_gateway.primitives.code_interpreter.jupyter.JupyterCodeInterpreterProvider"
  config:
    base_url: "${JUPYTER_URL:=http://localhost:8888}"
    token: "${JUPYTER_TOKEN:=}"
    kernel_name: "python3"
    execution_timeout: 30.0
    file_root: "/tmp"
```

Code execution via a Jupyter Server or Enterprise Gateway. Each session creates a kernel with persistent state across calls. Uses WebSocket for execution and kernel-based file I/O (works without the Jupyter Contents REST API).

### Browser

#### AgentCore

```yaml
browser:
  backend: "agentic_primitives_gateway.primitives.browser.agentcore.AgentCoreBrowserProvider"
  config:
    region: "us-east-1"
```

AWS-managed browser automation using Playwright over CDP. Supports session lifecycle, navigation, screenshots, clicking, typing, JavaScript evaluation, and live view URLs.

#### Selenium Grid

Requires: `pip install agentic-primitives-gateway[selenium]`

```yaml
browser:
  backend: "agentic_primitives_gateway.primitives.browser.selenium_grid.SeleniumGridBrowserProvider"
  config:
    hub_url: "${SELENIUM_HUB_URL:=http://localhost:4444}"
    browser: "chrome"
```

Self-hosted browser automation via Selenium WebDriver. Connects to a Selenium Grid hub and creates browser sessions on demand. Supports Chrome, Firefox, and Edge. Good for air-gapped environments where cloud browser services are not available.

### Observability

#### Langfuse

Requires: `pip install agentic-primitives-gateway[langfuse]`

```yaml
observability:
  backend: "agentic_primitives_gateway.primitives.observability.langfuse.LangfuseObservabilityProvider"
  config:
    public_key: "${LANGFUSE_PUBLIC_KEY:=}"
    secret_key: "${LANGFUSE_SECRET_KEY:=}"
    base_url: "${LANGFUSE_BASE_URL:=https://cloud.langfuse.com}"
```

Full observability via Langfuse. Supports trace/log ingestion, LLM generation tracking, evaluation scoring, session management, trace retrieval, and flush. Credentials can be overridden per-request via `X-Cred-Langfuse-*` headers.

#### AgentCore

```yaml
observability:
  backend: "agentic_primitives_gateway.primitives.observability.agentcore.AgentCoreObservabilityProvider"
  config:
    region: "us-east-1"
    service_name: "agentic-primitives-gateway"
    agent_id: "my-agent"
```

AWS-managed observability using ADOT (AWS Distro for OpenTelemetry) to send traces to CloudWatch/X-Ray. Supports trace ingestion, LLM generation logging, and flush.

### Gateway

#### Bedrock Converse

```yaml
gateway:
  backend: "agentic_primitives_gateway.primitives.gateway.bedrock.BedrockConverseProvider"
  config:
    region: "us-east-1"
    default_model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
```

LLM request routing via the AWS Bedrock Converse API. Supports tool_use, system prompts, and multi-turn conversations. The model can be overridden per-request in the completion body. Uses per-request AWS credential pass-through.

### Tools

#### AgentCore Gateway

```yaml
tools:
  backend: "agentic_primitives_gateway.primitives.tools.agentcore.AgentCoreGatewayProvider"
  config:
    region: "us-east-1"
    gateway_id: "${AGENTCORE_GATEWAY_ID:=}"
    gateway_url: "${AGENTCORE_GATEWAY_URL:=}"
```

Tool discovery and invocation via AWS Bedrock AgentCore Gateway using the MCP protocol. Provide either `gateway_id` (resolved to a URL at runtime) or `gateway_url` (direct endpoint). Can also be set per-request via `X-Cred-Agentcore-Gateway-Id` or `X-Cred-Agentcore-Gateway-Url` headers.

#### MCP Registry

```yaml
tools:
  backend: "agentic_primitives_gateway.primitives.tools.mcp_registry.MCPRegistryProvider"
  config:
    base_url: "${MCP_REGISTRY_URL:=http://localhost:8080}"
    token: "${MCP_REGISTRY_TOKEN:=}"
    verify_ssl: true
```

Centralized tool discovery and invocation via an MCP Gateway Registry. Supports tool registration, search, invocation, deletion, and MCP server management. The registry URL and auth token can be overridden per-request via `X-Cred-Mcp-Registry-Url` and `X-Cred-Mcp-Registry-Token` headers.

### Policy

#### AgentCore

```yaml
policy:
  backend: "agentic_primitives_gateway.primitives.policy.agentcore.AgentCorePolicyProvider"
  config:
    region: "us-east-1"
```

Cedar-based policy management via AWS Bedrock AgentCore. Supports policy engine CRUD, policy CRUD, and optional policy generation (auto-generate policies from agent behavior). Policy definitions are normalized to raw Cedar strings on read.

#### Noop (In-Memory)

```yaml
policy:
  backend: "agentic_primitives_gateway.primitives.policy.noop.NoopPolicyProvider"
  config: {}
```

In-memory policy store for development and testing. Supports all CRUD operations but data is lost on restart. Policy generation is not supported.

### Evaluations

#### AgentCore

```yaml
evaluations:
  backend: "agentic_primitives_gateway.primitives.evaluations.agentcore.AgentCoreEvaluationsProvider"
  config:
    region: "us-east-1"
```

LLM-as-a-judge evaluations via AWS Bedrock AgentCore. Uses `bedrock-agentcore-control` for evaluator CRUD and `bedrock-agentcore` for runtime evaluation. Supports built-in evaluators (`Builtin.Helpfulness`, `Builtin.Coherence`, etc.), custom evaluators, and online evaluation configs.

#### Noop (In-Memory)

```yaml
evaluations:
  backend: "agentic_primitives_gateway.primitives.evaluations.noop.NoopEvaluationsProvider"
  config: {}
```

In-memory evaluator store for development and testing. Returns placeholder evaluation results. Online evaluation configs are not supported.

### Enforcement

#### Cedar (cedarpy)

Requires: `pip install agentic-primitives-gateway[cedar]`

```yaml
enforcement:
  backend: "agentic_primitives_gateway.enforcement.cedar.CedarPolicyEnforcer"
  config:
    policy_refresh_interval: 30
    engine_id: "my-engine"   # optional: scope to a single policy engine
```

Local Cedar policy evaluation via the Rust-backed `cedarpy` library. Reads policies from whichever `PolicyProvider` is configured (noop, AgentCore) and evaluates authorization requests at sub-millisecond latency. Background task refreshes policies every N seconds. Default-deny when active: no loaded policies = all requests blocked. See the [Policy Enforcement](#policy-enforcement) section for details.

#### Noop (default)

```yaml
enforcement:
  backend: "agentic_primitives_gateway.enforcement.noop.NoopPolicyEnforcer"
  config: {}
```

No enforcement -- all requests are allowed. This is the default when no enforcement is configured. The gateway behaves identically to before enforcement was added.

---

## Extending with Custom Providers

### 1. Implement the abstract base class

All provider ABCs are in `agentic_primitives_gateway.primitives.base`. Each defines async methods and a `healthcheck()` method.

Example -- a Redis-backed memory provider:

```python
# my_company/providers/redis_memory.py
from agentic_primitives_gateway.primitives.base import MemoryProvider
from agentic_primitives_gateway.models.memory import MemoryRecord, SearchResult

class RedisMemoryProvider(MemoryProvider):
    def __init__(self, redis_url: str = "redis://localhost:6379", **kwargs):
        self._redis = Redis.from_url(redis_url)

    async def store(self, namespace, key, content, metadata=None):
        # ...implement...

    async def retrieve(self, namespace, key):
        # ...implement...

    async def search(self, namespace, query, top_k=10, filters=None):
        # ...implement...

    async def delete(self, namespace, key):
        # ...implement...

    async def list_memories(self, namespace, filters=None, limit=100, offset=0):
        # ...implement...

    async def healthcheck(self):
        return self._redis.ping()
```

### 2. Configure the backend

```yaml
memory:
  default: "redis"
  backends:
    redis:
      backend: "my_company.providers.redis_memory.RedisMemoryProvider"
      config:
        redis_url: "redis://redis:6379/0"
```

The class is loaded via `importlib.import_module`, so it must be importable from the Python path. If packaging as a separate wheel, install it alongside `agentic-primitives-gateway`.

### 3. Provider contract

Every provider must:
- Accept `**kwargs` in `__init__` (config values are passed as keyword arguments)
- Implement all abstract methods from the base class
- Return the expected types (Pydantic models for memory, dicts for others)
- Implement `healthcheck()` -- called by the `/readyz` endpoint

---

## Running Locally

### Prerequisites

- Python 3.11+

### Install and run

```bash
# Install the server with dev dependencies
pip install -e ".[dev]"

# Run with default in-memory providers
uvicorn agentic_primitives_gateway.main:app --reload

# Or with a config file
AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE=config.yaml uvicorn agentic_primitives_gateway.main:app --reload
```

Open http://localhost:8000/docs for the Swagger UI.

### Run tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

The test suite contains 634 unit/system tests plus 42 integration tests covering all primitives, provider routing, and AWS credential pass-through.

---

## Client Library

The client is a separate package located at `client/` in the repository. Install it with:

```bash
pip install agentic-primitives-gateway-client
```

Or install from the local checkout:

```bash
pip install -e client/
```

Usage:

```python
from agentic_primitives_gateway_client import AgenticPlatformClient

client = AgenticPlatformClient(
    "http://agentic-primitives-gateway:8000",
    aws_access_key_id="AKIA...",
    aws_secret_access_key="...",
    aws_session_token="...",       # optional
    aws_region="us-east-1",        # optional
)
```

Credentials can be updated on the fly (e.g., after token refresh):

```python
client.set_aws_credentials(
    access_key_id=new_key,
    secret_access_key=new_secret,
    session_token=new_token,
)
```

---

## Deploying to Kubernetes

### Build the Docker image

```bash
docker build -t agentic-primitives-gateway:latest .

# To include mem0/Milvus support:
# Add mem0ai and pymilvus to the Dockerfile or use a build arg

# To include AgentCore support:
# Add bedrock-agentcore to the Dockerfile or use a build arg
```

### Deploy with Helm

```bash
cd deploy/helm

# Deploy with defaults
helm install agentic-primitives-gateway ./agentic-primitives-gateway

# Deploy with custom values
helm install agentic-primitives-gateway ./agentic-primitives-gateway -f my-values.yaml

# Upgrade after config changes
helm upgrade agentic-primitives-gateway ./agentic-primitives-gateway -f my-values.yaml
```

### Helm Values Reference

```yaml
replicaCount: 1

image:
  repository: agentic-primitives-gateway
  tag: latest
  pullPolicy: IfNotPresent

service:
  type: ClusterIP    # ClusterIP, NodePort, or LoadBalancer
  port: 8000

resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    cpu: 500m
    memory: 512Mi

# Allow server-side credential fallback (default: false)
allow_server_credentials: false

# Provider configuration -- rendered into a ConfigMap mounted at
# /etc/agentic-primitives-gateway/config.yaml
providers:
  memory:
    default: "mem0"
    backends:
      mem0:
        backend: "agentic_primitives_gateway.primitives.memory.mem0_provider.Mem0MemoryProvider"
        config:
          vector_store:
            provider: milvus
            config:
              collection_name: agentic_memories
              url: "http://milvus:19530"
          llm:
            provider: aws_bedrock
            config:
              model: us.anthropic.claude-sonnet-4-20250514-v1:0
          embedder:
            provider: aws_bedrock
            config:
              model: amazon.titan-embed-text-v2:0
      in_memory:
        backend: "agentic_primitives_gateway.primitives.memory.in_memory.InMemoryProvider"
        config: {}
      # agentcore:
      #   backend: "agentic_primitives_gateway.primitives.memory.agentcore.AgentCoreMemoryProvider"
      #   config:
      #     region: "us-east-1"
  observability:
    default: "noop"
    backends:
      noop:
        backend: "agentic_primitives_gateway.primitives.observability.noop.NoopObservabilityProvider"
        config: {}
      # langfuse:
      #   backend: "agentic_primitives_gateway.primitives.observability.langfuse.LangfuseObservabilityProvider"
      #   config: {}
      # agentcore:
      #   backend: "agentic_primitives_gateway.primitives.observability.agentcore.AgentCoreObservabilityProvider"
      #   config:
      #     region: "us-east-1"
  identity:
    default: "noop"
    backends:
      noop:
        backend: "agentic_primitives_gateway.primitives.identity.noop.NoopIdentityProvider"
        config: {}
      # agentcore:
      #   backend: "agentic_primitives_gateway.primitives.identity.agentcore.AgentCoreIdentityProvider"
      #   config:
      #     region: "us-east-1"
  code_interpreter:
    default: "noop"
    backends:
      noop:
        backend: "agentic_primitives_gateway.primitives.code_interpreter.noop.NoopCodeInterpreterProvider"
        config: {}
      # agentcore:
      #   backend: "agentic_primitives_gateway.primitives.code_interpreter.agentcore.AgentCoreCodeInterpreterProvider"
      #   config:
      #     region: "us-east-1"
  browser:
    default: "noop"
    backends:
      noop:
        backend: "agentic_primitives_gateway.primitives.browser.noop.NoopBrowserProvider"
        config: {}
      # agentcore:
      #   backend: "agentic_primitives_gateway.primitives.browser.agentcore.AgentCoreBrowserProvider"
      #   config:
      #     region: "us-east-1"
  gateway:
    default: "noop"
    backends:
      noop:
        backend: "agentic_primitives_gateway.primitives.gateway.noop.NoopGatewayProvider"
        config: {}
  tools:
    default: "noop"
    backends:
      noop:
        backend: "agentic_primitives_gateway.primitives.tools.noop.NoopToolsProvider"
        config: {}
```

### How It Works in K8s

1. The Helm chart creates a **ConfigMap** from the `providers` values, rendered as a YAML file.
2. The **Deployment** mounts this ConfigMap at `/etc/agentic-primitives-gateway/config.yaml` and sets `AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE` to point to it.
3. On startup, the app loads the config file and initializes all providers.
4. **Liveness probe** hits `/healthz` -- always returns 200 if the process is alive.
5. **Readiness probe** hits `/readyz` -- returns 200 only if all provider healthchecks pass.
6. The ConfigMap has a **checksum annotation** on the pod spec, so changing provider config triggers a rolling restart.

### AWS Credential Pass-Through

The server **does not use its own AWS credentials** for AgentCore calls. Instead, credentials are passed through from the client on every request via HTTP headers:

| Header | Required | Description |
|--------|----------|-------------|
| `X-AWS-Access-Key-Id` | Yes (for AgentCore) | AWS access key ID |
| `X-AWS-Secret-Access-Key` | Yes (for AgentCore) | AWS secret access key |
| `X-AWS-Session-Token` | No | STS session token (for temporary credentials) |
| `X-AWS-Region` | No | Override the provider's default region |
| `X-Agent-Id` | No | Agent identity for policy enforcement (Cedar principal) |

**How it works:**

1. The `RequestContextMiddleware` in `main.py` extracts these headers on every request.
2. The credentials are stored in a request-scoped `contextvars.ContextVar` (defined in `context.py`).
3. AgentCore providers call `get_boto3_session()` from `context.py` on each operation, which creates a `boto3.Session` with the caller's credentials.
4. If no credentials are in the headers, the providers fall back to the server environment's default credential chain (env vars, instance profile, etc.).

This means:
- **Each agent authenticates with its own AWS identity** -- no shared service credentials.
- **The server is stateless** with respect to AWS auth -- it is a pure pass-through.
- **Agents running in AgentCore Runtime** can forward their workload access tokens.
- **Agents running elsewhere** can use STS temporary credentials from `AssumeRole`.

### Service Credential Pass-Through

For non-AWS services (Langfuse, OpenAI, etc.), the platform supports a generic credential pass-through via `X-Cred-{Service}-{Key}` headers. The middleware parses these into per-service credential dicts that providers read from context.

| Header pattern | Parsed as |
|----------------|-----------|
| `X-Cred-Langfuse-Public-Key: pk-...` | `{"langfuse": {"public_key": "pk-..."}}` |
| `X-Cred-Langfuse-Secret-Key: sk-...` | `{"langfuse": {..., "secret_key": "sk-..."}}` |
| `X-Cred-Agentcore-Memory-Id: mem-123` | `{"agentcore": {"memory_id": "mem-123"}}` |
| `X-Cred-Agentcore-Gateway-Url: https://...` | `{"agentcore": {..., "gateway_url": "https://..."}}` |
| `X-Cred-Mcp-Registry-Token: eyJ...` | `{"mcp_registry": {"token": "eyJ..."}}` |
| `X-Cred-Mcp-Registry-Url: http://...` | `{"mcp_registry": {"url": "http://..."}}` |

Providers call `get_service_credentials("langfuse")` from `context.py` to read their credentials. If no credentials are in the headers, providers fall back to config-level defaults.

The client handles this via `set_service_credentials()`:

```python
# Langfuse observability
client.set_service_credentials("langfuse", {
    "public_key": "pk-...",
    "secret_key": "sk-...",
    "base_url": "https://cloud.langfuse.com",
})

# AgentCore Gateway (tools)
client.set_service_credentials("agentcore", {
    "gateway_url": "https://gw-id.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
    "gateway_token": "...",
})

# MCP Gateway Registry (tools)
client.set_service_credentials("mcp_registry", {
    "url": "http://mcp-registry:8080",
    "token": "eyJhbGciOiJSUzI1NiIs...",  # JWT token
})
```

### AgentCore Memory ID Resolution

The `AgentCoreMemoryProvider` resolves `memory_id` per-request in this order:

1. **Client header** `X-Cred-Agentcore-Memory-Id` (via `set_service_credentials("agentcore", {"memory_id": "..."})`)
2. **Config default** -- if `memory_id` is set in the provider's config block
3. **Error** -- raises a clear error instructing the user to provide a memory_id. AgentCore memory IDs must be created via the AgentCore console or API first.

---

## Multi-Tenancy

The gateway can serve multiple agents, users, or teams from a single deployment. Some isolation mechanisms work out of the box; others require additional configuration or an authenticating reverse proxy.

### What works today

**Request-scoped credential isolation.** AWS credentials (`X-AWS-*`), service credentials (`X-Cred-*`), and provider routing (`X-Provider-*`) are stored in Python `contextvars` scoped to the current request. One tenant's credentials never leak to another tenant's request, even under concurrent load. This is the foundation of the gateway's multi-tenancy model.

**Per-request provider routing.** Different tenants can route to different backends on the same request via `X-Provider-*` headers. For example, tenant A can use `mem0` for memory while tenant B uses `agentcore`, without any server-side configuration changes.

**Cedar policy enforcement.** When the `CedarPolicyEnforcer` is active, each request is evaluated against Cedar policies using the caller's principal (`X-Agent-Id`). Policies can scope access per agent, per action, and per resource. Cedar's `forbid` overrides `permit`, so you can grant broad access and then carve out restrictions. See [Policy Enforcement](#policy-enforcement).

**Memory namespace isolation.** Memory operations are scoped by namespace (`/api/v1/memory/{namespace}`). Tenants using different namespaces (e.g., `agent:tenant-a:session-1` vs `agent:tenant-b:session-1`) cannot read each other's data. This is a convention -- the gateway does not enforce namespace boundaries unless Cedar policies are configured to do so.

**Per-request AWS identity.** AgentCore providers create a fresh `boto3.Session` per request using the caller's credentials. Each agent authenticates with its own AWS identity -- there are no shared service credentials (unless `allow_server_credentials` is enabled as a fallback).

**Stateless server.** The gateway holds no tenant-specific state in memory between requests (aside from the in-memory providers, which are dev-only). In Kubernetes, any replica can serve any tenant's request.

### What requires configuration

**Policy enforcement must be explicitly enabled.** The default is `NoopPolicyEnforcer` (all requests allowed). To enforce per-tenant access control, configure `CedarPolicyEnforcer` and create policies that scope access by principal. Without enforcement, any caller can access any primitive with any namespace.

**External backend providers are recommended.** The in-memory providers (`InMemoryProvider`, `NoopPolicyProvider`, `NoopEvaluationsProvider`) share a single process-global store -- all tenants see the same data. For multi-tenant deployments, use external backends (AgentCore, mem0+Milvus, Langfuse) where data isolation is handled by the backend itself.

**Agent definitions are shared.** The `FileAgentStore` exposes all agent specs to all callers via the `/api/v1/agents` API. Any caller can create, read, update, or delete any agent. To restrict agent management, use Cedar policies scoping `agents:create_agent`, `agents:update_agent`, and `agents:delete_agent` to specific principals.

### What does NOT work today (known gaps)

**No built-in authentication.** The gateway does not verify caller identity. Headers like `X-Agent-Id`, `X-AWS-*`, and `X-Cred-*` are trusted as-is. In a multi-tenant production deployment, place an authenticating reverse proxy (e.g., AWS ALB with Cognito, Envoy with OAuth2, or an API gateway with JWT validation) in front of the gateway to validate identity before requests reach it. The reverse proxy should set `X-Agent-Id` based on the verified identity.

**No tenant-scoped metrics.** Prometheus metrics (`/metrics`) are aggregated across all tenants. There is no per-tenant breakdown of request counts, latencies, or error rates. If you need tenant-level observability, use the `X-Agent-Id` header in your proxy's access logs or configure per-tenant Langfuse projects via `X-Cred-Langfuse-*` headers.

**No tenant-level rate limiting or quotas.** The gateway does not limit requests per tenant. Rate limiting should be handled by the reverse proxy or API gateway in front of the gateway.

**No tenant-scoped agent store.** Agent specs are global -- there is no concept of "tenant A's agents" vs "tenant B's agents" at the storage level. Cedar policies can restrict who can manage which agents, but the underlying store is shared.

### Deployment patterns

**Single-tenant (simplest).** One gateway instance per tenant. No authentication needed. Each instance has its own config, agent store, and credentials. Suitable for development, single-team use, or when each tenant runs in a separate Kubernetes namespace.

**Multi-tenant with authenticating proxy.** One shared gateway behind a reverse proxy that validates identity (JWT, OAuth2, mTLS) and sets `X-Agent-Id`. Cedar policies enforce per-tenant access. External backends (AgentCore, Langfuse) provide data isolation. This is the recommended production pattern.

```
Client → Auth Proxy (validate JWT, set X-Agent-Id)
       → Gateway (Cedar enforcement, provider routing)
       → External backends (AgentCore, Milvus, Langfuse)
```

**Multi-tenant with per-tenant credentials.** Each tenant sends their own AWS credentials (`X-AWS-*`) or service credentials (`X-Cred-*`). The gateway forwards them to backends. Tenants are isolated at the backend level (separate AWS accounts, separate Langfuse projects). No Cedar enforcement needed if backend-level isolation is sufficient.

---

## Project Structure

```
agentic-primitives-gateway/
├── src/agentic_primitives_gateway/
│   ├── main.py                     # FastAPI app, RequestContextMiddleware, provider discovery
│   ├── config.py                   # Settings (pydantic-settings), multi-provider config parsing
│   ├── context.py                  # Request-scoped AWS credentials and provider routing context vars
│   ├── registry.py                 # Provider registry -- loads named backends, resolves per-request
│   ├── metrics.py                  # Prometheus MetricsProxy wrapper for all providers
│   ├── watcher.py                  # Config file watcher for hot-reload (K8s ConfigMap aware)
│   ├── routes/
│   │   ├── health.py               # /healthz, /readyz
│   │   ├── memory.py               # /api/v1/memory/* (22 endpoints)
│   │   ├── identity.py             # /api/v1/identity/* (14 endpoints)
│   │   ├── code_interpreter.py     # /api/v1/code-interpreter/* (8 endpoints)
│   │   ├── browser.py              # /api/v1/browser/* (11 endpoints)
│   │   ├── observability.py        # /api/v1/observability/* (11 endpoints)
│   │   ├── gateway.py              # /api/v1/gateway/* (2 endpoints)
│   │   ├── tools.py                # /api/v1/tools/* (9 endpoints)
│   │   └── agents.py               # /api/v1/agents/* (6 endpoints: CRUD + chat)
│   ├── agents/                     # Declarative agent orchestration
│   │   ├── runner.py               # LLM tool-call loop with auto-memory/trace hooks
│   │   ├── tools.py                # Tool registry: 15 tools across 5 primitives
│   │   └── store.py                # Agent spec persistence (FileAgentStore)
│   ├── models/                     # Pydantic request/response models per primitive
│   │   └── agents.py               # AgentSpec, ChatRequest, ChatResponse
│   └── primitives/
│       ├── base.py                 # Re-exports all provider ABCs
│       ├── _sync.py                # SyncRunnerMixin (shared executor helper for sync backends)
│       ├── memory/
│       │   ├── noop.py             # No-op (logs only)
│       │   ├── in_memory.py        # Dict-based (dev/test)
│       │   ├── mem0_provider.py    # mem0 + Milvus
│       │   └── agentcore.py        # AWS Bedrock AgentCore
│       ├── identity/
│       │   ├── noop.py
│       │   ├── agentcore.py        # AWS Bedrock AgentCore
│       │   ├── keycloak.py         # Keycloak
│       │   ├── entra.py            # Microsoft Entra (Azure AD)
│       │   └── okta.py             # Okta
│       ├── code_interpreter/
│       │   ├── noop.py
│       │   ├── agentcore.py        # AWS Bedrock AgentCore
│       │   └── jupyter.py          # Jupyter Server / Enterprise Gateway
│       ├── browser/
│       │   ├── noop.py
│       │   ├── agentcore.py        # AWS Bedrock AgentCore
│       │   └── selenium_grid.py    # Selenium Grid (self-hosted)
│       ├── observability/
│       │   ├── noop.py
│       │   ├── langfuse.py         # Langfuse (SDK v3)
│       │   └── agentcore.py        # AWS AgentCore via OpenTelemetry
│       ├── gateway/
│       │   ├── noop.py
│       │   └── bedrock.py          # AWS Bedrock Converse API (tool_use support)
│       └── tools/
│           ├── noop.py
│           ├── agentcore.py        # AWS AgentCore Gateway (MCP-compatible)
│           └── mcp_registry.py     # MCP Registry
├── client/                         # Standalone Python client (separate package: agentic-primitives-gateway-client)
├── tests/                          # Server tests (unit/system + integration)
├── configs/                        # YAML presets (local, agentcore, kitchen-sink, agents-*)
├── deploy/helm/agentic-primitives-gateway/   # Helm chart
├── Dockerfile                      # Multi-stage build
└── pyproject.toml
```
