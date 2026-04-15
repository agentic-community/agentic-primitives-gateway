# MCP Gateway Registry Integration

The [MCP Gateway Registry](https://github.com/agentic-community/mcp-gateway-registry) is a self-hosted service for centralized MCP tool discovery and invocation. The gateway integrates with it as a first-class tools provider backend, giving your agents access to any MCP server registered in the registry.

## What It Does

The MCP Gateway Registry acts as a central hub for MCP servers. Instead of connecting agents directly to individual MCP servers, you register them in the registry and the gateway discovers and invokes tools through it.

```
Agent  -->  Gateway (tools primitive)  -->  MCP Gateway Registry  -->  MCP Server A
                                                                  -->  MCP Server B
                                                                  -->  MCP Server C
```

Key capabilities:

- **Tool discovery** -- list and search tools across all registered MCP servers
- **Tool invocation** -- invoke any tool through the registry's MCP streamable-http proxy
- **Server management** -- register, list, and inspect MCP servers
- **Semantic search** -- find tools by description or capability (when the registry supports it)
- **Health-aware routing** -- only healthy servers are included in tool listings

## Configuration

### Server-Side (Gateway Config)

Add the `MCPRegistryProvider` as a tools backend in your gateway config YAML:

```yaml
providers:
  tools:
    default: "mcp_registry"
    backends:
      mcp_registry:
        backend: "agentic_primitives_gateway.primitives.tools.mcp_registry.MCPRegistryProvider"
        config:
          base_url: "${MCP_REGISTRY_URL:=http://localhost:8080}"
          verify_ssl: false  # Set true for production
```

The `kitchen-sink.yaml` and `e2e-selfhosted-langchain.yaml` configs already include this backend.

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_REGISTRY_URL` | `http://localhost:8080` | Base URL of the MCP Gateway Registry |
| `MCP_REGISTRY_TOKEN` | (none) | JWT token for registry authentication |

### Per-User Credentials

In multi-tenant deployments, each user can provide their own registry credentials via headers:

```bash
curl -H "X-Cred-Mcp-Registry-Url: https://my-registry.internal:8080" \
     -H "X-Cred-Mcp-Registry-Token: eyJ..." \
     http://localhost:8000/api/v1/tools
```

Or configure them in Keycloak using the `apg.*` convention:

| User Attribute | Maps To |
|----------------|---------|
| `apg.mcp_registry.url` | Registry base URL |
| `apg.mcp_registry.token` | JWT token |

These can be managed from the gateway's Settings page in the web UI.

**Credential resolution order:**

1. Client headers (`X-Cred-Mcp-Registry-*`) -- always win
2. OIDC-resolved attributes (`apg.mcp_registry.*`) -- per-user
3. Provider config / environment variables -- server ambient

## Using the Tools API

Once configured, all tools from the registry are available through the standard tools endpoints.

### List All Tools

```bash
curl http://localhost:8000/api/v1/tools
```

Tools are returned with a `server_title/tool_name` naming format:

```json
[
  {
    "name": "weather-service/get_forecast",
    "description": "Get weather forecast for a location",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
    "metadata": {"server": "weather-service", "server_path": "/proxy/weather-service"}
  }
]
```

### Search Tools

```bash
curl "http://localhost:8000/api/v1/tools/search?query=weather&max_results=5"
```

Uses semantic search when the registry supports it, otherwise falls back to keyword matching.

### Invoke a Tool

```bash
curl -X POST http://localhost:8000/api/v1/tools/weather-service%2Fget_forecast/invoke \
  -H "Content-Type: application/json" \
  -d '{"params": {"city": "Seattle"}}'
```

### List MCP Servers

```bash
curl http://localhost:8000/api/v1/tools/servers
```

### Register an MCP Server

```bash
curl -X POST http://localhost:8000/api/v1/tools/servers \
  -H "Content-Type: application/json" \
  -d '{"name": "my-server", "url": "https://my-mcp-server.example.com/mcp"}'
```

## Using with Declarative Agents

Agents configured with the tools primitive automatically get access to registry tools. Set the provider override to route the agent's tool calls through the registry:

```yaml
agents:
  specs:
    tool-user:
      model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
      description: "An agent with access to MCP registry tools"
      system_prompt: |
        You have access to tools from the MCP registry.
        Use search_tools to find relevant tools, then invoke them.
      primitives:
        tools:
          enabled: true
        memory:
          enabled: true
          namespace: "agent:{agent_name}"
      provider_overrides:
        tools: "mcp_registry"
```

## Using with the Python Client

### Provider Routing

```python
from agentic_primitives_gateway_client import AgenticPlatformClient

client = AgenticPlatformClient("http://localhost:8000", aws_from_environment=True)

# Route tools to MCP Registry
client.set_provider_for("tools", "mcp_registry")

# Set credentials (if not using OIDC or env vars)
client.set_service_credentials(
    "mcp_registry",
    {
        "url": "http://mcp-registry:8080",
        "token": "your-jwt-token",
    },
)

# List tools from the registry
tools = await client.list_tools()

# Invoke a tool
result = await client.invoke_tool(
    "weather-service/get_forecast",
    {"city": "Seattle"},
)
```

### Building a LangChain Agent with MCP Tools

See the full example in [examples/langchain-mcp-tools/](https://github.com/agentic-community/agentic-primitives-gateway/tree/main/examples/langchain-mcp-tools) for a complete LangChain agent that discovers and invokes tools dynamically from the registry.

## How It Works

The `MCPRegistryProvider` uses the MCP streamable-http transport protocol:

1. **Server discovery** -- calls `GET /v0.1/servers` on the registry to list available MCP servers and their proxy paths
2. **Endpoint discovery** -- probes each server's path (trying both `{path}` and `{path}/mcp`) with an `initialize` request to find the correct MCP endpoint
3. **Session management** -- maintains MCP sessions per server via the `Mcp-Session-Id` header, with a 5-minute cache TTL and automatic re-initialization on expiry
4. **Tool listing** -- sends `tools/list` JSON-RPC calls to each healthy server and aggregates results
5. **Tool invocation** -- sends `tools/call` JSON-RPC calls to the correct server, resolving the server from the `ServerTitle/tool_name` format

All MCP calls are synchronous HTTP (via `httpx`) wrapped in `SyncRunnerMixin` for async compatibility.

## Backend Comparison

| Feature | MCP Registry | AgentCore | Noop |
|---------|-------------|-----------|------|
| Register tool | yes | no | no-op |
| List/search tools | yes | yes | no-op |
| Invoke tool | yes | yes | no-op |
| Delete tool | yes | no | no-op |
| Server management | yes | no | no |
| Semantic search | yes (if supported) | no | no |

## Prerequisites

- A running [MCP Gateway Registry](https://github.com/agentic-community/mcp-gateway-registry) instance
- One or more MCP servers registered in the registry
- JWT token for registry authentication (if the registry requires it)
