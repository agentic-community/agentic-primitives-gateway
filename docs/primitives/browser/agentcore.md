# AgentCore Browser

AWS-managed browser automation provider backed by Bedrock AgentCore using Playwright.

## Configuration

### Server-Side (Gateway Config)

```yaml
providers:
  browser:
    backend: "agentic_primitives_gateway.primitives.browser.agentcore.AgentCoreBrowserProvider"
    config:
      region: "us-east-1"
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `region` | `us-east-1` | AWS region for AgentCore |

**Credentials:** sessions start under the request's AWS credentials, and every later AWS call (stop, live-view URL signing) is re-signed with the credentials of the request making it — sessions outlive short-lived STS credentials. Before **every** Playwright interaction (navigate, content, screenshot, click, type, evaluate), AgentCore must authorize the current request's AWS credentials to read the session and report status `READY`; authorization is never cached, even for the same access-key ID, because a key may be revoked or presented with a different secret. Under `allow_server_credentials: never`, interactions without caller AWS credentials are refused. The session's original region is retained for subsequent SDK calls. A failed remote stop keeps the session and Playwright connection usable so it can be retried; local resources close only after AgentCore confirms the stop.

## Using the Browser API

All standard browser endpoints work with this provider. See the [Browser API reference](../../api/browser.md) for the full endpoint list.

```bash
# Start a session
curl -X POST http://localhost:8000/api/v1/browser/sessions

# Navigate
curl -X POST http://localhost:8000/api/v1/browser/sessions/{id}/navigate \
  -d '{"url": "https://example.com"}'

# Read page content
curl http://localhost:8000/api/v1/browser/sessions/{id}/content
```

## Using with Declarative Agents

```yaml
agents:
  specs:
    web-agent:
      model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
      primitives:
        browser:
          enabled: true
```

## Prerequisites

- `pip install agentic-primitives-gateway[agentcore]`
- AWS credentials with AgentCore access
- Playwright browsers managed by AgentCore
