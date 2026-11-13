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

Reading application secrets through `get_api_key` requires a gateway admin principal; `workload_token` does not authorize a Microsoft Graph admin lookup.

For `get_workload_token`, passing a caller `user_token` uses on-behalf-of exchange and remains available to authenticated callers. Without `user_token`, the operation mints the configured gateway client's token and requires admin.

### Per-Request Credentials

A caller-supplied set is used whole — send `tenant_id`, `client_id`, and `client_secret` together; the operator's values never fill gaps.

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
