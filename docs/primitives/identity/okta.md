# Okta Identity

Identity provider using [Okta](https://www.okta.com/) for OAuth2 token exchange and API key management.

## Configuration

### Server-Side (Gateway Config)

```yaml
providers:
  identity:
    backend: "agentic_primitives_gateway.primitives.identity.okta.OktaIdentityProvider"
    config:
      domain: "dev-123456.okta.com"
      client_id: "${OKTA_CLIENT_ID}"
      client_secret: "${OKTA_CLIENT_SECRET}"
      api_token: "${OKTA_API_TOKEN}"
      auth_server: "default"
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `domain` | `""` | Okta domain (e.g. `dev-123456.okta.com`) |
| `client_id` | `""` | OAuth client ID |
| `client_secret` | (none) | OAuth client secret |
| `api_token` | (none) | Okta API token (SSWS) for admin operations |
| `auth_server` | `default` | Authorization server ID |

### Per-Request Credentials

```bash
curl -H "X-Cred-Okta-Domain: dev-123456.okta.com" \
     -H "X-Cred-Okta-Client-Id: my-client" \
     -H "X-Cred-Okta-Client-Secret: my-secret" \
     -H "X-Cred-Okta-Api-Token: SSWS-token" \
     http://localhost:8000/api/v1/identity/token
```

Or via the Python client:

```python
client.set_service_credentials("okta", {
    "domain": "dev-123456.okta.com",
    "client_id": "my-client",
    "client_secret": "my-secret",
})
```

## Prerequisites

- `pip install agentic-primitives-gateway[okta]`
- Okta developer account with an application configured
