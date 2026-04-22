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

The `{session_id}` placeholder is **stripped** for the memory store, and `{user_id}` is injected when auth is active:

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

## Fallback to Child Namespaces

If the memory namespace is empty, the runner also searches child namespaces:

```
Memory namespace:  agent:research-assistant       (empty)
Child namespaces:  agent:research-assistant:abc123 (has memories)
                   agent:research-assistant:def456 (has memories)
```

This handles cases where memories were stored in session-scoped namespaces before the memory/session split.

!!! warning "Multi-tenancy safety"
    The child search uses `namespace + ":"` as the prefix (with trailing colon). This prevents `agent:bot` from matching `agent:bot-2`'s memories.

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
| **Private memory** (default) | `PrimitiveConfig.memory.namespace` on the agent spec | `remember`, `recall`, `search_memory`, `forget`, `list_memories` | Per-user (`:u:{principal.id}` suffix) |

All three hit the same `registry.memory` provider — the only difference is the namespace string each tool uses. Private memory appends `:u:{principal.id}` to the template; shared memory does not.

### Example: worker agent with all three

```yaml
# In the agent spec used as a team worker:
primitives:
  memory:
    enabled: true
    namespace: "agent:{agent_name}"        # → "agent:researcher:u:alice" (private)
    shared_namespaces:                     # agent-level pools (cross-user)
      - "org:engineering-docs"
      - "project:{agent_name}-shared"
```

```yaml
# In the team spec:
shared_memory_namespace: "team:{team_name}"  # → "team:research-team" (cross-user, team-wide)
```

When Alice chats with the shared `researcher` agent inside a team run:

- `remember("my favorite color")` → `agent:researcher:u:alice` (only Alice sees it)
- `share_finding("budget")` → `team:research-team` (visible to everyone on the team, forever)
- `share_to("org:engineering-docs", "api-spec")` → `org:engineering-docs` (visible to every user who uses an agent configured with that pool)

## Best Practices

- **Use `agent:{agent_name}`** for most cases (default). Memories persist across sessions and are user-scoped when auth is active.
- **Include `{session_id}`** only if you want session-isolated memory stores (rare).
- **Use `shared_namespaces`** on the agent spec for cross-user pools (e.g., organizational docs).
- **Use `shared_memory_namespace`** on the team spec for worker-to-worker collaboration inside a team run.
- **The UI memory panel** shows all namespaces for an agent, including child namespaces.
