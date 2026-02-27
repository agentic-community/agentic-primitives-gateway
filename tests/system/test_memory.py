"""System tests for the AgentCore memory primitive.

Full stack: AgenticPlatformClient → ASGI → middleware → route → registry →
AgentCoreMemoryProvider → (mocked) MemorySessionManager.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentic_primitives_gateway_client import AgenticPlatformClient, AgenticPlatformError

# ── Helpers ───────────────────────────────────────────────────────────


def _set_memory_creds(client: AgenticPlatformClient) -> None:
    """Attach the agentcore memory-id header the provider expects."""
    client.set_service_credentials("agentcore", {"memory_id": "test-mem-123"})


def _mock_session(manager: MagicMock) -> MagicMock:
    """Wire ``manager -> create_memory_session -> mock session``."""
    session = MagicMock()
    manager.create_memory_session.return_value = session
    return session


# ── Key-value memory ──────────────────────────────────────────────────


class TestStoreMemory:
    async def test_store_memory(self, client: AgenticPlatformClient, mock_memory_manager: MagicMock) -> None:
        _set_memory_creds(client)
        session = _mock_session(mock_memory_manager)

        record = await client.store_memory("ns1", "k1", "hello world", {"tag": "v1"})

        assert record["namespace"] == "ns1"
        assert record["key"] == "k1"
        assert record["content"] == "hello world"
        assert record["metadata"]["tag"] == "v1"
        session.add_turns.assert_called_once()


class TestRetrieveMemory:
    async def test_retrieve_existing(self, client: AgenticPlatformClient, mock_memory_manager: MagicMock) -> None:
        _set_memory_creds(client)
        session = _mock_session(mock_memory_manager)
        session.get_last_k_turns.return_value = []
        mock_memory_manager.search_long_term_memories.return_value = [
            {
                "id": "rec-1",
                "memory": "hello world",
                "score": 0.95,
                "metadata": {"_agentic_key": "k1"},
            }
        ]

        record = await client.retrieve_memory("ns1", "k1")

        assert record["content"] == "hello world"
        assert record["key"] == "k1"

    async def test_retrieve_not_found(self, client: AgenticPlatformClient, mock_memory_manager: MagicMock) -> None:
        _set_memory_creds(client)
        session = _mock_session(mock_memory_manager)
        session.get_last_k_turns.return_value = []
        mock_memory_manager.search_long_term_memories.return_value = []

        with pytest.raises(AgenticPlatformError) as exc_info:
            await client.retrieve_memory("ns1", "missing")
        assert exc_info.value.status_code == 404


class TestListMemories:
    async def test_list_memories(self, client: AgenticPlatformClient, mock_memory_manager: MagicMock) -> None:
        _set_memory_creds(client)
        session = _mock_session(mock_memory_manager)
        session.list_long_term_memory_records.return_value = [
            {
                "id": "rec-1",
                "memory": "content-1",
                "metadata": {"_agentic_key": "k1"},
            },
        ]
        session.get_last_k_turns.return_value = []

        result = await client.list_memories("ns1", limit=10)

        assert "records" in result
        assert len(result["records"]) >= 1


class TestSearchMemory:
    async def test_search_memory(self, client: AgenticPlatformClient, mock_memory_manager: MagicMock) -> None:
        _set_memory_creds(client)
        session = _mock_session(mock_memory_manager)
        session.get_last_k_turns.return_value = []
        mock_memory_manager.search_long_term_memories.return_value = [
            {
                "id": "rec-1",
                "memory": "matched content",
                "score": 0.9,
                "metadata": {"_agentic_key": "k1"},
            },
        ]

        result = await client.search_memory("ns1", "query", top_k=5)

        assert "results" in result
        assert len(result["results"]) == 1
        assert result["results"][0]["score"] == 0.9


class TestDeleteMemory:
    async def test_delete_memory(self, client: AgenticPlatformClient, mock_memory_manager: MagicMock) -> None:
        _set_memory_creds(client)
        session = _mock_session(mock_memory_manager)
        mock_memory_manager.search_long_term_memories.return_value = [
            {
                "id": "rec-1",
                "memory": "content",
                "metadata": {"_agentic_key": "k1"},
            },
        ]
        # delete_memory_record must succeed
        session.delete_memory_record.return_value = None

        # Client returns None on 204
        await client.delete_memory("ns1", "k1")

    async def test_delete_not_found(self, client: AgenticPlatformClient, mock_memory_manager: MagicMock) -> None:
        _set_memory_creds(client)
        _mock_session(mock_memory_manager)
        mock_memory_manager.search_long_term_memories.return_value = []

        with pytest.raises(AgenticPlatformError) as exc_info:
            await client.delete_memory("ns1", "missing")
        assert exc_info.value.status_code == 404


# ── Conversation events ───────────────────────────────────────────────


class TestCreateEvent:
    async def test_create_event(self, client: AgenticPlatformClient, mock_memory_manager: MagicMock) -> None:
        _set_memory_creds(client)
        session = _mock_session(mock_memory_manager)
        session.add_turns.return_value = {
            "event_id": "evt-1",
            "actor_id": "actor-1",
            "session_id": "sess-1",
            "messages": [{"text": "hi", "role": "USER"}],
            "metadata": {},
        }

        result = await client.create_event("actor-1", "sess-1", [{"text": "hi", "role": "USER"}])

        assert result["actor_id"] == "actor-1"
        assert result["session_id"] == "sess-1"


class TestListEvents:
    async def test_list_events(self, client: AgenticPlatformClient, mock_memory_manager: MagicMock) -> None:
        _set_memory_creds(client)
        mock_memory_manager.list_events.return_value = [
            {
                "event_id": "evt-1",
                "actor_id": "actor-1",
                "session_id": "sess-1",
                "messages": [{"text": "hi", "role": "USER"}],
                "metadata": {},
            },
        ]

        result = await client.list_events("actor-1", "sess-1")

        assert "events" in result
        assert len(result["events"]) == 1


class TestGetEvent:
    async def test_get_event(self, client: AgenticPlatformClient, mock_memory_manager: MagicMock) -> None:
        _set_memory_creds(client)
        mock_memory_manager.get_event.return_value = {
            "event_id": "evt-1",
            "actor_id": "actor-1",
            "session_id": "sess-1",
            "messages": [{"text": "hi", "role": "USER"}],
            "metadata": {},
        }

        result = await client.get_event("actor-1", "sess-1", "evt-1")

        assert result["event_id"] == "evt-1"


class TestDeleteEvent:
    async def test_delete_event(self, client: AgenticPlatformClient, mock_memory_manager: MagicMock) -> None:
        _set_memory_creds(client)
        mock_memory_manager.delete_event.return_value = None

        await client.delete_event("actor-1", "sess-1", "evt-1")


class TestGetLastTurns:
    async def test_get_last_turns(self, client: AgenticPlatformClient, mock_memory_manager: MagicMock) -> None:
        _set_memory_creds(client)
        session = _mock_session(mock_memory_manager)
        session.get_last_k_turns.return_value = [
            [{"content": {"text": "hi"}, "role": "USER"}],
        ]

        result = await client.get_last_turns("actor-1", "sess-1", k=3)

        assert "turns" in result
        assert len(result["turns"]) == 1


# ── Session management ────────────────────────────────────────────────


class TestListActors:
    async def test_list_actors(self, client: AgenticPlatformClient, mock_memory_manager: MagicMock) -> None:
        _set_memory_creds(client)
        mock_memory_manager.list_actors.return_value = [
            {"actor_id": "actor-1"},
            {"actor_id": "actor-2"},
        ]

        result = await client.list_actors()

        assert "actors" in result
        assert len(result["actors"]) == 2


class TestListSessions:
    async def test_list_memory_sessions(self, client: AgenticPlatformClient, mock_memory_manager: MagicMock) -> None:
        _set_memory_creds(client)
        mock_memory_manager.list_actor_sessions.return_value = [
            {"session_id": "sess-1", "actor_id": "actor-1"},
        ]

        result = await client.list_memory_sessions("actor-1")

        assert "sessions" in result
        assert len(result["sessions"]) == 1


# ── Branch management ─────────────────────────────────────────────────


class TestForkConversation:
    async def test_fork_conversation(self, client: AgenticPlatformClient, mock_memory_manager: MagicMock) -> None:
        _set_memory_creds(client)
        mock_memory_manager.fork_conversation.return_value = {
            "name": "branch-1",
            "root_event_id": "evt-0",
        }

        result = await client.fork_conversation(
            "actor-1",
            "sess-1",
            "evt-0",
            "branch-1",
            [{"text": "branched msg", "role": "USER"}],
        )

        assert result["name"] == "branch-1"


class TestListBranches:
    async def test_list_branches(self, client: AgenticPlatformClient, mock_memory_manager: MagicMock) -> None:
        _set_memory_creds(client)
        mock_memory_manager.list_branches.return_value = [
            {"name": "branch-1"},
        ]

        result = await client.list_branches("actor-1", "sess-1")

        assert "branches" in result
        assert len(result["branches"]) == 1


# ── Control plane — memory resources ──────────────────────────────────


class TestCreateMemoryResource:
    async def test_create_memory_resource(
        self, client: AgenticPlatformClient, mock_memory_control_plane: MagicMock
    ) -> None:
        mock_memory_control_plane.create_memory.return_value = {
            "memory": {
                "id": "mem-new",
                "name": "test-resource",
                "status": "CREATING",
                "arn": "arn:aws:bedrock:us-east-1:123:memory/mem-new",
            }
        }

        result = await client.create_memory_resource("test-resource")

        assert result["name"] == "test-resource"


class TestGetMemoryResource:
    async def test_get_memory_resource(
        self, client: AgenticPlatformClient, mock_memory_control_plane: MagicMock
    ) -> None:
        mock_memory_control_plane.get_memory.return_value = {
            "memory": {
                "id": "mem-1",
                "name": "my-mem",
                "status": "ACTIVE",
                "arn": "arn:aws:bedrock:us-east-1:123:memory/mem-1",
            }
        }

        result = await client.get_memory_resource("mem-1")

        assert result["memory_id"] == "mem-1"


class TestListMemoryResources:
    async def test_list_memory_resources(
        self, client: AgenticPlatformClient, mock_memory_control_plane: MagicMock
    ) -> None:
        mock_memory_control_plane.list_memories.return_value = {
            "memories": [
                {"id": "mem-1", "name": "m1", "status": "ACTIVE", "arn": "arn:1"},
                {"id": "mem-2", "name": "m2", "status": "ACTIVE", "arn": "arn:2"},
            ]
        }

        result = await client.list_memory_resources()

        assert "resources" in result
        assert len(result["resources"]) == 2


class TestDeleteMemoryResource:
    async def test_delete_memory_resource(
        self, client: AgenticPlatformClient, mock_memory_control_plane: MagicMock
    ) -> None:
        mock_memory_control_plane.delete_memory.return_value = None

        await client.delete_memory_resource("mem-1")


# ── Strategy management ───────────────────────────────────────────────


class TestListStrategies:
    async def test_list_strategies(self, client: AgenticPlatformClient, mock_memory_control_plane: MagicMock) -> None:
        mock_memory_control_plane.get_memory.return_value = {
            "memory": {
                "id": "mem-1",
                "strategies": [
                    {"strategyId": "s1", "name": "semantic", "description": "semantic search"},
                ],
            }
        }

        result = await client.list_strategies("mem-1")

        assert "strategies" in result
        assert len(result["strategies"]) == 1


class TestAddStrategy:
    async def test_add_strategy(self, client: AgenticPlatformClient, mock_memory_control_plane: MagicMock) -> None:
        mock_memory_control_plane.update_memory.return_value = {
            "memory": {
                "id": "mem-1",
                "strategies": [
                    {"strategyId": "s-new", "name": "semantic"},
                ],
            }
        }

        result = await client.add_strategy("mem-1", {"type": "semantic"})

        assert result["strategy_id"] == "s-new"


class TestDeleteStrategy:
    async def test_delete_strategy(self, client: AgenticPlatformClient, mock_memory_control_plane: MagicMock) -> None:
        mock_memory_control_plane.update_memory.return_value = {"memory": {"id": "mem-1", "strategies": []}}

        await client.delete_strategy("mem-1", "s1")
