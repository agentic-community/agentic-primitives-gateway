"""Shared namespace resolution for agent memory."""

from __future__ import annotations

from agentic_primitives_gateway.models.agents import AgentSpec


def resolve_knowledge_namespace(spec: AgentSpec) -> str:
    """Resolve the agent-scoped knowledge namespace (no session_id).

    Memory tools (remember/recall/search) use this namespace so that
    stored facts persist across sessions. The {session_id} placeholder
    is stripped — session scoping is only for conversation history.
    """
    mem_config = spec.primitives.get("memory")
    ns = mem_config.namespace if mem_config and mem_config.namespace else "agent:{agent_name}"
    ns = ns.replace(":{session_id}", "").replace("{session_id}", "")
    return ns.replace("{agent_name}", spec.name).rstrip(":")


def resolve_knowledge_namespace_for_name(name: str, namespace_template: str | None) -> str:
    """Resolve knowledge namespace from an agent name and optional template.

    Used by routes that don't have a full AgentSpec but need the same logic.
    """
    ns = namespace_template or "agent:{agent_name}"
    ns = ns.replace(":{session_id}", "").replace("{session_id}", "")
    return ns.replace("{agent_name}", name).rstrip(":")
