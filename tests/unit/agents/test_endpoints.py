"""Tests for new agent endpoints: tools, memory, tool-catalog, streaming."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from agentic_primitives_gateway.agents.store import FileAgentStore
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.routes import agents as agents_module
from agentic_primitives_gateway.routes._background import BackgroundRunManager
from agentic_primitives_gateway.routes.agents import set_agent_bg, set_agent_store

SAMPLE_AGENT = {
    "name": "test-agent",
    "model": "test-model",
    "primitives": {
        "memory": {"enabled": True, "tools": None, "namespace": "agent:{agent_name}"},
    },
}


@pytest.fixture(autouse=True)
def _setup(tmp_path: Any) -> None:
    store = FileAgentStore(path=str(tmp_path / "agents.json"))
    set_agent_store(store)
    # Reset bg manager to a fresh one for each test
    set_agent_bg(BackgroundRunManager(stale_seconds=600))


class TestToolCatalogEndpoint:
    def test_returns_all_primitives(self) -> None:
        client = TestClient(app)
        resp = client.get("/api/v1/agents/tool-catalog")
        assert resp.status_code == 200
        data = resp.json()
        assert "primitives" in data
        prims = data["primitives"]
        assert "memory" in prims
        assert "code_interpreter" in prims
        assert "browser" in prims
        assert "agents" in prims

    def test_memory_tools_listed(self) -> None:
        client = TestClient(app)
        resp = client.get("/api/v1/agents/tool-catalog")
        tools = resp.json()["primitives"]["memory"]
        names = [t["name"] for t in tools]
        assert "remember" in names
        assert "recall" in names
        assert "search_memory" in names


class TestAgentToolsEndpoint:
    def test_returns_tools_for_agent(self) -> None:
        client = TestClient(app)
        client.post("/api/v1/agents", json=SAMPLE_AGENT)
        resp = client.get("/api/v1/agents/test-agent/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_name"] == "test-agent"
        assert len(data["tools"]) > 0
        tool_names = [t["name"] for t in data["tools"]]
        assert "remember" in tool_names

    def test_404_for_unknown_agent(self) -> None:
        client = TestClient(app)
        resp = client.get("/api/v1/agents/nonexistent/tools")
        assert resp.status_code == 404

    def test_tools_include_primitive_and_provider(self) -> None:
        client = TestClient(app)
        client.post("/api/v1/agents", json=SAMPLE_AGENT)
        resp = client.get("/api/v1/agents/test-agent/tools")
        for tool in resp.json()["tools"]:
            assert "primitive" in tool
            assert "provider" in tool


class TestAgentMemoryEndpoint:
    def test_memory_disabled(self) -> None:
        client = TestClient(app)
        agent = {**SAMPLE_AGENT, "primitives": {}}
        client.post("/api/v1/agents", json=agent)
        resp = client.get("/api/v1/agents/test-agent/memory")
        assert resp.status_code == 200
        data = resp.json()
        assert data["memory_enabled"] is False

    def test_memory_enabled_empty(self) -> None:
        client = TestClient(app)
        client.post("/api/v1/agents", json=SAMPLE_AGENT)
        resp = client.get("/api/v1/agents/test-agent/memory")
        assert resp.status_code == 200
        data = resp.json()
        assert data["memory_enabled"] is True
        assert data["namespace"].startswith("agent:test-agent:u:")

    def test_404_for_unknown_agent(self) -> None:
        client = TestClient(app)
        resp = client.get("/api/v1/agents/nonexistent/memory")
        assert resp.status_code == 404


class TestSessionHistoryEndpoint:
    def test_returns_empty_history(self) -> None:
        client = TestClient(app)
        client.post("/api/v1/agents", json=SAMPLE_AGENT)
        resp = client.get("/api/v1/agents/test-agent/sessions/some-session-id")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_name"] == "test-agent"
        assert data["session_id"] == "some-session-id"
        assert data["messages"] == []

    def test_404_for_unknown_agent(self) -> None:
        client = TestClient(app)
        resp = client.get("/api/v1/agents/nonexistent/sessions/abc")
        assert resp.status_code == 404


class TestStreamingEndpoint:
    def test_returns_sse_response(self) -> None:
        """Noop LLM returns empty content, but SSE envelope should work."""
        client = TestClient(app)
        client.post("/api/v1/agents", json=SAMPLE_AGENT)

        resp = client.post(
            "/api/v1/agents/test-agent/chat/stream",
            json={"message": "hello"},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        # Should contain at least stream_start and done events
        body = resp.text
        assert "stream_start" in body
        assert '"type": "done"' in body or '"type":"done"' in body

    def test_404_for_unknown_agent(self) -> None:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/agents/nonexistent/chat/stream",
            json={"message": "hello"},
        )
        assert resp.status_code == 404


class TestSetAgentBg:
    def test_set_agent_bg_replaces_manager(self) -> None:
        new_bg = BackgroundRunManager(stale_seconds=300)
        set_agent_bg(new_bg)
        assert agents_module._bg is new_bg
        assert agents_module._active_runs is new_bg.runs


class TestSetAgentStore:
    def test_set_agent_store_updates_runner(self, tmp_path: Any) -> None:
        store = FileAgentStore(path=str(tmp_path / "agents2.json"))
        set_agent_store(store)
        assert agents_module._store is store


class TestGetStoreNotInitialized:
    def test_raises_when_store_is_none(self) -> None:
        agents_module._store = None
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/agents")
        assert resp.status_code == 500


class TestAgentToolsWithOverrides:
    def test_tools_with_provider_overrides(self) -> None:
        client = TestClient(app)
        agent = {
            **SAMPLE_AGENT,
            "provider_overrides": {"memory": "default"},
        }
        client.post("/api/v1/agents", json=agent)
        resp = client.get("/api/v1/agents/test-agent/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_name"] == "test-agent"

    def test_tools_agent_primitive_shows_agent_delegation(self) -> None:
        """Agent-as-tool delegation tools show provider as 'agent_delegation'."""
        client = TestClient(app)
        # Create target agent first
        client.post(
            "/api/v1/agents",
            json={"name": "helper", "model": "m"},
        )
        agent_with_delegation = {
            "name": "coordinator",
            "model": "test-model",
            "primitives": {
                "agents": {"enabled": True, "tools": ["helper"]},
            },
        }
        client.post("/api/v1/agents", json=agent_with_delegation)
        resp = client.get("/api/v1/agents/coordinator/tools")
        assert resp.status_code == 200
        tools = resp.json()["tools"]
        agent_tools = [t for t in tools if t["primitive"] == "agents"]
        for t in agent_tools:
            assert t["provider"] == "agent_delegation"

    def test_tools_unknown_primitive_shows_unknown_provider(self) -> None:
        """When registry.get_primitive raises, provider should be 'unknown'."""
        client = TestClient(app)
        client.post("/api/v1/agents", json=SAMPLE_AGENT)

        with patch("agentic_primitives_gateway.routes.agents.registry") as mock_reg:
            mock_reg.get_primitive.side_effect = Exception("no such primitive")
            with patch("agentic_primitives_gateway.routes.agents.build_tool_list") as mock_build:
                mock_tool = MagicMock()
                mock_tool.name = "some_tool"
                mock_tool.description = "desc"
                mock_tool.primitive = "nonexistent"
                mock_build.return_value = [mock_tool]
                resp = client.get("/api/v1/agents/test-agent/tools")

        assert resp.status_code == 200
        tool_info = resp.json()["tools"][0]
        assert tool_info["provider"] == "unknown"


class TestAgentMemoryWithData:
    def test_memory_with_provider_overrides(self) -> None:
        client = TestClient(app)
        agent = {
            **SAMPLE_AGENT,
            "provider_overrides": {"memory": "default"},
        }
        client.post("/api/v1/agents", json=agent)
        resp = client.get("/api/v1/agents/test-agent/memory")
        assert resp.status_code == 200
        data = resp.json()
        assert data["memory_enabled"] is True

    def test_memory_introspection_exception_handled(self) -> None:
        """When memory provider raises, endpoint still returns gracefully."""
        client = TestClient(app)
        client.post("/api/v1/agents", json=SAMPLE_AGENT)

        with patch("agentic_primitives_gateway.routes.agents.registry") as mock_reg:
            mock_reg.memory.list_namespaces = AsyncMock(side_effect=RuntimeError("fail"))
            resp = client.get("/api/v1/agents/test-agent/memory")

        assert resp.status_code == 200
        data = resp.json()
        assert data["memory_enabled"] is True
        assert data["stores"] == []

    def test_memory_with_matching_namespaces(self) -> None:
        """When list_namespaces returns matching namespaces, they are included."""
        client = TestClient(app)
        client.post("/api/v1/agents", json=SAMPLE_AGENT)

        mock_record = MagicMock()
        mock_record.key = "k1"
        mock_record.content = "some content"
        mock_record.updated_at = MagicMock()
        mock_record.updated_at.isoformat.return_value = "2025-01-01T00:00:00"

        with patch("agentic_primitives_gateway.routes.agents.registry") as mock_reg:
            # Return a namespace that matches the agent
            mock_reg.memory.list_namespaces = AsyncMock(return_value=["agent:test-agent:u:noop-admin"])
            mock_reg.memory.list_memories = AsyncMock(return_value=[mock_record])
            resp = client.get("/api/v1/agents/test-agent/memory")

        assert resp.status_code == 200
        data = resp.json()
        assert data["memory_enabled"] is True
        assert len(data["stores"]) >= 1

    def test_memory_fallback_direct_list(self) -> None:
        """When resolved namespace not in list_namespaces, fallback list_memories is tried."""
        client = TestClient(app)
        client.post("/api/v1/agents", json=SAMPLE_AGENT)

        mock_record = MagicMock()
        mock_record.key = "k1"
        mock_record.content = "fallback content"
        mock_record.updated_at = MagicMock()
        mock_record.updated_at.isoformat.return_value = "2025-01-01T00:00:00"

        with patch("agentic_primitives_gateway.routes.agents.registry") as mock_reg:
            # Return empty namespaces so fallback path is taken
            mock_reg.memory.list_namespaces = AsyncMock(return_value=[])
            mock_reg.memory.list_memories = AsyncMock(return_value=[mock_record])
            resp = client.get("/api/v1/agents/test-agent/memory")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["stores"]) == 1
        assert data["stores"][0]["memory_count"] == 1


class TestSessionListEndpoint:
    def test_list_sessions(self) -> None:
        client = TestClient(app)
        client.post("/api/v1/agents", json=SAMPLE_AGENT)
        resp = client.get("/api/v1/agents/test-agent/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_name"] == "test-agent"
        assert isinstance(data["sessions"], list)

    def test_list_sessions_404(self) -> None:
        client = TestClient(app)
        resp = client.get("/api/v1/agents/nonexistent/sessions")
        assert resp.status_code == 404

    def test_list_sessions_with_provider_overrides(self) -> None:
        client = TestClient(app)
        agent = {**SAMPLE_AGENT, "provider_overrides": {"memory": "default"}}
        client.post("/api/v1/agents", json=agent)
        resp = client.get("/api/v1/agents/test-agent/sessions")
        assert resp.status_code == 200

    def test_list_sessions_provider_error_handled(self) -> None:
        client = TestClient(app)
        client.post("/api/v1/agents", json=SAMPLE_AGENT)
        with patch("agentic_primitives_gateway.routes.agents.registry") as mock_reg:
            mock_reg.memory.list_sessions = AsyncMock(side_effect=RuntimeError("fail"))
            resp = client.get("/api/v1/agents/test-agent/sessions")
        assert resp.status_code == 200
        assert resp.json()["sessions"] == []


class TestSessionCleanupEndpoint:
    def test_cleanup_sessions(self) -> None:
        client = TestClient(app)
        client.post("/api/v1/agents", json=SAMPLE_AGENT)
        resp = client.post("/api/v1/agents/test-agent/sessions/cleanup?keep=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "deleted" in data
        assert "kept" in data

    def test_cleanup_sessions_404(self) -> None:
        client = TestClient(app)
        resp = client.post("/api/v1/agents/nonexistent/sessions/cleanup")
        assert resp.status_code == 404

    def test_cleanup_sessions_with_sessions_to_delete(self) -> None:
        client = TestClient(app)
        client.post("/api/v1/agents", json=SAMPLE_AGENT)
        mock_sessions = [{"session_id": f"s{i}", "last_activity": f"2025-01-0{i}T00:00:00"} for i in range(1, 8)]
        with patch("agentic_primitives_gateway.routes.agents.registry") as mock_reg:
            mock_reg.memory.list_sessions = AsyncMock(return_value=mock_sessions)
            mock_reg.memory.delete_session = AsyncMock()
            resp = client.post("/api/v1/agents/test-agent/sessions/cleanup?keep=3")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] == 4
        assert data["kept"] == 3

    def test_cleanup_sessions_provider_error_handled(self) -> None:
        client = TestClient(app)
        client.post("/api/v1/agents", json=SAMPLE_AGENT)
        with patch("agentic_primitives_gateway.routes.agents.registry") as mock_reg:
            mock_reg.memory.list_sessions = AsyncMock(side_effect=RuntimeError("fail"))
            resp = client.post("/api/v1/agents/test-agent/sessions/cleanup")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 0


class TestDeleteSessionEndpoint:
    def test_delete_session(self) -> None:
        client = TestClient(app)
        client.post("/api/v1/agents", json=SAMPLE_AGENT)
        resp = client.delete("/api/v1/agents/test-agent/sessions/some-session")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_delete_session_404(self) -> None:
        client = TestClient(app)
        resp = client.delete("/api/v1/agents/nonexistent/sessions/some-session")
        assert resp.status_code == 404

    def test_delete_session_with_overrides(self) -> None:
        client = TestClient(app)
        agent = {**SAMPLE_AGENT, "provider_overrides": {"memory": "default"}}
        client.post("/api/v1/agents", json=agent)
        resp = client.delete("/api/v1/agents/test-agent/sessions/some-session")
        assert resp.status_code == 200

    def test_delete_session_provider_error_handled(self) -> None:
        client = TestClient(app)
        client.post("/api/v1/agents", json=SAMPLE_AGENT)
        with patch("agentic_primitives_gateway.routes.agents.registry") as mock_reg:
            mock_reg.memory.delete_session = AsyncMock(side_effect=RuntimeError("fail"))
            resp = client.delete("/api/v1/agents/test-agent/sessions/sid1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"


class TestSessionStreamEndpoint:
    def test_stream_session_events(self) -> None:
        client = TestClient(app)
        client.post("/api/v1/agents", json=SAMPLE_AGENT)
        # Mock bg manager to return a done event so the generator exits immediately
        with (
            patch.object(
                agents_module._bg, "get_events_async", new=AsyncMock(return_value=[{"type": "done", "response": "ok"}])
            ),
            patch.object(agents_module._bg, "get_status_async", new=AsyncMock(return_value="idle")),
        ):
            resp = client.get("/api/v1/agents/test-agent/sessions/sid1/stream")
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

    def test_stream_session_events_404(self) -> None:
        client = TestClient(app)
        resp = client.get("/api/v1/agents/nonexistent/sessions/sid1/stream")
        assert resp.status_code == 404


class TestSessionStatusEndpoint:
    def test_session_status_idle(self) -> None:
        client = TestClient(app)
        client.post("/api/v1/agents", json=SAMPLE_AGENT)
        resp = client.get("/api/v1/agents/test-agent/sessions/sid1/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "idle"


class TestCancelSessionRunEndpoint:
    def test_cancel_no_active_run(self) -> None:
        client = TestClient(app)
        client.post("/api/v1/agents", json=SAMPLE_AGENT)
        resp = client.delete("/api/v1/agents/test-agent/sessions/sid1/run")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_cancel_404(self) -> None:
        client = TestClient(app)
        resp = client.delete("/api/v1/agents/nonexistent/sessions/sid1/run")
        assert resp.status_code == 404

    def test_cancel_with_event_store(self) -> None:
        """When event store is present, cancel sets status and appends event."""
        client = TestClient(app)
        client.post("/api/v1/agents", json=SAMPLE_AGENT)

        mock_event_store = AsyncMock()
        agents_module._bg._event_store = mock_event_store
        mock_event_store.get_owner = AsyncMock(return_value=None)

        resp = client.delete("/api/v1/agents/test-agent/sessions/sid1/run")
        assert resp.status_code == 200
        mock_event_store.set_status.assert_called_once()
        mock_event_store.append_event.assert_called_once()

        # Clean up
        agents_module._bg._event_store = None

    def test_cancel_forbidden_for_non_owner(self) -> None:
        """Non-owner non-admin gets 403."""
        client = TestClient(app)
        # Create agent with shared_with=["*"] so access check passes
        agent = {**SAMPLE_AGENT, "shared_with": ["*"]}
        client.post("/api/v1/agents", json=agent)

        mock_event_store = AsyncMock()
        agents_module._bg._event_store = mock_event_store
        mock_event_store.get_owner = AsyncMock(return_value="other-user-id")

        # Noop auth returns principal with is_admin=True, so patch to non-admin
        from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal

        non_admin = AuthenticatedPrincipal(id="user1", type="user", scopes=frozenset())
        with patch("agentic_primitives_gateway.routes.agents.require_principal", return_value=non_admin):
            resp = client.delete("/api/v1/agents/test-agent/sessions/sid1/run")
        assert resp.status_code == 403

        # Clean up
        agents_module._bg._event_store = None


class TestSessionHistoryWithData:
    def test_history_with_turns(self) -> None:
        client = TestClient(app)
        client.post("/api/v1/agents", json=SAMPLE_AGENT)

        mock_turns = [
            [{"role": "user", "text": "hello"}, {"role": "assistant", "text": "hi"}],
        ]
        with patch("agentic_primitives_gateway.routes.agents.registry") as mock_reg:
            mock_reg.memory.get_last_turns = AsyncMock(return_value=mock_turns)
            resp = client.get("/api/v1/agents/test-agent/sessions/sid1")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][0]["content"] == "hello"

    def test_history_with_overrides(self) -> None:
        client = TestClient(app)
        agent = {**SAMPLE_AGENT, "provider_overrides": {"memory": "default"}}
        client.post("/api/v1/agents", json=agent)
        resp = client.get("/api/v1/agents/test-agent/sessions/sid1")
        assert resp.status_code == 200

    def test_history_provider_error_handled(self) -> None:
        client = TestClient(app)
        client.post("/api/v1/agents", json=SAMPLE_AGENT)
        with patch("agentic_primitives_gateway.routes.agents.registry") as mock_reg:
            mock_reg.memory.get_last_turns = AsyncMock(side_effect=NotImplementedError("not supported"))
            resp = client.get("/api/v1/agents/test-agent/sessions/sid1")
        assert resp.status_code == 200
        assert resp.json()["messages"] == []


class TestChatWithOverrides:
    def test_chat_with_provider_overrides(self) -> None:
        client = TestClient(app)
        agent = {**SAMPLE_AGENT, "provider_overrides": {"memory": "default"}}
        client.post("/api/v1/agents", json=agent)
        resp = client.post(
            "/api/v1/agents/test-agent/chat",
            json={"message": "hello"},
        )
        assert resp.status_code == 200
