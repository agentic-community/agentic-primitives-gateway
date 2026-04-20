# Lineage visualization

The `/ui/agents/{name}/lineage` and `/ui/teams/{name}/lineage` pages
render a DAG of every version reachable from a given identity via
parent links or forks.  It's the place to go when you need to answer
"who forked this, and from which version?" or "what changed between
v3 and v4?"

## Reading the graph

- **Nodes** are `AgentVersion` / `TeamVersion` records.  The label shows
  the version number and the qualified identity (`v3 @ alice:researcher`).
  Node border color reflects status — green for deployed, gray for
  draft / archived, blue for proposed, red for rejected.
- **Solid arrows** are intra-identity parent links (`parent_version_id`).
  `v1 → v2 → v3` is the edit history of a single identity.
- **Dashed + animated arrows** are cross-identity forks (`forked_from`).
  They point from the source version to the forked v1 that spawned a
  new identity.

Clicking a node opens a side drawer with the full version JSON + a
"Deploy this version" button when the viewer owns (or is admin of) the
target identity.

## Where nodes come from

The server walks the DAG in `VersionedSpecStore.get_lineage()`
starting from the root identity and following parent links + fork
links transitively.  You get every version of the root identity plus
every version of every identity forked from it.  The traversal
handles cross-namespace forks — Alice's `researcher` v3 forked by Bob
into `(bob, researcher)` v1 shows up in Alice's lineage too.

## Finding something useful

A few typical questions the viz answers at a glance:

- **Is my fork still tracking the source?** If the source identity has
  v4 deployed but your fork's `forked_from.version_id` points at v2,
  you're two versions behind.
- **Who reverted the approved change?** Click the deployed node; the
  drawer shows `created_by` + `approved_by`.
- **What's still pending review?** Blue-bordered (proposed) nodes are
  the admin queue.

## Also available

- The [PendingProposals page](../guides/admin-approval.md) shows *just*
  the proposed nodes across all identities — better for admin triage.
- The version history pages (`/ui/agents/{name}/versions`) show the
  same data as a linear table, useful when you don't need the fork
  topology.
