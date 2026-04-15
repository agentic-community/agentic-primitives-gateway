# Selenium Grid Browser

Self-hosted browser automation provider using [Selenium Grid](https://www.selenium.dev/documentation/grid/). Provides full WebDriver-based browser control for agents.

## Configuration

### Server-Side (Gateway Config)

```yaml
providers:
  browser:
    backend: "agentic_primitives_gateway.primitives.browser.selenium_grid.SeleniumGridBrowserProvider"
    config:
      hub_url: "http://localhost:4444"
      browser: "chrome"
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `hub_url` | `http://localhost:4444` | Selenium Grid hub URL |
| `browser` | `chrome` | Browser type (`chrome`, `firefox`, `edge`) |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SELENIUM_HUB_URL` | `http://localhost:4444` | Selenium Grid hub URL |
| `SELENIUM_BROWSER` | `chrome` | Browser type |

## Running Selenium Grid Locally

```bash
# Standalone Chrome (simplest)
docker run -d -p 4444:4444 --shm-size=2g selenium/standalone-chrome:latest

# Or with Docker Compose for a full grid
docker compose -f deploy/docker-compose-selenium.yml up -d
```

## Using the Browser API

### Start a Session

```bash
curl -X POST http://localhost:8000/api/v1/browser/sessions \
  -H "Content-Type: application/json" \
  -d '{}'
```

### Navigate

```bash
curl -X POST http://localhost:8000/api/v1/browser/sessions/{session_id}/navigate \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'
```

### Read Page Content

```bash
curl http://localhost:8000/api/v1/browser/sessions/{session_id}/content
```

### Take a Screenshot

```bash
curl http://localhost:8000/api/v1/browser/sessions/{session_id}/screenshot
```

### Click, Type, Evaluate

```bash
# Click
curl -X POST http://localhost:8000/api/v1/browser/sessions/{session_id}/click \
  -d '{"selector": "#submit-button"}'

# Type
curl -X POST http://localhost:8000/api/v1/browser/sessions/{session_id}/type \
  -d '{"selector": "#search-input", "text": "agentic AI"}'

# Execute JavaScript
curl -X POST http://localhost:8000/api/v1/browser/sessions/{session_id}/evaluate \
  -d '{"expression": "document.title"}'
```

### Close Session

```bash
curl -X DELETE http://localhost:8000/api/v1/browser/sessions/{session_id}
```

## Using with Declarative Agents

```yaml
agents:
  specs:
    web-researcher:
      model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
      system_prompt: |
        You have web browsing capabilities.
        Use navigate to visit pages, read_page to extract content.
      primitives:
        browser:
          enabled: true
      provider_overrides:
        browser: "selenium_grid"
```

The agent automatically gets `navigate`, `read_page`, `click`, `type_text`, `screenshot`, and `evaluate_js` tools.

## Using with the Python Client

```python
from agentic_primitives_gateway_client import AgenticPlatformClient, Browser

client = AgenticPlatformClient("http://localhost:8000")
browser = Browser(client)

await browser.start()
await browser.navigate("https://example.com")
content = await browser.read_page()
screenshot = await browser.screenshot()
await browser.close()
```

## How It Works

1. **Session creation**: creates a new WebDriver session on the Selenium Grid hub
2. **Commands**: translates gateway browser commands to WebDriver protocol calls
3. **Session isolation** : each agent run gets its own browser session
4. **Cleanup**: sessions are tracked in the `SessionRegistry` and cleaned up when the agent run completes or on orphan detection

## Prerequisites

- `pip install agentic-primitives-gateway[selenium]`
- Running Selenium Grid instance
