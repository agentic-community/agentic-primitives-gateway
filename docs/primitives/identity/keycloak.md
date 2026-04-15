# Keycloak Identity

Self-hosted identity provider using [Keycloak](https://www.keycloak.org/) for token exchange, API key management, and workload identity.

## Configuration

### Server-Side (Gateway Config)

```yaml
providers:
  identity:
    backend: "agentic_primitives_gateway.primitives.identity.keycloak.KeycloakIdentityProvider"
    config:
      server_url: "http://localhost:8080"
      realm: "agents"
      client_id: "agentic-gateway"
      client_secret: "${KEYCLOAK_CLIENT_SECRET}"
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `server_url` | `http://localhost:8080` | Keycloak server URL |
| `realm` | `master` | Keycloak realm name |
| `client_id` | `agentic-gateway` | Client ID for token exchange |
| `client_secret` | (none) | Client secret |

### Per-Request Credentials

Users can override Keycloak connection details per-request:

```bash
curl -H "X-Cred-Keycloak-Server-Url: https://keycloak.example.com" \
     -H "X-Cred-Keycloak-Realm: my-realm" \
     -H "X-Cred-Keycloak-Client-Id: my-client" \
     -H "X-Cred-Keycloak-Client-Secret: my-secret" \
     http://localhost:8000/api/v1/identity/token
```

Or via the Python client:

```python
client.set_service_credentials("keycloak", {
    "server_url": "https://keycloak.example.com",
    "realm": "my-realm",
    "client_id": "my-client",
    "client_secret": "my-secret",
})
```

## Using the Identity API

### Get a Token

```bash
curl -X POST http://localhost:8000/api/v1/identity/token \
  -H "Content-Type: application/json" \
  -d '{"audience": "https://api.example.com", "scopes": ["read", "write"]}'
```

### Get an API Key

```bash
curl http://localhost:8000/api/v1/identity/api-key
```

## Using with the Python Client

```python
from agentic_primitives_gateway_client import AgenticPlatformClient, Identity

client = AgenticPlatformClient("http://localhost:8000")
identity = Identity(client)

token = await identity.get_token(audience="https://api.example.com")
api_key = await identity.get_api_key()
```

## Prerequisites

- `pip install agentic-primitives-gateway[keycloak]`
- Running Keycloak instance with a realm and client configured
