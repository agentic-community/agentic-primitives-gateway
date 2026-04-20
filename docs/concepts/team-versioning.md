# Team versioning

Teams use the same versioning model as agents — identity `(owner_id, name)`,
immutable `TeamVersion` records, deployed pointer, fork with
`forked_from` lineage, and the same admin-approval gate.  Read
[Agent versioning](agent-versioning.md) first; this page only covers
the team-specific differences.

## Worker / planner / synthesizer resolution

A team spec references agents by name in three fields:
`workers: list[str]`, `planner: str`, `synthesizer: str`.  When the team
runs, every reference resolves in the **team owner's** namespace first,
falling back to `system`:

```
team_spec.owner_id == alice
  → resolve bare "researcher"  as (alice, researcher) → (system, researcher)
```

This differs deliberately from sub-agent delegation.  Sub-agent refs
resolve in the *running agent's* owner namespace; team worker refs
resolve in the *team's* owner namespace.  The rationale is the same
either way: sharing something means sharing its whole dependency
graph, regardless of who triggered the run.

## Fork auto-qualifies worker refs too

When Bob forks Alice's team `research-crew` with
`workers=["analyst", "writer"]` and both `(alice, analyst)` and
`(alice, writer)` exist, the fork's spec rewrites both worker refs to
`"alice:analyst"` and `"alice:writer"`.  Same story for `planner` and
`synthesizer`.

This lets Bob run the forked team end-to-end without also forking every
worker agent.  He can selectively fork workers later and update the
refs.

## No cross-entity version references

A `TeamVersion` embeds `workers: list[str]` of bare or qualified names
— not `version_id` pointers.  The team always binds to whichever
version of each worker is currently deployed.  If Alice updates her
`analyst` agent to a new version, Bob's forked team that points at
`"alice:analyst"` picks up Alice's new deployment automatically.

This is a deliberate choice: teams are supposed to evolve with their
worker agents.  Pinning a team to exact worker version_ids is future
work; see the [Todos](https://github.com/omrishiv/agentic-primitives-gateway/blob/main/Todos.md)
list.

## See also

- [Agent versioning](agent-versioning.md) — underlying model.
- [Admin-approval workflow](../guides/admin-approval.md) — gate
  applies to both agents and teams.
