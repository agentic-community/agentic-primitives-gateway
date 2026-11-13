# Memory Namespaces

How agent memory is organized and why the memory namespace is separate from session IDs.

## Two Types of Memory

Agents have two separate memory systems:

| Type | Scoped by | Persists across sessions? | Used for |
|------|-----------|--------------------------|----------|
| **Memory store** | Agent name + user ID | Yes | Facts, preferences, learned information |
| **Conversation history** | Agent name + user ID + session ID | No (per session) | Recent turns for context |

## Memory Namespace

The memory namespace is resolved from the agent spec:

```yaml
primitives:
  memory:
    enabled: true
    namespace: "agent:{agent_name}:{session_id}"  # Template
```

The `{session_id}` placeholder is **stripped** for the memory store; the terminal user ID is encoded as one namespace segment:

- Template: `agent:{agent_name}:{session_id}`
- Memory namespace: `agent:research-assistant:u:alice` (always user-scoped, no session)
- Noop auth: `agent:research-assistant:u:noop` (all dev users share)
- This is where `remember`, `recall`, `search_memory` tools read/write

This means when a user says "my name is Alice" and the agent stores it, it persists across all of **that user's** sessions, but is invisible to other users.

## Conversation History

Conversation history uses `(actor_id, session_id)` directly:

- `actor_id` is user-scoped when auth is active: `{agent_name}:u:{user_id}` (e.g., `research-assistant:u:alice`)
- `actor_id` without auth: `{agent_name}` (agent-scoped only)
- `auto_memory: true` → each turn is stored as an event
- On new conversations, recent turns are loaded from the same session
- Different sessions have independent histories

## Memory Context Injection

At the start of a new conversation (no history loaded), the runner:

1. Fetches memories from the memory namespace
2. Formats them as a system-like preamble message
3. Injects into the conversation so the LLM knows what it stored before

This means the agent "remembers" facts from previous sessions even with a new session ID.

## Historical Child Namespaces

The runner no longer searches child namespaces when a private memory namespace is empty. Private `:u:{encoded_user_id}` is terminal: `bob:admin` becomes `bob%3Aadmin`, not a child of Bob's `…:u:bob` namespace. Historical session-scoped memories require an explicit, ownership-checked migration into the caller's exact private namespace.


## User Isolation (Private Memory)

When JWT auth is active, two users chatting with the same agent have **completely isolated private memory**:

- User Alice's memory namespace: `agent:research-assistant:u:alice`
- User Bob's memory namespace: `agent:research-assistant:u:bob`
- Their conversation histories are also isolated (different `actor_id` values)

!!! note "`shared_with` does not share memory"
    The `shared_with` field on an agent spec (e.g., `shared_with: ["*"]`) controls **who can use the agent**, not who sees the private memory. Even if an agent is shared with all users, each user's `remember`/`recall`/`search_memory` data remains private. Use shared memory pools or team shared memory for cross-user data.

## Shared Memory (Cross-User by Design)

Three separate knobs expose cross-user state; each has its own tools and its own namespace contextvar:

| Knob | Where configured | Tool names | Scope |
|------|------------------|------------|-------|
| **Agent-level shared pools** | `PrimitiveConfig.memory.shared_namespaces` on the agent spec | `share_to`, `read_from_pool`, `search_pool`, `list_pool` | Cross-user, one per pool name |
| **Team shared memory** | `TeamSpec.shared_memory_namespace` | `share_finding`, `read_shared`, `search_shared`, `list_shared` | Cross-user, one per team |
| **Private memory** (default) | `PrimitiveConfig.memory.namespace` on the agent spec | `remember`, `recall`, `search_memory`, `forget`, `list_memories` | Per-user (`:u:{encoded_user_id}` suffix) |

All three hit the same `registry.memory` provider — the only difference is the namespace string each tool uses. Private memory appends a terminal encoded user ID; shared memory does not.

### Example: worker agent with all three

```yaml
# In the agent spec used as a team worker:
primitives:
  memory:
    enabled: true
    namespace: "agent:{agent_name}"        # private, always ends with :u:{caller_id}
    shared_namespaces:                     # user-owned agent pools (cross-user)
      - "pool:{agent_owner}:engineering-docs"
      - "pool:{agent_owner}:{agent_name}-shared"
```

```yaml
# In a user-owned team spec:
shared_memory_namespace: "team:{team_owner}:{team_name}"
```

When Alice chats with a shared `researcher` agent, her private `remember` data remains in her own `:u:alice` namespace, while `share_to("pool:{agent_owner}:engineering-docs", "api-spec")` uses the agent owner's pool. During a team run, worker private/working memory includes the team owner, caller and run ID; `share_finding("budget")` instead uses the team's explicitly shared namespace across authorized callers and runs.

User-created agent pools must resolve under `pool:{agent_owner}:…`; user-created team pools must resolve under `team:{team_owner}:…`. Owner IDs are encoded as one namespace segment. A user can instead reference an **already deployed pool** shared through another owner's accessible agent or team. Operator-seeded (`system`) specs may retain existing unscoped pool names such as `org:engineering-docs`. Merely declaring a name in a new spec never grants access to an unshared pool; private `:u:` namespaces cannot be configured as shared pools. Shared pools stay cross-user for people allowed to access their declaring agent/team, and the REST memory API enforces the same access policy.

## Best Practices

- **Use `agent:{agent_name}`** for most cases (default). Memories persist across sessions and are user-scoped when auth is active.
- **Include `{session_id}`** only if you want session-isolated memory stores (rare).
- **Use `shared_namespaces`** on the agent spec for cross-user pools (e.g., organizational docs).
- **Use `shared_memory_namespace`** on the team spec for worker-to-worker collaboration inside a team run.
- **The UI memory panel** shows only the caller's exact private namespace for an agent, not arbitrary child namespaces.
