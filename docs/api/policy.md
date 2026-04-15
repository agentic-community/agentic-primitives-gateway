# Policy API

`/api/v1/policy`

Cedar-based policy engine and policy management. All endpoints require authentication. Mutation endpoints (create, update, delete) require admin.

This API manages policy **definitions** (the write path). For policy **enforcement** at request time, see the [Policy Enforcement](../concepts/policy.md) concept guide.

**Backends:** `NoopPolicyProvider`, [`AgentCorePolicyProvider`](../primitives/policy/agentcore.md)

## Enforcement Info

| Method | Path | Description |
|---|---|---|
| `GET` | `/enforcement` | Returns the active enforcement engine ID and whether enforcement is enabled. |

```bash
curl http://localhost:8000/api/v1/policy/enforcement
# {"active": true, "engine_id": "my-engine"}
```

## Policy Engines

| Method | Path | Description |
|---|---|---|
| `POST` | `/engines` | Create a policy engine. Returns 201. **Admin only.** |
| `GET` | `/engines` | List policy engines. Query params: `max_results`, `next_token`. |
| `GET` | `/engines/{engine_id}` | Get a policy engine. |
| `DELETE` | `/engines/{engine_id}` | Delete a policy engine. Returns 204. **Admin only.** |

```bash
curl -X POST http://localhost:8000/api/v1/policy/engines \
  -H "Content-Type: application/json" \
  -d '{"name": "production", "description": "Production policy engine"}'
```

## Policies (Cedar)

| Method | Path | Description |
|---|---|---|
| `POST` | `/engines/{engine_id}/policies` | Create a policy. Returns 201. **Admin only.** |
| `GET` | `/engines/{engine_id}/policies` | List policies. Query params: `max_results`, `next_token`. |
| `GET` | `/engines/{engine_id}/policies/{policy_id}` | Get a policy. |
| `PUT` | `/engines/{engine_id}/policies/{policy_id}` | Update a policy. **Admin only.** |
| `DELETE` | `/engines/{engine_id}/policies/{policy_id}` | Delete a policy. Returns 204. **Admin only.** |

```bash
# Create a policy
curl -X POST http://localhost:8000/api/v1/policy/engines/my-engine/policies \
  -H "Content-Type: application/json" \
  -d '{
    "policy_body": "permit(principal == Agent::\"research-assistant\", action, resource);",
    "description": "Allow research-assistant full access"
  }'
```

## Policy Generation (Optional)

Auto-generate policies from agent behavior. Returns 501 if not supported by the configured provider. Only `AgentCorePolicyProvider` supports generation.

| Method | Path | Description |
|---|---|---|
| `POST` | `/engines/{engine_id}/generations` | Start policy generation. Returns 201 or 501. |
| `GET` | `/engines/{engine_id}/generations` | List generations. Returns 501 if not supported. |
| `GET` | `/engines/{engine_id}/generations/{generation_id}` | Get generation status. |
| `GET` | `/engines/{engine_id}/generations/{generation_id}/assets` | List generated policy assets. |
