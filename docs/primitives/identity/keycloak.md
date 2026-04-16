# Keycloak Identity

Self-hosted identity provider using [Keycloak](https://www.keycloak.org/) for token exchange, API key management, and workload identity.

## Keycloak and the Gateway: Three Config Sections

The gateway has three distinct config sections that can all be backed by the same Keycloak instance, but they serve different purposes and typically use **different clients**:

```
┌─────────────────────────────────────────────────────────────────┐
│                        Keycloak Realm                           │
│                                                                 │
│  ┌──────────────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ agentic-gateway-ui   │  │ agentic-     │  │ agentic-     │  │
│  │ (public client)      │  │ gateway      │  │ gateway-admin│  │
│  │                      │  │ (confidential│  │ (confidential│  │
│  │ Used by:             │  │  client)     │  │  client)     │  │
│  │ • auth.jwt           │  │              │  │              │  │
│  │ • Web UI OIDC login  │  │ Used by:     │  │ Used by:     │  │
│  │                      │  │ • identity   │  │ • credentials│  │
│  │ No secret — browser  │  │   provider   │  │   resolver   │  │
│  │ safe (PKCE flow)     │  │              │  │   + writer   │  │
│  │                      │  │ Token        │  │              │  │
│  │                      │  │ exchange,    │  │ Reads user   │  │
│  │                      │  │ workload IDs │  │ attributes   │  │
│  └──────────────────────┘  └──────────────┘  └──────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 1. `auth.jwt` — Who is the user?

**Purpose:** Validates JWT tokens on incoming API requests. Identifies the user (principal) for ownership, scoping, and policy enforcement.

**Client type:** **Public** (no secret) — used by the web UI's browser-based OIDC flow (Authorization Code + PKCE).

```yaml
auth:
  backend: jwt
  jwt:
    issuer: "https://keycloak.example.com/realms/agents"
    client_id: "agentic-gateway-ui"    # public client
    algorithms: ["RS256"]
    claims_mapping:
      groups: "groups"
      scopes: "scope"
```

This section has **nothing to do with the identity primitive**. It's pure authentication — "is this JWT valid and who sent it?" The gateway fetches JWKS keys from the issuer's `.well-known/openid-configuration` endpoint.

### 2. `providers.identity` — Agent-to-service authentication

**Purpose:** Enables agents to obtain tokens for external services (token exchange), retrieve API keys, and manage workload identities. This is the **identity primitive**.

**Client type:** **Confidential** (has a secret) — used server-side by the gateway. Needs "Service accounts roles" enabled for workload tokens.

```yaml
providers:
  identity:
    backend: "...keycloak.KeycloakIdentityProvider"
    config:
      server_url: "https://keycloak.example.com"
      realm: "agents"
      client_id: "agentic-gateway"          # confidential client
      client_secret: "${KEYCLOAK_CLIENT_SECRET}"
```

This is what agents call when they need to authenticate with external APIs. An agent calls `POST /api/v1/identity/token` and the gateway exchanges tokens via Keycloak on the agent's behalf.

### 3. `credentials` — Per-user credential resolution

**Purpose:** Resolves per-user backend credentials (Langfuse keys, MCP tokens, etc.) from Keycloak user attributes. Populates the same context as `X-Cred-*` headers, but automatically from the OIDC provider.

**Client type:** **Confidential** with **Admin API access** — needs `manage-users` and `manage-realm` roles to read user attributes. Can be the same client as the identity provider, or a dedicated admin client.

```yaml
credentials:
  resolver: oidc
  writer:
    backend: keycloak
    config:
      admin_client_id: "${KC_ADMIN_CLIENT_ID}"      # confidential client with admin roles
      admin_client_secret: "${KC_ADMIN_CLIENT_SECRET}"
```

When a user sends a JWT, the gateway reads their `apg.*` attributes from Keycloak (e.g., `apg.langfuse.public_key`) and injects them as service credentials. Users manage their credentials via the Settings page in the web UI.

### Which client for what?

| Config section | Purpose | Client type | Client ID (example) | Requires secret? | Keycloak roles needed |
|---|---|---|---|---|---|
| `auth.jwt` | Validate user JWTs | **Public** | `agentic-gateway-ui` | No | None |
| `providers.identity` | Token exchange, workload IDs | **Confidential** | `agentic-gateway` | Yes | `manage-clients` (for control plane) |
| `credentials.writer` | Read/write user attributes | **Confidential** | `agentic-gateway` or `agentic-gateway-admin` | Yes | `manage-users`, `manage-realm` |

**You can use the same confidential client** for both `providers.identity` and `credentials.writer` if you assign all the needed roles. Using separate clients provides better audit separation.

**The public client** (`agentic-gateway-ui`) should **only** be used for `auth.jwt`. It has no secret and cannot perform admin operations.

## Prerequisites

```bash
pip install agentic-primitives-gateway[keycloak]
```

A running Keycloak instance with a confidential client configured. The [Agents on EKS](https://awslabs.github.io/ai-on-eks/docs/infra/agents-on-eks) infrastructure includes a pre-configured Keycloak.

## Setting Up Keycloak

### Option A: Use an existing realm

If you already have a Keycloak realm (e.g., from Agents on EKS), you can add the gateway client to it.

### Option B: Create a dedicated realm (recommended for isolation)

A dedicated realm keeps agent infrastructure separate from your application's user realm. This is recommended for production deployments.

1. Log into the Keycloak admin console
2. Click the realm dropdown (top left) → **Create realm**
3. Name it (e.g., `agents`) → **Create**

### Create the gateway client

In your chosen realm:

1. Go to **Clients** → **Create client**
2. **General settings:**
   - Client ID: `agentic-gateway`
   - Client type: **OpenID Connect**
3. **Capability config:**
   - Client authentication: **ON** (makes it confidential)
   - Authorization: OFF
   - Check **Service accounts roles** (enables client_credentials grant for workload tokens)
   - Check **Standard flow** if the web UI will use this realm for OIDC login
4. **Login settings:**
   - Valid redirect URIs: `http://localhost:8000/*` (or your gateway URL)
   - Web origins: `http://localhost:8000` (for CORS)
5. Click **Save**
6. Go to the **Credentials** tab → copy the **Client secret**

### Assign admin roles (required for control plane operations)

The gateway's service account needs Keycloak Admin API access to manage workload identities (clients) and credential providers.

1. Go to **Clients** → **agentic-gateway** → **Service account roles**
2. Click **Assign role**
3. Change the filter to **Filter by clients** → select **realm-management**
4. Select these roles:
   - **manage-clients** — create, update, delete clients (workload identities)
   - **manage-realm** — required for credential resolution (reading user attributes)
5. Click **Assign**

**Security considerations:**

- `manage-clients` allows the gateway to create and delete OAuth clients in the realm. In a shared realm, this means the gateway could modify clients it didn't create. Use a **dedicated realm** to limit blast radius.
- `manage-realm` is needed for the OIDC credential resolution subsystem (reading `apg.*` user attributes via the Admin API). If you don't use credential resolution, you can skip this role.
- In production, consider using a **separate service account client** for admin operations (not the same client used for token exchange). This lets you audit admin actions separately.
- Never expose the client secret in client-side code. The gateway holds the secret server-side; the web UI uses a separate **public** client for OIDC login.

### Optional: Create a public client for the web UI (`auth.jwt`)

If the gateway's web UI needs OIDC login through this realm (this is the **public client** described in the "Which client for what?" table above):

1. Go to **Clients** → **Create client**
2. Client ID: `agentic-gateway-ui`
3. Client authentication: **OFF** (public client — required for browser SPAs)
4. Standard flow: **ON**, Direct access grants: OFF
5. Valid redirect URIs: `http://localhost:8000/ui/*`, `http://localhost:5173/ui/*` (dev)
6. Web origins: `http://localhost:8000`, `http://localhost:5173`

Then configure the gateway:

```yaml
auth:
  backend: jwt
  jwt:
    issuer: "https://keycloak.example.com/realms/agents"
    client_id: "agentic-gateway-ui"
    algorithms: ["RS256"]
```

### Optional: Add a groups mapper

If you want JWT-based group membership for Cedar policy enforcement:

1. Go to **Clients** → **agentic-gateway-ui** → **Client scopes** → **agentic-gateway-ui-dedicated**
2. **Add mapper** → **By configuration** → **Group Membership**
3. Name: `groups`, Token Claim Name: `groups`, Full group path: **OFF**

### Optional: Enable credential resolution (`credentials` section)

If you want per-user credential resolution (users store their own Langfuse keys, MCP tokens, etc. in Keycloak):

1. The service account needs **`manage-users`** and **`manage-realm`** roles (in addition to `manage-clients`)
2. The gateway reads `apg.*` user attributes from Keycloak's Admin API
3. The credential writer auto-declares new `apg.*` attributes in Keycloak's User Profile config

```yaml
credentials:
  resolver: oidc
  writer:
    backend: keycloak
    config:
      # Can be the same client as the identity provider, or a dedicated admin client
      admin_client_id: "${KC_ADMIN_CLIENT_ID:=agentic-gateway}"
      admin_client_secret: "${KC_ADMIN_CLIENT_SECRET}"
  cache:
    ttl_seconds: 300
    max_entries: 10000
```

Users then manage their credentials via the gateway's web UI Settings page (`/ui/settings`). The writer stores them as `apg.langfuse.public_key`, `apg.mcp_registry.token`, etc. in Keycloak user attributes.

## Gateway Configuration

```yaml
providers:
  identity:
    backend: "agentic_primitives_gateway.primitives.identity.keycloak.KeycloakIdentityProvider"
    config:
      server_url: "${KEYCLOAK_SERVER_URL:=http://localhost:8080}"
      realm: "${KEYCLOAK_REALM:=agents}"
      client_id: "${KEYCLOAK_CLIENT_ID:=agentic-gateway}"
      client_secret: "${KEYCLOAK_CLIENT_SECRET}"
```

| Parameter | Default | Description |
|---|---|---|
| `server_url` | `http://localhost:8080` | Keycloak server URL |
| `realm` | `master` | Keycloak realm name |
| `client_id` | `agentic-gateway` | Confidential client ID |
| `client_secret` | (none) | Client secret from the Credentials tab |

### Per-request credentials

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

## Supported Operations

### Data plane (token operations)

| Operation | Description |
|---|---|
| **get_token** | Exchange a workload token for an external service token via Keycloak's token exchange. Requires "Standard Token Exchange" enabled on the client. |
| **get_api_key** | Retrieve a client secret from Keycloak by client label lookup via the Admin API. |
| **get_workload_token** | Obtain a workload identity token using client_credentials grant. Requires "Service accounts roles" enabled. |

### Control plane (admin operations)

These require `manage-clients` role on the service account.

| Operation | Description |
|---|---|
| **list_credential_providers** | Lists Identity Providers (IDPs) configured in the realm. |
| **create_credential_provider** | Creates a new IDP in the realm. |
| **get/update/delete_credential_provider** | Manage IDPs by alias. |
| **list_workload_identities** | Lists confidential clients with service accounts enabled. |
| **create_workload_identity** | Creates a new confidential client with client_credentials grant. |
| **get/update/delete_workload_identity** | Manage clients by client_id. |

## Usage Examples

### Python client

```python
from agentic_primitives_gateway_client import AgenticPlatformClient, Identity

client = AgenticPlatformClient("http://localhost:8000")
identity = Identity(client)

# Get a workload token (client_credentials grant)
token = await identity.get_workload_token("my-agent")

# Get an API key (client secret lookup)
key = await identity.get_api_key("my-service", workload_token="...")
```

### curl

```bash
# Get a workload token
curl -X POST http://localhost:8000/api/v1/identity/workload-token \
  -H "Content-Type: application/json" \
  -d '{"workload_name": "my-agent"}'

# List workload identities
curl http://localhost:8000/api/v1/identity/workload-identities

# Create a workload identity
curl -X POST http://localhost:8000/api/v1/identity/workload-identities \
  -H "Content-Type: application/json" \
  -d '{"name": "new-agent", "allowed_return_urls": ["https://example.com/callback"]}'
```

## Security Best Practices

1. **Use a dedicated realm** for agent infrastructure. This prevents the gateway's `manage-clients` role from affecting application clients.

2. **Rotate client secrets** periodically. The gateway config supports `${KEYCLOAK_CLIENT_SECRET}` env var references for secret injection from Kubernetes Secrets or vault systems.

3. **Separate UI and service clients.** The web UI should use a public client (`agentic-gateway-ui`). The gateway backend should use a confidential client (`agentic-gateway`). Never share secrets with browser-side code.

4. **Audit admin operations.** Keycloak logs all Admin API calls. Monitor for unexpected client creation/deletion from the gateway's service account.

5. **Minimize roles.** Only assign `manage-clients` if the gateway needs to create workload identities. If you only need token exchange, no admin roles are required.
