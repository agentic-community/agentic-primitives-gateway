"""Integration tests for the AgentCore memory primitive.

Full stack with real AWS calls:
AgenticPlatformClient → ASGI → middleware → route → registry →
AgentCoreMemoryProvider → real MemorySessionManager SDK.

Requires: AWS credentials + self-provisioned memory resource (via fixture).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from agentic_primitives_gateway_client import AgenticPlatformClient, AgenticPlatformError

pytestmark = pytest.mark.integration


# ── Helpers ───────────────────────────────────────────────────────────


def _unique(prefix: str = "integ") -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


# ── Key-value memory ─────────────────────────────────────────────────


class TestStoreAndRetrieve:
    async def test_store_and_retrieve(self, client: AgenticPlatformClient, memory_resource: str) -> None:
        ns = _unique("ns")
        key = _unique("key")

        record = await client.store_memory(ns, key, "hello integration")

        assert record["namespace"] == ns
        assert record["key"] == key
        assert record["content"] == "hello integration"

        retrieved = await client.retrieve_memory(ns, key)

        assert retrieved["content"] == "hello integration"
        assert retrieved["key"] == key

    async def test_store_with_metadata(self, client: AgenticPlatformClient, memory_resource: str) -> None:
        ns = _unique("ns")
        key = _unique("key")

        record = await client.store_memory(ns, key, "tagged content", {"env": "test", "version": "1"})

        assert record["metadata"]["env"] == "test"
        assert record["metadata"]["version"] == "1"

        retrieved = await client.retrieve_memory(ns, key)

        assert retrieved["metadata"]["env"] == "test"


class TestSearchMemory:
    async def test_search_memory(self, client: AgenticPlatformClient, memory_resource: str) -> None:
        ns = _unique("ns")

        await client.store_memory(ns, "planets-1", "Mars is the fourth planet from the Sun")
        await client.store_memory(ns, "planets-2", "Jupiter is the largest planet")
        await client.store_memory(ns, "food-1", "Pizza is a popular Italian dish")

        result = await client.search_memory(ns, "planets in our solar system", top_k=5)

        assert "results" in result
        # We expect at least one relevant hit
        assert len(result["results"]) >= 1


class TestListMemories:
    async def test_list_memories(self, client: AgenticPlatformClient, memory_resource: str) -> None:
        ns = _unique("ns")

        await client.store_memory(ns, "item-1", "first item")
        await client.store_memory(ns, "item-2", "second item")

        result = await client.list_memories(ns, limit=10)

        assert "records" in result
        assert len(result["records"]) >= 2


class TestDeleteMemory:
    async def test_delete_memory(self, client: AgenticPlatformClient, memory_resource: str) -> None:
        ns = _unique("ns")
        key = _unique("key")

        await client.store_memory(ns, key, "to be deleted")

        # Verify it exists
        await client.retrieve_memory(ns, key)

        # Delete it
        await client.delete_memory(ns, key)

        # Verify it's gone
        with pytest.raises(AgenticPlatformError) as exc_info:
            await client.retrieve_memory(ns, key)
        assert exc_info.value.status_code == 404


# ── Conversation events ──────────────────────────────────────────────


class TestCreateEvent:
    async def test_create_event(self, client: AgenticPlatformClient, memory_resource: str) -> None:
        actor_id = _unique("actor")
        session_id = _unique("sess")

        result = await client.create_event(
            actor_id,
            session_id,
            [{"text": "Hello from integration test", "role": "USER"}],
        )

        assert result["actor_id"] == actor_id
        assert result["session_id"] == session_id


class TestListEvents:
    async def test_list_events(self, client: AgenticPlatformClient, memory_resource: str) -> None:
        actor_id = _unique("actor")
        session_id = _unique("sess")

        await client.create_event(actor_id, session_id, [{"text": "msg 1", "role": "USER"}])
        await client.create_event(actor_id, session_id, [{"text": "msg 2", "role": "ASSISTANT"}])

        result = await client.list_events(actor_id, session_id)

        assert "events" in result
        assert len(result["events"]) >= 2


class TestGetLastTurns:
    async def test_get_last_turns(self, client: AgenticPlatformClient, memory_resource: str) -> None:
        actor_id = _unique("actor")
        session_id = _unique("sess")

        # Add several turns
        for i in range(5):
            await client.create_event(
                actor_id,
                session_id,
                [{"text": f"turn {i}", "role": "USER"}],
            )

        result = await client.get_last_turns(actor_id, session_id, k=3)

        assert "turns" in result
        assert len(result["turns"]) <= 3


class TestListActors:
    async def test_list_actors(self, client: AgenticPlatformClient, memory_resource: str) -> None:
        actor_id = _unique("actor")
        session_id = _unique("sess")

        # Ensure at least one actor exists
        await client.create_event(actor_id, session_id, [{"text": "hi", "role": "USER"}])

        result = await client.list_actors()

        assert "actors" in result
        assert len(result["actors"]) >= 1


class TestListSessions:
    async def test_list_sessions(self, client: AgenticPlatformClient, memory_resource: str) -> None:
        actor_id = _unique("actor")
        session_id = _unique("sess")

        await client.create_event(actor_id, session_id, [{"text": "hi", "role": "USER"}])

        result = await client.list_memory_sessions(actor_id)

        assert "sessions" in result
        assert len(result["sessions"]) >= 1


# ── Control plane — memory resources ─────────────────────────────────


@pytest.mark.xfail(reason="Memory resource provisioning takes 3-5 min; can't delete while CREATING")
class TestMemoryResourceLifecycle:
    async def test_memory_resource_lifecycle(self, client: AgenticPlatformClient) -> None:
        """Create, get, list, delete a memory resource (no fixture)."""
        name = f"integ_{uuid4().hex[:8]}"

        # Create
        created = await client.create_memory_resource(name)
        memory_id = created["memory_id"]
        assert created["name"] == name

        try:
            # Get
            fetched = await client.get_memory_resource(memory_id)
            assert fetched["memory_id"] == memory_id

            # List
            listed = await client.list_memory_resources()
            assert "resources" in listed
            ids = [r["memory_id"] for r in listed["resources"]]
            assert memory_id in ids
        finally:
            # Delete
            await client.delete_memory_resource(memory_id)


# ── Strategy management ──────────────────────────────────────────────


class TestStrategyManagement:
    async def test_strategy_management(self, client: AgenticPlatformClient, memory_resource: str) -> None:
        """Add strategy, list strategies, delete strategy."""
        # Add a semantic strategy — must match update_memory memoryStrategies shape
        added = await client.add_strategy(
            memory_resource,
            {
                "semanticMemoryStrategy": {
                    "name": f"integ_{uuid4().hex[:8]}",
                    "description": "integration test strategy",
                },
            },
        )
        strategy_id = added["strategy_id"]
        assert strategy_id  # Should be non-empty

        # List strategies
        listed = await client.list_strategies(memory_resource)
        assert "strategies" in listed
        ids = [s["strategy_id"] for s in listed["strategies"]]
        assert strategy_id in ids

        # Delete strategy
        await client.delete_strategy(memory_resource, strategy_id)
