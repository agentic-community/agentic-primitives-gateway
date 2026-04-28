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
    resolve_knowledge_namespace,
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


class TestResolveKnowledgeNamespace:
    """Intent: knowledge namespaces are shared-by-default, opt-in user-scoped.

    A support bot's KB is the same corpus for every caller — ``agents/namespace.py``
    deliberately does NOT append ``:u:{principal.id}`` the way memory does.
    Deployments that *do* want per-user corpora opt in with a ``{principal_id}``
    placeholder in the template, which then gets substituted.

    Both halves matter:
      * If someone copy-pastes the memory pattern and force-appends ``:u:``,
        every multi-user support bot silently gets per-user corpora and the
        shared KB breaks — same failure shape as the shared-pool incident.
      * If someone "simplifies" the template substitution and drops the
        ``{principal_id}`` branch, deployments that opted into per-user
        corpora silently lose isolation.
    """

    def _spec(self, namespace: str | None = None, owner_id: str = "system") -> AgentSpec:
        primitives = {}
        if namespace is not None:
            primitives["knowledge"] = PrimitiveConfig(enabled=True, namespace=namespace)
        return AgentSpec(
            name="support-bot",
            model="m",
            primitives=primitives,
            owner_id=owner_id,
        )

    def test_default_template_is_shared_across_users(self) -> None:
        """Alice and Bob on the same agent MUST resolve to the same knowledge namespace."""
        spec = self._spec()
        bob = AuthenticatedPrincipal(id="bob", type="user")
        assert resolve_knowledge_namespace(spec, _ALICE) == resolve_knowledge_namespace(spec, bob)

    def test_default_template_shape(self) -> None:
        spec = self._spec()
        assert resolve_knowledge_namespace(spec, _ALICE) == "knowledge:system:support-bot"

    def test_explicit_template_without_principal_id_is_shared(self) -> None:
        """An explicit template that doesn't reference ``{principal_id}`` is still shared."""
        spec = self._spec(namespace="corpus:{agent_name}")
        bob = AuthenticatedPrincipal(id="bob", type="user")
        assert resolve_knowledge_namespace(spec, _ALICE) == resolve_knowledge_namespace(spec, bob)
        assert resolve_knowledge_namespace(spec, _ALICE) == "corpus:support-bot"

    def test_principal_id_placeholder_opts_in_to_user_scoping(self) -> None:
        """When the template DOES contain ``{principal_id}``, per-user isolation kicks in."""
        spec = self._spec(namespace="corpus:{agent_name}:u:{principal_id}")
        bob = AuthenticatedPrincipal(id="bob", type="user")
        alice_ns = resolve_knowledge_namespace(spec, _ALICE)
        bob_ns = resolve_knowledge_namespace(spec, bob)
        assert alice_ns == "corpus:support-bot:u:alice"
        assert bob_ns == "corpus:support-bot:u:bob"
        assert alice_ns != bob_ns

    def test_session_id_placeholder_is_silently_stripped(self) -> None:
        """Intent: ``{session_id}`` in a knowledge template is silently removed.

        This behavior is inherited from :func:`_substitute` (memory uses
        the same strip to hand session scoping off to ``resolve_actor_id``).
        For knowledge it's a surprise — a bulk-indexed corpus usually
        shouldn't be session-scoped — but the strip is consistent with
        the rest of the template-resolution layer, so we pin it rather
        than flip to an error.

        If someone changes this to raise instead of strip (perhaps a
        reasonable design choice), this test fails and the author has
        to update the namespace.py docstring + any deployments that
        put ``{session_id}`` in a knowledge template.
        """
        spec = self._spec(namespace="corpus:{agent_name}:{session_id}")
        # Both users resolve to the same stripped namespace — session
        # and user scoping are both absent by design.
        assert resolve_knowledge_namespace(spec, _ALICE) == "corpus:support-bot"
