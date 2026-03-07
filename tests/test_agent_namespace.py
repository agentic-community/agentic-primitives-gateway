"""Tests for agent namespace resolution and memory context loading."""

from __future__ import annotations

from agentic_primitives_gateway.agents.namespace import (
    resolve_knowledge_namespace,
    resolve_knowledge_namespace_for_name,
)
from agentic_primitives_gateway.models.agents import AgentSpec, PrimitiveConfig


class TestResolveKnowledgeNamespace:
    def _make_spec(self, namespace: str | None = None) -> AgentSpec:
        primitives = {}
        if namespace is not None:
            primitives["memory"] = PrimitiveConfig(enabled=True, namespace=namespace)
        return AgentSpec(name="test-agent", model="test-model", primitives=primitives)

    def test_default_namespace(self) -> None:
        spec = self._make_spec()
        assert resolve_knowledge_namespace(spec) == "agent:test-agent"

    def test_explicit_namespace_without_session(self) -> None:
        spec = self._make_spec("myapp:{agent_name}")
        assert resolve_knowledge_namespace(spec) == "myapp:test-agent"

    def test_strips_session_id_placeholder(self) -> None:
        spec = self._make_spec("agent:{agent_name}:{session_id}")
        assert resolve_knowledge_namespace(spec) == "agent:test-agent"

    def test_strips_session_id_without_colon(self) -> None:
        spec = self._make_spec("{agent_name}-{session_id}")
        assert resolve_knowledge_namespace(spec) == "test-agent-"

    def test_no_memory_config(self) -> None:
        spec = AgentSpec(name="test-agent", model="test-model")
        assert resolve_knowledge_namespace(spec) == "agent:test-agent"

    def test_memory_disabled(self) -> None:
        spec = AgentSpec(
            name="test-agent",
            model="test-model",
            primitives={"memory": PrimitiveConfig(enabled=False, namespace="custom:{agent_name}:{session_id}")},
        )
        # Still resolves even if disabled — caller decides whether to use it
        assert resolve_knowledge_namespace(spec) == "custom:test-agent"


class TestResolveKnowledgeNamespaceForName:
    def test_with_template(self) -> None:
        assert resolve_knowledge_namespace_for_name("bot", "ns:{agent_name}:{session_id}") == "ns:bot"

    def test_without_template(self) -> None:
        assert resolve_knowledge_namespace_for_name("bot", None) == "agent:bot"

    def test_no_session_id_in_template(self) -> None:
        assert resolve_knowledge_namespace_for_name("bot", "custom:{agent_name}") == "custom:bot"


class TestNamespaceIsolation:
    """Ensure namespace resolution doesn't leak between agents with similar names."""

    def test_prefix_isolation(self) -> None:
        ns1 = resolve_knowledge_namespace_for_name("bot", None)
        ns2 = resolve_knowledge_namespace_for_name("bot-2", None)
        assert ns1 == "agent:bot"
        assert ns2 == "agent:bot-2"
        assert not ns2.startswith(ns1 + ":")
