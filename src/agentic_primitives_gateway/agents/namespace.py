"""Shared namespace *templates* for agent memory, knowledge, and conversation history.

This module is the pure-function layer for namespace resolution: it
takes an agent spec + principal and returns a resolved namespace
string.  It does not touch contextvars — that wiring lives in each
primitive's ``context.py`` module and the runners set the contextvars
using the values this module computes.

Memory and conversation-history keys are scoped on three axes:

1. **Agent identity** (``owner_id`` + ``name``) — Alice's ``researcher``
   and Bob's forked ``researcher`` are fully isolated even though they
   share a bare name.
2. **Caller identity** (``principal.id``) — two users chatting with the
   same agent never share memory.  Applied here via the trailing
   ``:u:{principal.id}`` suffix.
3. **Session** (``session_id``) — applied separately via
   :func:`resolve_actor_id`.

Shared memory pools (``PrimitiveConfig.memory.shared_namespaces``) are
a deliberate exception to user-scoping: pools are cross-user by
design, so ``resolve_shared_pools`` does **not** append ``:u:``.

Knowledge namespaces point at a bulk-indexed, often shared corpus
(support KB, product docs, code repository).  They are agent-level,
not user-level — every user chatting with the same agent should hit
the same corpus.  :func:`resolve_knowledge_namespace` does not append
``:u:{principal.id}`` automatically; deployments that need per-user
corpora can include ``{principal_id}`` in their template explicitly.

Templates accept ``{agent_owner}`` and ``{agent_name}`` placeholders.

User-scope format convention — IMPORTANT
----------------------------------------

Every user-scoped key uses ``:u:{user_id}`` with ``{user_id}`` as the
**terminal segment** — no trailing colons or extra segments after
the user id.  The route-level parser in
:func:`routes._helpers.require_user_scoped` takes everything after
the ``:u:`` marker as the owner id, so::

    agent:alice:bot:u:alice          # parsed owner = "alice"          ✓
    knowledge:system:support:u:alice # parsed owner = "alice"          ✓
    :u:alice:kb                      # parsed owner = "alice:kb"       ✗

The last form would 403 alice from what looks like her own
namespace.  If you're constructing a namespace in a new code path,
put the user segment at the end.
"""

from __future__ import annotations

from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.models.agents import AgentSpec

_DEFAULT_NS_TEMPLATE = "agent:{agent_owner}:{agent_name}"
_DEFAULT_KNOWLEDGE_NS_TEMPLATE = "knowledge:{agent_owner}:{agent_name}"


def _substitute(template: str, *, agent_owner: str, agent_name: str) -> str:
    ns = template.replace(":{session_id}", "").replace("{session_id}", "")
    ns = ns.replace("{agent_owner}", agent_owner)
    ns = ns.replace("{agent_name}", agent_name)
    return ns.rstrip(":")


def resolve_memory_namespace(spec: AgentSpec, principal: AuthenticatedPrincipal) -> str:
    """Resolve the user-scoped memory namespace.

    Memory tools (remember/recall/search) use this namespace so that
    stored facts persist across sessions but are isolated per
    agent-owner *and* per user.  The ``{session_id}`` placeholder is
    stripped — session scoping is handled by :func:`resolve_actor_id`.
    """
    mem_config = spec.primitives.get("memory")
    template = mem_config.namespace if mem_config and mem_config.namespace else _DEFAULT_NS_TEMPLATE
    base = _substitute(template, agent_owner=spec.owner_id, agent_name=spec.name)
    return f"{base}:u:{principal.id}"


def resolve_memory_namespace_for_identity(
    *,
    name: str,
    owner_id: str,
    namespace_template: str | None,
    principal: AuthenticatedPrincipal,
) -> str:
    """Identity-based variant of :func:`resolve_memory_namespace`."""
    template = namespace_template or _DEFAULT_NS_TEMPLATE
    base = _substitute(template, agent_owner=owner_id, agent_name=name)
    return f"{base}:u:{principal.id}"


def resolve_knowledge_namespace(spec: AgentSpec, principal: AuthenticatedPrincipal) -> str:
    """Resolve the knowledge namespace for the agent's ``search_knowledge`` tool.

    Reads ``primitives.knowledge.namespace`` verbatim (with ``{agent_owner}``
    / ``{agent_name}`` substitution).  Falls back to a default template
    when no namespace is configured — same shape as
    :func:`resolve_memory_namespace`, so every caller just reads a string.

    Unlike memory, knowledge is **not** user-scoped by default: a support
    bot's KB is the same corpus for every caller.  Deployments that need
    per-user corpora can include ``{principal_id}`` in their template
    explicitly — but it's not added automatically.

    ``{session_id}`` placeholders are silently stripped (inherited from
    :func:`_substitute`).  That's appropriate for memory, where session
    scoping is handled by ``resolve_actor_id``, but for knowledge it's
    arguably a surprise — a bulk-indexed corpus usually doesn't make
    sense to scope by session.  We keep the strip for template-format
    consistency with memory, but operators should NOT include
    ``{session_id}`` in knowledge templates.
    """
    k_config = spec.primitives.get("knowledge")
    template = k_config.namespace if k_config and k_config.namespace else _DEFAULT_KNOWLEDGE_NS_TEMPLATE
    ns = _substitute(template, agent_owner=spec.owner_id, agent_name=spec.name)
    # Allow explicit user-scoping via a ``{principal_id}`` placeholder
    # without forcing it on every deployment.
    return ns.replace("{principal_id}", principal.id)


def resolve_shared_pools(spec: AgentSpec) -> dict[str, str] | None:
    """Resolve shared memory pools from the agent's memory primitive config.

    Pool namespaces are **cross-user** — no ``:u:`` suffix.  The whole
    point of a shared pool is that Alice and Bob hit the same data;
    previously this function appended ``:u:{principal.id}`` which made
    each pool silently per-user, masking the feature.

    Returns a dict mapping the pool's declared name (as the LLM refers
    to it) to the resolved namespace the memory provider should use.
    """
    mem_config = spec.primitives.get("memory")
    if not mem_config or not mem_config.shared_namespaces:
        return None
    pools: dict[str, str] = {}
    for ns in mem_config.shared_namespaces:
        pools[ns] = _substitute(ns, agent_owner=spec.owner_id, agent_name=spec.name)
    return pools


def resolve_actor_id(spec: AgentSpec, principal: AuthenticatedPrincipal) -> str:
    """Resolve the actor_id for conversation history scoping.

    Format: ``"{owner_id}:{name}:u:{principal.id}"``.  Owner-scoped so
    forks don't share conversation history with the upstream agent.
    The ``:u:`` separator prevents collision with agent names that end
    with a user-id-shaped string.
    """
    return f"{spec.owner_id}:{spec.name}:u:{principal.id}"


def resolve_actor_id_for_identity(*, name: str, owner_id: str, principal: AuthenticatedPrincipal) -> str:
    """Identity-based variant of :func:`resolve_actor_id`."""
    return f"{owner_id}:{name}:u:{principal.id}"
