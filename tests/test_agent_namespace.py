"""Tests for agent namespace resolution and memory context loading."""

from __future__ import annotations

from agentic_primitives_gateway.agents.namespace import (
    resolve_knowledge_namespace,
    resolve_knowledge_namespace_for_name,
)
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.models.agents import AgentSpec, PrimitiveConfig

_ALICE = AuthenticatedPrincipal(id="alice", type="user")


class TestResolveKnowledgeNamespace:
    def _make_spec(self, namespace: str | None = None) -> AgentSpec:
        primitives = {}
        if namespace is not None:
            primitives["memory"] = PrimitiveConfig(enabled=True, namespace=namespace)
        return AgentSpec(name="test-agent", model="test-model", primitives=primitives)

    def test_default_namespace_with_principal(self) -> None:
        spec = self._make_spec()
        assert resolve_knowledge_namespace(spec, _ALICE) == "agent:test-agent:u:alice"

    def test_default_namespace_no_principal(self) -> None:
        spec = self._make_spec()
        assert resolve_knowledge_namespace(spec) == "agent:test-agent:u:anonymous"

    def test_explicit_namespace_without_session(self) -> None:
        spec = self._make_spec("myapp:{agent_name}")
        assert resolve_knowledge_namespace(spec, _ALICE) == "myapp:test-agent:u:alice"

    def test_strips_session_id_placeholder(self) -> None:
        spec = self._make_spec("agent:{agent_name}:{session_id}")
        assert resolve_knowledge_namespace(spec, _ALICE) == "agent:test-agent:u:alice"

    def test_strips_session_id_without_colon(self) -> None:
        spec = self._make_spec("{agent_name}-{session_id}")
        assert resolve_knowledge_namespace(spec, _ALICE) == "test-agent-:u:alice"

    def test_no_memory_config(self) -> None:
        spec = AgentSpec(name="test-agent", model="test-model")
        assert resolve_knowledge_namespace(spec, _ALICE) == "agent:test-agent:u:alice"

    def test_memory_disabled(self) -> None:
        spec = AgentSpec(
            name="test-agent",
            model="test-model",
            primitives={"memory": PrimitiveConfig(enabled=False, namespace="custom:{agent_name}:{session_id}")},
        )
        assert resolve_knowledge_namespace(spec, _ALICE) == "custom:test-agent:u:alice"

    def test_different_users_different_namespaces(self) -> None:
        spec = self._make_spec()
        bob = AuthenticatedPrincipal(id="bob", type="user")
        assert resolve_knowledge_namespace(spec, _ALICE) != resolve_knowledge_namespace(spec, bob)


class TestResolveKnowledgeNamespaceForName:
    def test_with_template(self) -> None:
        assert resolve_knowledge_namespace_for_name("bot", "ns:{agent_name}:{session_id}", _ALICE) == "ns:bot:u:alice"

    def test_without_template(self) -> None:
        assert resolve_knowledge_namespace_for_name("bot", None, _ALICE) == "agent:bot:u:alice"

    def test_no_session_id_in_template(self) -> None:
        assert resolve_knowledge_namespace_for_name("bot", "custom:{agent_name}", _ALICE) == "custom:bot:u:alice"

    def test_no_principal(self) -> None:
        assert resolve_knowledge_namespace_for_name("bot", None) == "agent:bot:u:anonymous"


class TestNamespaceIsolation:
    """Ensure namespace resolution doesn't leak between agents with similar names."""

    def test_prefix_isolation(self) -> None:
        ns1 = resolve_knowledge_namespace_for_name("bot", None, _ALICE)
        ns2 = resolve_knowledge_namespace_for_name("bot-2", None, _ALICE)
        assert ns1 == "agent:bot:u:alice"
        assert ns2 == "agent:bot-2:u:alice"
        assert not ns2.startswith(ns1 + ":")

    def test_user_isolation(self) -> None:
        bob = AuthenticatedPrincipal(id="bob", type="user")
        ns_alice = resolve_knowledge_namespace_for_name("bot", None, _ALICE)
        ns_bob = resolve_knowledge_namespace_for_name("bot", None, bob)
        assert ns_alice != ns_bob
