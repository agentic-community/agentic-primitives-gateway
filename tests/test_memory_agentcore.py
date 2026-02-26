from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentic_primitives_gateway.primitives.memory.agentcore import AgentCoreMemoryProvider


@patch("agentic_primitives_gateway.primitives.memory.agentcore.get_boto3_session")
@patch("agentic_primitives_gateway.primitives.memory.agentcore.get_service_credentials")
class TestAgentCoreMemoryProvider:
    """Tests for the AgentCore memory provider."""

    def _make_provider(self, **kwargs):
        return AgentCoreMemoryProvider(**kwargs)

    @pytest.mark.asyncio
    async def test_store(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = {"memory_id": "mem-123"}
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        mock_mem_session = MagicMock()

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.create_memory_session.return_value = mock_mem_session
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider()
            result = await provider.store(
                namespace="test-ns",
                key="key-1",
                content="hello world",
                metadata={"tag": "test"},
            )

        assert result.namespace == "test-ns"
        assert result.key == "key-1"
        assert result.content == "hello world"
        mock_mem_session.add_turns.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_uses_config_memory_id(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = None
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.create_memory_session.return_value = MagicMock()
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider(memory_id="config-mem-id")
            await provider.store(namespace="ns", key="k", content="c")

            mock_mgr_cls.assert_called_once_with(
                memory_id="config-mem-id",
                region_name="us-east-1",
                boto3_session=mock_session,
            )

    @pytest.mark.asyncio
    async def test_no_memory_id_raises(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = None
        mock_get_session.return_value = MagicMock(region_name="us-east-1")

        provider = self._make_provider()
        with pytest.raises(ValueError, match="memory_id is required"):
            await provider.store(namespace="ns", key="k", content="c")

    @pytest.mark.asyncio
    async def test_retrieve_found(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = {"memory_id": "mem-1"}
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.search_long_term_memories.return_value = [
                {"id": "rec-1", "memory": "found content", "metadata": {"_agentic_key": "my-key"}, "score": 0.9},
            ]
            mock_mgr.create_memory_session.return_value = MagicMock(get_last_k_turns=MagicMock(return_value=[]))
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider()
            result = await provider.retrieve(namespace="ns", key="my-key")

        assert result is not None
        assert result.key == "my-key"
        assert result.content == "found content"

    @pytest.mark.asyncio
    async def test_retrieve_not_found(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = {"memory_id": "mem-1"}
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.search_long_term_memories.return_value = []
            mock_mgr.create_memory_session.return_value = MagicMock(get_last_k_turns=MagicMock(return_value=[]))
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider()
            result = await provider.retrieve(namespace="ns", key="nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_search_combines_lt_and_st(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = {"memory_id": "mem-1"}
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        msg = {"content": {"text": "hello matching query"}}

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.search_long_term_memories.return_value = [
                {"id": "lt-1", "memory": "long term result", "metadata": {}, "score": 0.8},
            ]
            mock_mem_session = MagicMock()
            mock_mem_session.get_last_k_turns.return_value = [[msg]]
            mock_mgr.create_memory_session.return_value = mock_mem_session
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider()
            results = await provider.search(namespace="ns", query="hello")

        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_search_deduplicates_by_content(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = {"memory_id": "mem-1"}
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.search_long_term_memories.return_value = [
                {"id": "1", "memory": "same content", "metadata": {}, "score": 0.9},
                {"id": "2", "memory": "same content", "metadata": {}, "score": 0.7},
            ]
            mock_mgr.create_memory_session.return_value = MagicMock(get_last_k_turns=MagicMock(return_value=[]))
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider()
            results = await provider.search(namespace="ns", query="content")

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_delete_success(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = {"memory_id": "mem-1"}
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.search_long_term_memories.return_value = [
                {"id": "rec-1", "memory": "to delete", "metadata": {"_agentic_key": "my-key"}},
            ]
            mock_mem_session = MagicMock()
            mock_mgr.create_memory_session.return_value = mock_mem_session
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider()
            result = await provider.delete(namespace="ns", key="my-key")

        assert result is True
        mock_mem_session.delete_memory_record.assert_called_once_with("rec-1")

    @pytest.mark.asyncio
    async def test_delete_not_found(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = {"memory_id": "mem-1"}
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.search_long_term_memories.return_value = []
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider()
            result = await provider.delete(namespace="ns", key="nonexistent")

        assert result is False

    @pytest.mark.asyncio
    async def test_delete_handles_exception(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = {"memory_id": "mem-1"}
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.search_long_term_memories.side_effect = RuntimeError("AWS error")
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider()
            result = await provider.delete(namespace="ns", key="k")

        assert result is False

    @pytest.mark.asyncio
    async def test_list_memories(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = {"memory_id": "mem-1"}
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mem_session = MagicMock()
            mock_mem_session.list_long_term_memory_records.return_value = [
                {"id": "1", "memory": "mem-1", "metadata": {}},
            ]
            mock_mem_session.get_last_k_turns.return_value = [
                [{"content": "turn-1"}],
            ]
            mock_mgr.create_memory_session.return_value = mock_mem_session
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider()
            records = await provider.list_memories(namespace="ns")

        assert len(records) >= 1

    @pytest.mark.asyncio
    async def test_list_memories_with_filters(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = {"memory_id": "mem-1"}
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mem_session = MagicMock()
            mock_mem_session.list_long_term_memory_records.return_value = [
                {"id": "1", "memory": "correct", "metadata": {"category": "A"}},
                {"id": "2", "memory": "filtered", "metadata": {"category": "B"}},
            ]
            mock_mem_session.get_last_k_turns.return_value = []
            mock_mgr.create_memory_session.return_value = mock_mem_session
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider()
            records = await provider.list_memories(namespace="ns", filters={"category": "A"})

        assert len(records) == 1
        assert records[0].content == "correct"

    @pytest.mark.asyncio
    async def test_healthcheck(self, mock_get_svc_creds, mock_get_session):
        provider = self._make_provider()
        assert await provider.healthcheck() is True

    def test_stable_session_id(self, mock_get_svc_creds, mock_get_session):
        sid1 = AgentCoreMemoryProvider._stable_session_id("test-ns")
        sid2 = AgentCoreMemoryProvider._stable_session_id("test-ns")
        sid3 = AgentCoreMemoryProvider._stable_session_id("other-ns")
        assert sid1 == sid2
        assert sid1 != sid3
        assert len(sid1) == 32

    # ── Conversation memory tests ────────────────────────────────────

    @pytest.mark.asyncio
    async def test_create_event(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = {"memory_id": "mem-123"}
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        mock_mem_session = MagicMock()
        mock_mem_session.add_turns.return_value = {
            "event_id": "evt-1",
            "actor_id": "actor-1",
            "session_id": "sess-1",
        }

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.create_memory_session.return_value = mock_mem_session
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider()
            result = await provider.create_event(
                actor_id="actor-1",
                session_id="sess-1",
                messages=[("Hello", "USER")],
            )

        assert result["event_id"] == "evt-1"
        mock_mem_session.add_turns.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_events(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = {"memory_id": "mem-123"}
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.list_events.return_value = [
                {"event_id": "e1", "actor_id": "a1", "session_id": "s1"},
                {"event_id": "e2", "actor_id": "a1", "session_id": "s1"},
            ]
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider()
            result = await provider.list_events(actor_id="a1", session_id="s1")

        assert len(result) == 2
        assert result[0]["event_id"] == "e1"

    @pytest.mark.asyncio
    async def test_get_event(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = {"memory_id": "mem-123"}
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.get_event.return_value = {
                "event_id": "evt-1",
                "actor_id": "a1",
                "session_id": "s1",
            }
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider()
            result = await provider.get_event(actor_id="a1", session_id="s1", event_id="evt-1")

        assert result["event_id"] == "evt-1"

    @pytest.mark.asyncio
    async def test_delete_event(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = {"memory_id": "mem-123"}
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider()
            await provider.delete_event(actor_id="a1", session_id="s1", event_id="evt-1")

        mock_mgr.delete_event.assert_called_once_with(actor_id="a1", session_id="s1", event_id="evt-1")

    @pytest.mark.asyncio
    async def test_get_last_turns(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = {"memory_id": "mem-123"}
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        mock_mem_session = MagicMock()
        mock_mem_session.get_last_k_turns.return_value = [
            [{"content": {"text": "Hello"}, "role": "USER"}],
        ]

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.create_memory_session.return_value = mock_mem_session
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider()
            result = await provider.get_last_turns(actor_id="a1", session_id="s1", k=3)

        assert len(result) == 1
        assert result[0][0]["text"] == "Hello"

    # ── Session management tests ─────────────────────────────────────

    @pytest.mark.asyncio
    async def test_list_actors(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = {"memory_id": "mem-123"}
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.list_actors.return_value = [
                {"actor_id": "a1"},
                {"actor_id": "a2"},
            ]
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider()
            result = await provider.list_actors()

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_list_sessions(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = {"memory_id": "mem-123"}
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.list_actor_sessions.return_value = [
                {"session_id": "s1", "actor_id": "a1"},
            ]
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider()
            result = await provider.list_sessions(actor_id="a1")

        assert len(result) == 1
        assert result[0]["session_id"] == "s1"

    # ── Branch management tests ──────────────────────────────────────

    @pytest.mark.asyncio
    async def test_fork_conversation(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = {"memory_id": "mem-123"}
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.fork_conversation.return_value = {
                "name": "branch-1",
                "root_event_id": "evt-1",
            }
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider()
            result = await provider.fork_conversation(
                actor_id="a1",
                session_id="s1",
                root_event_id="evt-1",
                branch_name="branch-1",
                messages=[("Hello", "USER")],
            )

        assert result["name"] == "branch-1"

    @pytest.mark.asyncio
    async def test_list_branches(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = {"memory_id": "mem-123"}
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.list_branches.return_value = [
                {"name": "main", "root_event_id": "evt-0", "event_count": 5},
            ]
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider()
            result = await provider.list_branches(actor_id="a1", session_id="s1")

        assert len(result) == 1
        assert result[0]["name"] == "main"

    # ── Control plane tests ──────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_create_memory_resource(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = {"memory_id": "mem-123"}
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.create_memory.return_value = {
                "memory_id": "mem-new",
                "name": "test-mem",
            }
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider()
            result = await provider.create_memory_resource(name="test-mem")

        assert result["memory_id"] == "mem-new"

    @pytest.mark.asyncio
    async def test_get_memory_resource(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = {"memory_id": "mem-123"}
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.get_memory.return_value = {
                "memory_id": "mem-1",
                "name": "test-mem",
                "status": "ACTIVE",
            }
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider()
            result = await provider.get_memory_resource(memory_id="mem-1")

        assert result["memory_id"] == "mem-1"
        assert result["status"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_list_memory_resources(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = {"memory_id": "mem-123"}
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.list_memories.return_value = [
                {"memory_id": "mem-1", "name": "test-1"},
            ]
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider()
            result = await provider.list_memory_resources()

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_delete_memory_resource(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = {"memory_id": "mem-123"}
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider()
            await provider.delete_memory_resource(memory_id="mem-1")

        mock_mgr.delete_memory.assert_called_once_with(memory_id="mem-1")

    # ── Strategy management tests ────────────────────────────────────

    @pytest.mark.asyncio
    async def test_list_strategies(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = {"memory_id": "mem-123"}
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.list_strategies.return_value = [
                {"strategy_id": "s1", "type": "semantic"},
            ]
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider()
            result = await provider.list_strategies(memory_id="mem-1")

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_add_strategy(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = {"memory_id": "mem-123"}
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.add_strategy.return_value = {"strategy_id": "s-new"}
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider()
            result = await provider.add_strategy(
                memory_id="mem-1",
                strategy={"type": "semantic"},
            )

        assert result["strategy_id"] == "s-new"

    @pytest.mark.asyncio
    async def test_delete_strategy(self, mock_get_svc_creds, mock_get_session):
        mock_get_svc_creds.return_value = {"memory_id": "mem-123"}
        mock_session = MagicMock(region_name="us-east-1")
        mock_get_session.return_value = mock_session

        with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr_cls.return_value = mock_mgr

            provider = self._make_provider()
            await provider.delete_strategy(memory_id="mem-1", strategy_id="s1")

        mock_mgr.delete_strategy.assert_called_once_with(memory_id="mem-1", strategy_id="s1")
