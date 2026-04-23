"""Intent-level test: forking an agent isolates memory.

The user-visible contract of fork is that Alice gets her own copy.
"Her own" means memory keys don't leak between the source owner and
the forked owner — otherwise a fork would inherit the source's
private facts, which defeats the whole "personal copy" story.

The individual pieces (``resolve_memory_namespace`` embeds
``{agent_owner}``; ``FileAgentStore.fork`` copies the spec into the
target owner's namespace) are each unit-tested.  Nothing asserts the
end-to-end "Bob writes → Alice forks → Alice's search comes back
empty" contract.  If either piece silently regresses so that Alice's
resolved namespace happened to equal Bob's, every existing test would
still pass.  This file closes that gap.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentic_primitives_gateway.agents.file_store import FileAgentStore
from agentic_primitives_gateway.agents.namespace import resolve_memory_namespace
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.models.agents import AgentSpec, PrimitiveConfig
from agentic_primitives_gateway.primitives.memory.in_memory import InMemoryProvider

_ALICE = AuthenticatedPrincipal(id="alice", type="user", groups=frozenset(), scopes=frozenset())
_BOB = AuthenticatedPrincipal(id="bob", type="user", groups=frozenset(), scopes=frozenset())


@pytest.fixture
async def agent_store(tmp_path: Any) -> FileAgentStore:
    return FileAgentStore(path=str(tmp_path / "agents.json"))


def _make_spec(owner_id: str, name: str = "researcher") -> AgentSpec:
    return AgentSpec(
        name=name,
        model="m",
        primitives={"memory": PrimitiveConfig(enabled=True, namespace="agent:{agent_owner}:{agent_name}")},
        owner_id=owner_id,
    )


class TestForkMemoryIsolation:
    @pytest.mark.asyncio
    async def test_bob_remembers_alice_forks_alice_cannot_recall(self, agent_store: FileAgentStore) -> None:
        """Bob creates `researcher`, writes a memory, Alice forks → Alice's search is empty.

        Walks the whole namespace→store→fork→namespace path with real
        values (``AgentSpec``, ``InMemoryProvider``, ``FileAgentStore``)
        so a silent regression at any layer — Alice's resolved
        namespace happens to match Bob's; fork forgets to rewrite
        ``owner_id``; the template stops including ``{agent_owner}`` —
        causes the assertion to fail.
        """
        # Bob creates his researcher agent.
        await agent_store.create_version(
            name="researcher",
            owner_id="bob",
            spec=_make_spec("bob"),
            created_by="bob",
        )
        bob_spec = _make_spec("bob")

        # Shared backing store — the point is that even against the
        # same underlying provider, Alice and Bob see different data.
        memory = InMemoryProvider()

        # Bob remembers something private.
        bob_ns = resolve_memory_namespace(bob_spec, _BOB)
        await memory.store(namespace=bob_ns, key="api-key", content="bob-sekret")

        # Alice forks Bob's agent into her own namespace.
        fork_version = await agent_store.fork(
            source_name="researcher",
            source_owner_id="bob",
            target_owner_id="alice",
            created_by="alice",
        )

        # Alice's resolved namespace for her fork.
        alice_spec = fork_version.spec
        alice_ns = resolve_memory_namespace(alice_spec, _ALICE)

        # Sanity: the two namespaces must differ — otherwise fork
        # didn't create an independent identity.
        assert alice_ns != bob_ns

        # Contract: Alice cannot retrieve Bob's memory.
        leaked = await memory.retrieve(namespace=alice_ns, key="api-key")
        assert leaked is None, f"Alice saw Bob's private memory (ns={alice_ns})"

        # Contract: Alice's search finds nothing.
        results = await memory.search(namespace=alice_ns, query="api-key")
        assert results == []

        # Bob still has his memory (we didn't accidentally move it).
        bob_record = await memory.retrieve(namespace=bob_ns, key="api-key")
        assert bob_record is not None
        assert bob_record.content == "bob-sekret"

    @pytest.mark.asyncio
    async def test_forks_by_two_users_have_disjoint_namespaces(self, agent_store: FileAgentStore) -> None:
        """A system agent forked by Alice and by Bob → three disjoint namespaces.

        The namespaces must differ even when the forks share the same
        ``agent_name`` — this is the whole point of the
        ``{agent_owner}`` segment of the template.  A regression where
        ``{agent_owner}`` was dropped would make all three forks share
        memory: any user of the system agent would leak into every
        fork.
        """
        # System agent.
        system_spec = _make_spec("system", name="assistant")
        await agent_store.create_version(
            name="assistant",
            owner_id="system",
            spec=system_spec,
            created_by="system",
        )

        # Both users fork.
        alice_fork = await agent_store.fork(
            source_name="assistant",
            source_owner_id="system",
            target_owner_id="alice",
            created_by="alice",
        )
        bob_fork = await agent_store.fork(
            source_name="assistant",
            source_owner_id="system",
            target_owner_id="bob",
            created_by="bob",
        )

        sys_ns = resolve_memory_namespace(system_spec, _ALICE)
        alice_fork_ns = resolve_memory_namespace(alice_fork.spec, _ALICE)
        bob_fork_ns = resolve_memory_namespace(bob_fork.spec, _BOB)

        # Three resolved namespaces, all distinct.
        assert len({sys_ns, alice_fork_ns, bob_fork_ns}) == 3

        memory = InMemoryProvider()
        await memory.store(namespace=alice_fork_ns, key="note", content="from alice")
        await memory.store(namespace=bob_fork_ns, key="note", content="from bob")

        # Cross-reads return the correct owner's data, nothing leaks.
        alice_reads = await memory.retrieve(namespace=alice_fork_ns, key="note")
        bob_reads = await memory.retrieve(namespace=bob_fork_ns, key="note")
        assert alice_reads is not None and alice_reads.content == "from alice"
        assert bob_reads is not None and bob_reads.content == "from bob"

        # And the system namespace is empty — forks didn't bleed into the source.
        sys_note = await memory.retrieve(namespace=sys_ns, key="note")
        assert sys_note is None
