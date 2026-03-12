"""Shared namespace resolution for agent memory."""

from __future__ import annotations

from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.models.agents import AgentSpec


def resolve_knowledge_namespace(spec: AgentSpec, principal: AuthenticatedPrincipal) -> str:
    """Resolve the user-scoped knowledge namespace.

    Memory tools (remember/recall/search) use this namespace so that
    stored facts persist across sessions but are isolated per user.
    The {session_id} placeholder is stripped — session scoping is
    handled separately via ``resolve_actor_id``.

    Raises:
        RuntimeError: If no principal is provided.
    """
    mem_config = spec.primitives.get("memory")
    ns = mem_config.namespace if mem_config and mem_config.namespace else "agent:{agent_name}"
    ns = ns.replace(":{session_id}", "").replace("{session_id}", "")
    base = ns.replace("{agent_name}", spec.name).rstrip(":")
    return f"{base}:u:{principal.id}"


def resolve_knowledge_namespace_for_name(
    name: str, namespace_template: str | None, principal: AuthenticatedPrincipal
) -> str:
    """Resolve knowledge namespace from an agent name and optional template.

    Used by routes that don't have a full AgentSpec but need the same logic.
    """
    ns = namespace_template or "agent:{agent_name}"
    ns = ns.replace(":{session_id}", "").replace("{session_id}", "")
    base = ns.replace("{agent_name}", name).rstrip(":")
    return f"{base}:u:{principal.id}"


def resolve_actor_id(agent_name: str, principal: AuthenticatedPrincipal) -> str:
    """Resolve the actor_id for conversation history scoping.

    The actor_id is the primary key for isolating sessions and conversation
    history in the memory provider. It always includes the principal ID
    so that different users have isolated conversations.

    The ``:u:`` separator prevents collision with agent names that happen
    to end with a user ID string.
    """
    return f"{agent_name}:u:{principal.id}"
