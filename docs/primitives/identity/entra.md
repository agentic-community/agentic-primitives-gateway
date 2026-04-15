# Microsoft Entra ID

Identity provider using [Microsoft Entra ID](https://www.microsoft.com/en-us/security/business/identity-access/microsoft-entra-id) (formerly Azure AD) for OAuth2 token exchange.

## Configuration

### Server-Side (Gateway Config)

```yaml
providers:
  identity:
    backend: "agentic_primitives_gateway.primitives.identity.entra.EntraIdentityProvider"
    config:
      tenant_id: "${AZURE_TENANT_ID}"
      client_id: "${AZURE_CLIENT_ID}"
      client_secret: "${AZURE_CLIENT_SECRET}"
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `tenant_id` | `""` | Azure AD tenant ID |
| `client_id` | `""` | Application (client) ID |
| `client_secret` | (none) | Client secret |

### Per-Request Credentials

```bash
curl -H "X-Cred-Entra-Tenant-Id: my-tenant" \
     -H "X-Cred-Entra-Client-Id: my-client" \
     -H "X-Cred-Entra-Client-Secret: my-secret" \
     http://localhost:8000/api/v1/identity/token
```

Or via the Python client:

```python
client.set_service_credentials("entra", {
    "tenant_id": "my-tenant",
    "client_id": "my-client",
    "client_secret": "my-secret",
})
```

## Prerequisites

- `pip install agentic-primitives-gateway[entra]`
- Azure AD app registration with appropriate API permissions
