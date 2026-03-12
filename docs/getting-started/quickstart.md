# Quickstart

Get the gateway running locally in under 2 minutes.

## Prerequisites

- Python 3.11+
- Node.js 18+ (for the web UI, optional)

## Install

```bash
# Clone and install
git clone <repo-url>
cd agentic-primitives-gateway
pip install -e ".[dev]"
```

## Run with Local Config

The `local.yaml` config uses in-memory/noop providers -- no external services needed:

```bash
./run.sh local
# or:
AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE=configs/local.yaml \
  uvicorn agentic_primitives_gateway.main:app --reload --port 8000
```

The gateway starts at `http://localhost:8000`.

!!! note "Authentication"
    The default `local.yaml` config uses **noop auth** — full access with no credentials needed, ideal for local development. For production deployments, configure JWT/OIDC auth via a config like `configs/local-jwt.yaml`. You can also use API key auth:

    ```bash
    curl -H "Authorization: Bearer sk-dev-key" http://localhost:8000/api/v1/agents
    ```

## Verify It Works

```bash
# Health check
curl http://localhost:8000/healthz
# {"status":"ok"}

# List providers
curl http://localhost:8000/api/v1/providers
# {"memory":{"default":"in_memory","available":["in_memory"]}, ...}
```

## Store and Retrieve a Memory

```bash
# Store
curl -X POST http://localhost:8000/api/v1/memory/my-namespace \
  -H "Content-Type: application/json" \
  -d '{"key": "greeting", "content": "Hello, world!"}'

# Retrieve
curl http://localhost:8000/api/v1/memory/my-namespace/greeting
```

## Chat with an Agent

Use the kitchen-sink config which includes pre-configured agents:

```bash
AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE=configs/kitchen-sink.yaml \
  uvicorn agentic_primitives_gateway.main:app --reload --port 8000
```

```bash
# List agents
curl http://localhost:8000/api/v1/agents | python3 -m json.tool

# Chat
curl -X POST http://localhost:8000/api/v1/agents/research-assistant/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello! Remember that my name is Alice."}'
```

## Open the Web UI

If you've built the UI:

```bash
cd ui && npm install && npm run build
```

Then visit `http://localhost:8000/ui/` for the dashboard, agent chat, and API explorer.

For development with hot reload:

```bash
cd ui && npm run dev
# Opens at http://localhost:5173/ui/
```

## Run Tests

```bash
# Server tests (893+)
python -m pytest tests/ -v

# Client tests (100)
cd client && python -m pytest tests/ -v
```

## Next Steps

- [Configuration Guide](configuration.md) -- YAML config, environment variables, provider routing
- [Architecture](../concepts/architecture.md) -- understand how it all fits together
- [Agents](../concepts/agents.md) -- declarative agents with tool calling
