# Memory Namespaces

How agent memory is organized and why knowledge namespaces are separate from session IDs.

## Two Types of Memory

Agents have two separate memory systems:

| Type | Scoped by | Persists across sessions? | Used for |
|------|-----------|--------------------------|----------|
| **Knowledge store** | Agent name + user ID | Yes | Facts, preferences, learned information |
| **Conversation history** | Agent name + user ID + session ID | No (per session) | Recent turns for context |

## Knowledge Namespace

The knowledge namespace is resolved from the agent spec:

```yaml
primitives:
  memory:
    enabled: true
    namespace: "agent:{agent_name}:{session_id}"  # Template
```

The `{session_id}` placeholder is **stripped** for the knowledge store, and `{user_id}` is injected when auth is active:

- Template: `agent:{agent_name}:{session_id}`
- Knowledge namespace (authenticated): `agent:research-assistant:u:alice` (user-scoped, no session)
- Knowledge namespace (anonymous): `agent:research-assistant` (no user, no session)
- This is where `remember`, `recall`, `search_memory` tools read/write

This means when a user says "my name is Alice" and the agent stores it, it persists across all of **that user's** sessions -- but is invisible to other users.

## Conversation History

Conversation history uses `(actor_id, session_id)` directly:

- `actor_id` is user-scoped when auth is active: `{agent_name}:u:{user_id}` (e.g., `research-assistant:u:alice`)
- `actor_id` without auth: `{agent_name}` (agent-scoped only)
- `auto_memory: true` → each turn is stored as an event
- On new conversations, recent turns are loaded from the same session
- Different sessions have independent histories

## Memory Context Injection

At the start of a new conversation (no history loaded), the runner:

1. Fetches memories from the knowledge namespace
2. Formats them as a system-like preamble message
3. Injects into the conversation so the LLM knows what it stored before

This means the agent "remembers" facts from previous sessions even with a new session ID.

## Fallback to Child Namespaces

If the knowledge namespace is empty, the runner also searches child namespaces:

```
Knowledge namespace: agent:research-assistant       (empty)
Child namespaces:    agent:research-assistant:abc123 (has memories)
                     agent:research-assistant:def456 (has memories)
```

This handles backward compatibility when memories were stored before the knowledge/session split.

!!! warning "Multi-tenancy safety"
    The child search uses `namespace + ":"` as the prefix (with trailing colon). This prevents `agent:bot` from matching `agent:bot-2`'s memories.

## User Isolation

When JWT auth is active, two users chatting with the same agent have **completely isolated memory**:

- User Alice's knowledge namespace: `agent:research-assistant:u:alice`
- User Bob's knowledge namespace: `agent:research-assistant:u:bob`
- Their conversation histories are also isolated (different `actor_id` values)

!!! note "`shared_with` does not share memory"
    The `shared_with` field on an agent spec (e.g., `shared_with: ["*"]`) controls **who can use the agent**, not who sees the memory. Even if an agent is shared with all users, each user's memories and conversations remain private.

## Best Practices

- **Use `agent:{agent_name}`** for most cases (default). Memories persist across sessions and are user-scoped when auth is active.
- **Include `{session_id}`** only if you want session-isolated knowledge stores (rare).
- **Use explicit namespaces** like `project:acme` for shared team memory.
- **The UI memory panel** shows all namespaces for an agent, including child namespaces.
