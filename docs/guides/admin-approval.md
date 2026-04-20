# Running with admin-approval for deploys

For production deployments where an agent/team change could affect
downstream users, turn on the admin-approval gate so version deploys
require explicit review.

## Flip the switch

```yaml
governance:
  require_admin_approval_for_deploy: true
```

With the gate **on**:

- `POST /api/v1/agents` — creates the identity + v1 as `draft`.
  Returns 202 with the draft version (no live traffic).
- `POST /api/v1/agents/{name}/versions` — same: new versions land as
  `draft`.
- `PUT /api/v1/agents/{name}` — returns **409** with a pointer at
  `/versions`.  The PUT path cannot bypass approval.
- `POST /fork` — the fork itself still creates a deployed v1 in the
  caller's namespace (forks are creations, not updates of shared state).
  Subsequent edits to the fork go through the draft → proposed flow.
- YAML-seeded specs **always** bypass the gate on first load.
  Bootstrap must never deadlock.

## The propose → approve → deploy flow

A draft version has to move through three explicit steps:

```
POST  /api/v1/agents/{name}/versions            # create draft
POST  /api/v1/agents/{name}/versions/{vid}/propose
# then as admin:
POST  /api/v1/agents/{name}/versions/{vid}/approve
POST  /api/v1/agents/{name}/versions/{vid}/deploy
```

Rejection short-circuits:

```
POST  /api/v1/agents/{name}/versions/{vid}/reject  {reason: "..."}
```

Rejected versions are terminal.  The owner can create a new draft
from the same parent and try again.

## Admin review queue

`GET /api/v1/admin/agents/proposals` returns every pending agent
proposal across every namespace.  Same for `/api/v1/admin/teams/proposals`.
The UI renders both in a tabbed `Pending proposals` page
(`/ui/admin/proposals`) with one-click **Approve + deploy** or
**Reject with reason**.

## Audit trail

Every transition emits a structured audit event (see the
[governance concept guide](../concepts/governance.md)):

- `agent.version.create` / `team.version.create`
- `agent.version.propose`
- `agent.version.approve` — includes the approver's principal id
- `agent.version.reject` — includes truncated reason
- `agent.version.deploy` — includes `previous_version_id` so you can
  trace rollouts
- `agent.fork`

`resource_id` on every version/fork event is the qualified identity
(`{owner_id}:{name}`), so dashboards can filter by fork.

## Prometheus

- `gateway_agent_version_approvals_total{outcome}` —
  `approved | rejected | deployed`.  Compute approval rate,
  rejection reasons, and time-to-deploy from this counter plus the
  audit trail timestamps.
- `gateway_agent_versions_created_total{ns_kind, auto_deployed}` —
  break down what is and isn't going through the gate.  With the gate
  on, `auto_deployed=true` should drop to roughly the pre-seeded rate.

## When to turn it on

Recommended configurations:

- **Dev / quickstart**: gate off.  Keeps the edit-deploy cycle fast.
- **Pre-production**: gate on with the same user acting as owner and
  admin, so the workflow is exercised without real review friction.
- **Production**: gate on, admins are distinct from owners.  Pair with
  the audit trail for compliance evidence.

## Limitations

- The gate is process-wide — all agents + teams see the same setting.
  Per-identity overrides aren't wired.
- Owners can't approve their own proposals.  That's intentional; if
  you need it, grant the owner the admin scope.
- Rejected versions stay in the history forever.  You can prune them
  by bumping `agents.max_versions_per_identity` / the retention policy
  if the archive gets noisy.
