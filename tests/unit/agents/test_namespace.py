"""Tests for agent namespace resolution and memory context loading.

Memory and conversation-history namespaces are now owner-scoped:
``agent:{owner_id}:{name}:u:{user_id}`` — so Alice's forked ``researcher``
cannot read Bob's fork's memory even though they share a bare name.
"""

from __future__ import annotations

import pytest

from agentic_primitives_gateway.agents.namespace import (
    resolve_actor_id,
    resolve_actor_id_for_identity,
    resolve_memory_namespace,
    resolve_memory_namespace_for_identity,
)
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.models.agents import AgentSpec, PrimitiveConfig

_ALICE = AuthenticatedPrincipal(id="alice", type="user")


class TestResolveMemoryNamespace:
    def _make_spec(self, namespace: str | None = None, owner_id: str = "system") -> AgentSpec:
        primitives = {}
        if namespace is not None:
            primitives["memory"] = PrimitiveConfig(enabled=True, namespace=namespace)
        return AgentSpec(
            name="test-agent",
            model="test-model",
            primitives=primitives,
            owner_id=owner_id,
        )

    def test_default_namespace_with_principal(self) -> None:
        spec = self._make_spec()
        assert resolve_memory_namespace(spec, _ALICE) == "agent:system:test-agent:u:alice"

    def test_owner_isolation(self) -> None:
        alice_spec = self._make_spec(owner_id="alice")
        bob_spec = self._make_spec(owner_id="bob")
        assert resolve_memory_namespace(alice_spec, _ALICE) != resolve_memory_namespace(bob_spec, _ALICE)

    def test_requires_principal(self) -> None:
        spec = self._make_spec()
        with pytest.raises(TypeError):
            resolve_memory_namespace(spec)  # type: ignore[call-arg]

    def test_explicit_namespace_without_session(self) -> None:
        spec = self._make_spec("myapp:{agent_name}")
        assert resolve_memory_namespace(spec, _ALICE) == "myapp:test-agent:u:alice"

    def test_strips_session_id_placeholder(self) -> None:
        spec = self._make_spec("agent:{agent_owner}:{agent_name}:{session_id}")
        assert resolve_memory_namespace(spec, _ALICE) == "agent:system:test-agent:u:alice"

    def test_strips_session_id_without_colon(self) -> None:
        spec = self._make_spec("{agent_name}-{session_id}")
        assert resolve_memory_namespace(spec, _ALICE) == "test-agent-:u:alice"

    def test_no_memory_config(self) -> None:
        spec = AgentSpec(name="test-agent", model="test-model", owner_id="system")
        assert resolve_memory_namespace(spec, _ALICE) == "agent:system:test-agent:u:alice"

    def test_memory_disabled(self) -> None:
        spec = AgentSpec(
            name="test-agent",
            model="test-model",
            owner_id="system",
            primitives={"memory": PrimitiveConfig(enabled=False, namespace="custom:{agent_name}:{session_id}")},
        )
        assert resolve_memory_namespace(spec, _ALICE) == "custom:test-agent:u:alice"

    def test_different_users_different_namespaces(self) -> None:
        spec = self._make_spec()
        bob = AuthenticatedPrincipal(id="bob", type="user")
        assert resolve_memory_namespace(spec, _ALICE) != resolve_memory_namespace(spec, bob)


class TestResolveMemoryNamespaceForIdentity:
    def test_with_template(self) -> None:
        assert (
            resolve_memory_namespace_for_identity(
                name="bot",
                owner_id="system",
                namespace_template="ns:{agent_owner}:{agent_name}:{session_id}",
                principal=_ALICE,
            )
            == "ns:system:bot:u:alice"
        )

    def test_without_template(self) -> None:
        assert (
            resolve_memory_namespace_for_identity(
                name="bot", owner_id="system", namespace_template=None, principal=_ALICE
            )
            == "agent:system:bot:u:alice"
        )

    def test_no_session_id_in_template(self) -> None:
        assert (
            resolve_memory_namespace_for_identity(
                name="bot",
                owner_id="system",
                namespace_template="custom:{agent_name}",
                principal=_ALICE,
            )
            == "custom:bot:u:alice"
        )


class TestActorId:
    def test_actor_id_includes_owner_and_user(self) -> None:
        spec = AgentSpec(name="bot", model="m", owner_id="alice")
        assert resolve_actor_id(spec, _ALICE) == "alice:bot:u:alice"

    def test_actor_id_for_identity(self) -> None:
        assert resolve_actor_id_for_identity(name="bot", owner_id="system", principal=_ALICE) == "system:bot:u:alice"


class TestNamespaceIsolation:
    """Ensure namespace resolution doesn't leak between agents with similar names."""

    def test_prefix_isolation(self) -> None:
        ns1 = resolve_memory_namespace_for_identity(
            name="bot", owner_id="system", namespace_template=None, principal=_ALICE
        )
        ns2 = resolve_memory_namespace_for_identity(
            name="bot-2", owner_id="system", namespace_template=None, principal=_ALICE
        )
        assert ns1 == "agent:system:bot:u:alice"
        assert ns2 == "agent:system:bot-2:u:alice"
        assert not ns2.startswith(ns1 + ":")

    def test_user_isolation(self) -> None:
        bob = AuthenticatedPrincipal(id="bob", type="user")
        ns_alice = resolve_memory_namespace_for_identity(
            name="bot", owner_id="system", namespace_template=None, principal=_ALICE
        )
        ns_bob = resolve_memory_namespace_for_identity(
            name="bot", owner_id="system", namespace_template=None, principal=bob
        )
        assert ns_alice != ns_bob

    def test_owner_isolation(self) -> None:
        ns_alice = resolve_memory_namespace_for_identity(
            name="researcher",
            owner_id="alice",
            namespace_template=None,
            principal=_ALICE,
        )
        ns_bob = resolve_memory_namespace_for_identity(
            name="researcher",
            owner_id="bob",
            namespace_template=None,
            principal=_ALICE,
        )
        assert ns_alice != ns_bob
