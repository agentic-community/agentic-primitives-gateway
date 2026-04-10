# API Reference

The gateway exposes a REST API at `http://localhost:8000`. Full OpenAPI spec available at `/openapi.json` and interactive docs at `/docs`.

## Base URL

All primitive endpoints are under `/api/v1/{primitive}/`. Agent endpoints are under `/api/v1/agents/`. Team endpoints are under `/api/v1/teams/`.

## Authentication

The gateway supports pluggable authentication backends (noop, API key, JWT/OIDC). Auth headers:

| Header | Description |
|--------|-------------|
| `Authorization: Bearer <token>` | JWT or API key token for user authentication |
| `X-Api-Key` | Alternative API key header |

When auth is enabled, requests without valid credentials receive a **401 Unauthorized** response. Requests for resources the user does not own or have group access to receive a **403 Forbidden** response.

### Credential Pass-Through

Separate from user authentication, backend credentials are passed via request headers:

| Header | Description |
|--------|-------------|
| `X-AWS-Access-Key-Id` | AWS access key for Bedrock/AgentCore backends |
| `X-AWS-Secret-Access-Key` | AWS secret key |
| `X-AWS-Session-Token` | AWS session token (for temporary credentials) |
| `X-AWS-Region` | Override the provider's default region |
| `X-Cred-{Service}-{Key}` | Generic service credentials (e.g., `X-Cred-Langfuse-Public-Key`) |
| `X-Agent-Id` | Agent identity for Cedar policy evaluation |

## Provider Routing

| Header | Description |
|--------|-------------|
| `X-Provider` | Default provider for all primitives |
| `X-Provider-Memory` | Override memory provider |
| `X-Provider-LLM` | Override LLM provider |
| `X-Provider-{Primitive}` | Override any specific primitive |

## Common Response Codes

| Code | Meaning |
|------|---------|
| 200 | Success |
| 201 | Created |
| 204 | Deleted (no body) |
| 400 | Bad request (ValueError) |
| 401 | Unauthorized (missing or invalid credentials) |
| 403 | Forbidden (Cedar policy denial or resource not owned by user) |
| 404 | Not found |
| 409 | Conflict (e.g., duplicate agent name) |
| 422 | Validation error |
| 501 | Not implemented by this provider |
| 503 | Service unavailable (connection error) |
| 504 | Gateway timeout |

## Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/healthz` | Liveness probe (always returns `{"status": "ok"}`) |
| `GET` | `/readyz` | Readiness probe (checks all providers with tri-state healthcheck) |
| `GET` | `/metrics` | Prometheus metrics |
| `GET` | `/api/v1/providers` | List available providers per primitive |
| `GET` | `/api/v1/providers/status` | Authenticated provider healthcheck (uses caller's resolved credentials) |
| `GET` | `/auth/config` | Auth/OIDC configuration for UI (exempt from auth) |

### Tri-State Healthcheck

The readiness probe (`/readyz`) returns a per-provider status with three possible states:

| Status | Meaning |
|--------|---------|
| `ok` | Provider is fully healthy and operational |
| `reachable` | Provider server is up but needs user credentials (e.g., no server-side AWS/Langfuse credentials configured) |
| `down` | Provider is unreachable or errored |

Only `down` providers cause the overall readiness check to return HTTP 503. Providers in `reachable` state are not considered failures -- they work fine once a user provides credentials.

### Authenticated Provider Status

`GET /api/v1/providers/status` runs behind the full middleware stack (auth + credential resolution). Each provider's `healthcheck()` sees the authenticated user's resolved credentials. Providers that returned `reachable` on `/readyz` may return `ok` here if the user has valid credentials stored in their OIDC profile.

```json
{
  "checks": {
    "memory/mem0": "ok",
    "llm/bedrock": "ok",
    "observability/langfuse": "reachable"
  }
}
```

## Credentials

User credential management endpoints for reading, writing, and deleting per-user credentials stored in the identity provider (e.g., Keycloak).

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/credentials` | Read current user's credentials (values are masked) |
| `PUT` | `/api/v1/credentials` | Write/update credentials to the identity provider |
| `DELETE` | `/api/v1/credentials/{key}` | Delete a single credential by attribute name |
| `GET` | `/api/v1/credentials/status` | Credential resolution status for the current user |

### Credential Status

Returns information about how credentials are resolved for the current user, what the server credential fallback mode is, and which credential types are required by the active providers.

```bash
curl http://localhost:8000/api/v1/credentials/status \
  -H "Authorization: Bearer <token>"
```

```json
{
  "source": "oidc",
  "aws_configured": true,
  "server_credentials": "fallback",
  "required_credentials": ["aws", "langfuse"]
}
```

| Field | Description |
|-------|-------------|
| `source` | How credentials are resolved: `oidc`, `headers`, `server`, or `none` |
| `aws_configured` | Whether AWS credential resolution is enabled via OIDC |
| `server_credentials` | Server credential mode: `never`, `fallback`, or `always` |
| `required_credentials` | Credential types needed by active providers (e.g., `aws`, `langfuse`, `mem0`) |

### Read Credentials

```bash
curl http://localhost:8000/api/v1/credentials \
  -H "Authorization: Bearer <token>"
```

Returns masked values (only last 4 characters visible):

```json
{
  "attributes": {
    "apg.langfuse.public_key": "****abcd",
    "apg.langfuse.secret_key": "****ef12"
  }
}
```

### Write Credentials

```bash
curl -X PUT http://localhost:8000/api/v1/credentials \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"attributes": {"apg.langfuse.public_key": "pk-lf-abc123"}}'
```

### Delete a Credential

```bash
curl -X DELETE http://localhost:8000/api/v1/credentials/apg.langfuse.public_key \
  -H "Authorization: Bearer <token>"
```

## Primitives

Each primitive has its own route prefix. See the interactive docs at `/docs` for full schemas.

| Primitive | Prefix | Key Endpoints |
|-----------|--------|---------------|
| Memory | `/api/v1/memory` | Store, retrieve, search, events, sessions, branches, resources |
| Identity | `/api/v1/identity` | Tokens, API keys, workload identity, credential providers |
| Code Interpreter | `/api/v1/code-interpreter` | Sessions, execute, file upload/download |
| Browser | `/api/v1/browser` | Sessions, navigate, click, type, screenshot |
| Observability | `/api/v1/observability` | Traces, logs, generations, scores, sessions |
| LLM | `/api/v1/llm` | Completions, list models |
| Tools | `/api/v1/tools` | Register, list, search, invoke, servers |
| Policy | `/api/v1/policy` | Engines, policies, generations |
| Evaluations | `/api/v1/evaluations` | Evaluators, evaluate, online configs |

## Agents & Teams

- [Agent API Reference](agents.md)
- [Team API Reference](teams.md)
