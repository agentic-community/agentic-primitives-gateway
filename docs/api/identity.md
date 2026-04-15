# Identity API

`/api/v1/identity`

Workload identity tokens, OAuth2 token exchange, API key retrieval, and credential/workload management. All endpoints require authentication. Control plane endpoints (create, update, delete) require admin.

**Backends:** `NoopIdentityProvider`, [`AgentCoreIdentityProvider`](../primitives/identity/agentcore.md), [`KeycloakIdentityProvider`](../primitives/identity/keycloak.md), [`EntraIdentityProvider`](../primitives/identity/entra.md), [`OktaIdentityProvider`](../primitives/identity/okta.md)

## Token Operations (Data Plane)

| Method | Path | Description |
|---|---|---|
| `POST` | `/token` | Exchange a workload token for an external service OAuth2 token. |
| `POST` | `/api-key` | Retrieve a stored API key for a credential provider. |
| `POST` | `/workload-token` | Obtain a workload identity token, optionally scoped to a user. |
| `POST` | `/auth/complete` | Confirm user authorization for a 3-legged OAuth flow. Returns 204. |

### Token exchange

```bash
curl -X POST http://localhost:8000/api/v1/identity/token \
  -H "Content-Type: application/json" \
  -d '{
    "credential_provider": "github",
    "workload_token": "eyJ...",
    "auth_flow": "M2M",
    "scopes": ["repo"]
  }'
```

**Request body:**

| Field | Type | Required | Description |
|---|---|---|---|
| `credential_provider` | string | yes | Name of the registered credential provider. |
| `workload_token` | string | yes | The agent's workload identity token. |
| `auth_flow` | string | yes | `M2M` (machine-to-machine) or `USER_FEDERATION` (3-legged). |
| `scopes` | list[string] | no | OAuth2 scopes to request. |
| `callback_url` | string | no | Redirect URL for 3-legged flows. |
| `force_auth` | bool | no | Force re-authentication. |

## Credential Provider Management (Control Plane)

Requires admin. Returns 501 if not supported by the configured provider.

| Method | Path | Description |
|---|---|---|
| `GET` | `/credential-providers` | List registered credential providers. |
| `POST` | `/credential-providers` | Register a new credential provider. Returns 201. |
| `GET` | `/credential-providers/{name}` | Get credential provider details. |
| `PUT` | `/credential-providers/{name}` | Update a credential provider. |
| `DELETE` | `/credential-providers/{name}` | Delete a credential provider. Returns 204. |

## Workload Identity Management (Control Plane)

Requires admin. Returns 501 if not supported by the configured provider.

| Method | Path | Description |
|---|---|---|
| `GET` | `/workload-identities` | List workload identities. |
| `POST` | `/workload-identities` | Register a new workload identity. Returns 201. |
| `GET` | `/workload-identities/{name}` | Get workload identity details. |
| `PUT` | `/workload-identities/{name}` | Update a workload identity. |
| `DELETE` | `/workload-identities/{name}` | Delete a workload identity. Returns 204. |
