# Policy Enforcement

The gateway evaluates every API request against Cedar policies before it reaches the route handler.

## How It Works

1. **Action auto-discovery** -- Routes are introspected at startup to build a mapping of `(HTTP method, path pattern) → Cedar action`. No static list to maintain.
2. **Principal resolution** -- Extracted from request headers (`X-Agent-Id`, `X-Cred-*`, `X-AWS-Access-Key-Id`).
3. **Cedar evaluation** -- The `PolicyEnforcer` evaluates `permit(principal, action, resource)?` for each request.
4. **Default-deny** -- When Cedar is active with no loaded policies, all non-exempt requests are denied.

## Cedar Actions

Actions are derived automatically from route definitions:

```
/api/v1/memory/{namespace}       POST    →  Action::"memory:store_memory"
/api/v1/memory/{namespace}/{key} GET     →  Action::"memory:retrieve_memory"
/api/v1/agents/{name}/chat       POST    →  Action::"agents:chat_with_agent"
```

## Example Policies

```cedar
// Allow everyone to list and view agents
permit(principal, action == Action::"agents:list_agents", resource);
permit(principal, action == Action::"agents:get_agent", resource);

// Allow a specific agent to use memory
permit(
  principal == Agent::"research-assistant",
  action == Action::"memory:store_memory",
  resource
);

// Deny code execution for all agents
forbid(principal, action == Action::"code_interpreter:execute_code", resource);
```

## Configuration

```yaml
enforcement:
  backend: "agentic_primitives_gateway.enforcement.cedar.CedarPolicyEnforcer"
  config:
    policy_refresh_interval: 30  # Seconds between policy reloads
  seed_policies:
    - description: "Allow all"
      policy_body: 'permit(principal, action, resource);'
```

### Seed Policies

Policies in `seed_policies` are loaded into a "seed" policy engine at startup. This ensures enforcement works even with the noop (in-memory) policy provider.

## Exempt Paths

These paths are never enforced:

- `/healthz`, `/readyz`
- `/metrics`
- `/docs`, `/redoc`, `/openapi.json`
- `/ui/*`
- `/api/v1/providers`
- `/api/v1/policy/*` (policy management itself)

## Principals

The middleware resolves the principal from the `AuthenticatedPrincipal` contextvar first (set by the auth middleware when JWT auth is configured). If no authenticated principal is present, it falls back to header-based derivation.

**Resolution order:**

1. **Authenticated principal** (always set for non-exempt paths): `AuthenticatedPrincipal` contextvar → `User::"alice"` (from JWT `sub` claim or API key mapping)
2. Header fallback (exempt paths only): `X-Agent-Id: my-agent` → `Agent::"my-agent"`
3. Header fallback: `X-Cred-{service}-*` → `Service::"{service}"`
4. Header fallback: `X-AWS-Access-Key-Id: AKIA...` → `AWSPrincipal::"AKIA..."`
5. Last resort (exempt paths only): `Agent::"anonymous"`

Non-exempt paths always have an authenticated principal (the auth middleware returns 401 if credentials are missing). The header-based fallback and `Agent::"anonymous"` are only reachable on exempt paths, which are skipped by enforcement anyway.
