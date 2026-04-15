# Tools API

`/api/v1/tools`

Tool registration, discovery, invocation, and MCP server management. All endpoints require authentication.

**Backends:** `NoopToolsProvider`, `AgentCoreGatewayProvider`, [`MCPRegistryProvider`](../providers/mcp-gateway-registry.md)

## Tool Operations

| Method | Path | Description |
|---|---|---|
| `POST` | `/` | Register a tool. Returns 201. |
| `GET` | `/` | List registered tools. |
| `GET` | `/search` | Search tools by query. Query params: `query` (required), `max_results` (1--100, default 10). |
| `GET` | `/{name}` | Get a tool by name. Returns 404/501 if not found or not supported. |
| `DELETE` | `/{name}` | Delete a tool. Returns 204 or 501. |
| `POST` | `/{name}/invoke` | Invoke a tool. Body: `{"params": {...}}`. |

### Register a tool

```bash
curl -X POST http://localhost:8000/api/v1/tools \
  -H "Content-Type: application/json" \
  -d '{
    "name": "weather",
    "description": "Get current weather for a location",
    "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}}
  }'
```

### Invoke a tool

```bash
curl -X POST http://localhost:8000/api/v1/tools/weather/invoke \
  -H "Content-Type: application/json" \
  -d '{"params": {"city": "Seattle"}}'
```

**Response:**

```json
{
  "tool_name": "weather",
  "result": {"temperature": 62, "condition": "cloudy"},
  "status": "success"
}
```

Tool invocation returns 400 for invalid parameters, 502 if the tool execution fails.

## MCP Server Management

| Method | Path | Description |
|---|---|---|
| `GET` | `/servers` | List registered MCP servers with health status. Returns 501 if not supported. |
| `POST` | `/servers` | Register a new MCP server. Returns 201 or 501. |
| `GET` | `/servers/{server_name}` | Get server details. Returns 404/501. |

```bash
curl -X POST http://localhost:8000/api/v1/tools/servers \
  -H "Content-Type: application/json" \
  -d '{"name": "my-server", "url": "http://mcp-server:8080", "config": {}}'
```

## Backend Support

| Feature | MCPRegistry | AgentCore | Noop |
|---|---|---|---|
| Register tool | yes | no | no-op |
| List/search tools | yes | yes | no-op |
| Invoke tool | yes | yes | no-op |
| Delete tool | yes | no | no-op |
| Server management | yes | no | no |
