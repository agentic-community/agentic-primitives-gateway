# AgentCore Tools

AWS-managed tools provider backed by Bedrock AgentCore's MCP Gateway for tool discovery and invocation.

## Configuration

### Server-Side (Gateway Config)

```yaml
providers:
  tools:
    backend: "agentic_primitives_gateway.primitives.tools.agentcore.AgentCoreGatewayProvider"
    config:
      region: "us-east-1"
      gateway_id: "your-gateway-id"
      gateway_url: "https://gw-id.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `region` | `us-east-1` | AWS region |
| `gateway_id` | (none) | AgentCore gateway ID |
| `gateway_url` | (none) | Direct gateway URL (alternative to `gateway_id`) |

### Per-User Credentials

Users can provide their own gateway credentials:

```python
client.set_service_credentials("mcp_gateway", {
    "url": "https://gw-id.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
    "token": "...",
})
```

## Using the Tools API

### List Tools

```bash
curl http://localhost:8000/api/v1/tools
```

### Search Tools

```bash
curl "http://localhost:8000/api/v1/tools/search?query=weather"
```

### Invoke a Tool

```bash
curl -X POST http://localhost:8000/api/v1/tools/tool-name/invoke \
  -H "Content-Type: application/json" \
  -d '{"params": {"city": "Seattle"}}'
```

## Using with Declarative Agents

```yaml
agents:
  specs:
    tool-agent:
      model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
      primitives:
        tools:
          enabled: true
```

## Backend Comparison

See the [MCP Gateway Registry](mcp-gateway-registry.md) page for a comparison table between tools backends.

## Prerequisites

- `pip install agentic-primitives-gateway[agentcore]`
- AWS credentials with AgentCore access
- An AgentCore MCP gateway configured with MCP servers
