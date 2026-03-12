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
| `X-Provider-Gateway` | Override gateway provider |
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
| `GET` | `/healthz` | Liveness probe |
| `GET` | `/readyz` | Readiness probe (checks all providers) |
| `GET` | `/metrics` | Prometheus metrics |
| `GET` | `/api/v1/providers` | List available providers per primitive |
| `GET` | `/auth/config` | Auth/OIDC configuration for UI (exempt from auth) |

## Primitives

Each primitive has its own route prefix. See the interactive docs at `/docs` for full schemas.

| Primitive | Prefix | Key Endpoints |
|-----------|--------|---------------|
| Memory | `/api/v1/memory` | Store, retrieve, search, events, sessions, branches, resources |
| Identity | `/api/v1/identity` | Tokens, API keys, workload identity, credential providers |
| Code Interpreter | `/api/v1/code-interpreter` | Sessions, execute, file upload/download |
| Browser | `/api/v1/browser` | Sessions, navigate, click, type, screenshot |
| Observability | `/api/v1/observability` | Traces, logs, generations, scores, sessions |
| Gateway | `/api/v1/gateway` | Completions, list models |
| Tools | `/api/v1/tools` | Register, list, search, invoke, servers |
| Policy | `/api/v1/policy` | Engines, policies, generations |
| Evaluations | `/api/v1/evaluations` | Evaluators, evaluate, online configs |

## Agents & Teams

- [Agent API Reference](agents.md)
- [Team API Reference](teams.md)
