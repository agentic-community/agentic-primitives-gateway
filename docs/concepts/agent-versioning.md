# Agent versioning

Every agent is now backed by an immutable history of `AgentVersion`
records.  Editing an agent doesn't overwrite a row — it adds a new
version and flips a deployment pointer.  This page explains the data
model, the resolution rules, and why things are laid out this way.

## Identity = `(owner_id, name)`

An agent's primary key is the **identity** pair `(owner_id, name)`,
not the name alone.  Two different owners can have an agent called
`researcher`; they're distinct identities with independent version
histories.

Pre-seeded YAML specs live in the `system` namespace (`owner_id="system"`).
Anything a user creates or forks lives in that user's namespace
(`owner_id=principal.id`).

## Addressing: bare, qualified, admin-override

Routes accept three forms of `{name}`:

| Form | Example | Resolution |
|---|---|---|
| **Bare** | `/api/v1/agents/researcher` | `(principal.id, researcher)` → fall back to `("system", researcher)`.  Never falls through to another owner's shared agent. |
| **Qualified** | `/api/v1/agents/alice:researcher` | Direct `(alice, researcher)` lookup.  Required to read someone else's shared agent. |
| **Admin `?owner=`** | `/api/v1/agents/researcher?owner=alice` | Admin-only alternative to qualified form. |

The "no-fall-through-to-shared" rule exists so bare lookups are
**deterministic** — if two owners share agents named `researcher` with
you, which one would win?  Forcing qualified addressing for shared
access keeps resolution predictable.

## Version lifecycle

Every `AgentVersion` has one of five statuses:

```
draft  →  proposed  →  deployed
             │            │
             ├──► rejected (terminal)
             │
             └─► previous deployed flips to archived
```

- **draft** — the new-version default when the admin-approval gate is on.
  Inert — runners never serve drafts.
- **proposed** — owner has submitted the draft for admin review.
- **deployed** — the single active version per identity.  `GET /agents/{name}`
  always returns the spec embedded in the currently-deployed version.
- **archived** — previously deployed, superseded by a newer deploy.
- **rejected** — admin declined to approve the proposal.

## Run-time resolution is always the deployed version

The LLM loop in `AgentRunner` reads `spec.model`, `spec.system_prompt`,
etc. directly from the deployed `AgentSpec` on every request.  There is
no in-memory cache of old versions.  Drafts are inert until deployed.

## Sub-agent delegation resolves in the parent agent's namespace

When Alice's `researcher` delegates to `analyst`, the sub-agent
resolves as `(alice, analyst)` first and falls back to
`("system", analyst)`.  It does *not* use the caller's namespace —
sharing an agent means sharing its whole delegation graph.  This is
what makes "Bob runs Alice's shared `researcher`" work without Bob
having to also own every sub-agent.

## Fork = clone identity with lineage

`POST /api/v1/agents/{name}/fork` copies the *deployed* version of
the source identity into the caller's namespace and records a
`forked_from` pointer on the new v1:

```python
version.forked_from = ForkRef(
    name="researcher", owner_id="alice", version_id="<uuid>",
)
```

### Fork auto-qualification of sub-refs

If Alice's `researcher` delegates to bare `analyst`, and Alice also
owns `(alice, analyst)`, then Bob's fork auto-rewrites the ref to
`"alice:analyst"` so the fork keeps resolving to Alice's analyst.
Without this rewrite, the fork would fall back to system's `analyst`
(if any) or fail.

This means a fresh fork is immediately runnable against the source
owner's graph.  Bob can later fork `analyst` separately and update the
ref to `"bob:analyst"` or bare `analyst` as he prefers.

## Admin-approval gate

`governance.require_admin_approval_for_deploy` (default `false`) flips
the create-and-deploy flow into a PR-style workflow:

- Off: `POST /versions` auto-deploys.  `PUT /agents/{name}` is the same
  thing spelled differently.
- On: `POST /versions` creates `draft`; must transition through
  `propose` → admin `approve` → `deploy`.  `PUT` returns 409 with a
  pointer to `/versions`.

Pre-seeded YAML specs always bypass the gate on first load — bootstrap
must never deadlock.

## Memory + conversation history are identity-scoped

Memory keys become `agent:{owner_id}:{name}:u:{user_id}` and actor IDs
become `{owner_id}:{name}:u:{user_id}`.  Alice's forked `researcher`
has fully isolated memory from Alice's original — and from Bob's fork
— even though they all share a bare name.  This is critical for fork
correctness: without owner-scoping, every fork would inherit (and mutate)
upstream memory.

## Configuration

```yaml
governance:
  # Gate that forces POST /versions → /propose → admin /approve → /deploy.
  # Pre-seeded YAML specs always bypass.
  require_admin_approval_for_deploy: false

agents:
  # Past this count, the oldest non-deployed / non-draft versions per
  # identity are automatically archived.
  max_versions_per_identity: 50
```

## Prometheus metrics

New in the governance dashboard:

- `gateway_agent_versions_created_total{ns_kind, auto_deployed}` —
  new version persistence.  `ns_kind` is `system` or `user`;
  `auto_deployed` is a boolean.
- `gateway_agent_forks_total{source_ns_kind}`
- `gateway_agent_version_approvals_total{outcome}` —
  `approved`, `rejected`, or `deployed`.

Label cardinality is intentionally bounded: we never emit raw
`owner_id` because it would explode per-user.

## See also

- [Team versioning](team-versioning.md) — identical model for teams.
- [Admin-approval workflow](../guides/admin-approval.md) — how to run
  with the gate on in production.
- [Lineage visualization](../guides/lineage.md) — the `/ui/agents/{name}/lineage`
  DAG view.
