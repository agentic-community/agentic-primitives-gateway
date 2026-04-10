"""Tests for the declarative agents subsystem.

Tests CRUD operations, the tool-call loop, auto-memory hooks, agent store
persistence, and max-turns safety.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agentic_primitives_gateway.agents.store import FileAgentStore
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.primitives.llm.base import LLMProvider
from agentic_primitives_gateway.routes.agents import set_agent_store

# ── Mock gateway provider ────────────────────────────────────────────


class MockLLMProvider(LLMProvider):
    """Gateway provider that returns configurable responses for testing."""

    def __init__(self) -> None:
        self._responses: list[dict[str, Any]] = []
        self._call_index = 0
        self.requests: list[dict[str, Any]] = []

    def set_responses(self, responses: list[dict[str, Any]]) -> None:
        self._responses = responses
        self._call_index = 0

    async def route_request(self, model_request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(model_request)
        if self._call_index < len(self._responses):
            resp = self._responses[self._call_index]
            self._call_index += 1
            return resp
        return {
            "model": "mock",
            "content": "default response",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

    async def list_models(self) -> list[dict[str, Any]]:
        return [{"name": "mock", "provider": "test", "capabilities": ["chat", "tool_use"]}]

    async def healthcheck(self) -> bool:
        return True


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def agent_store(tmp_path: Any) -> FileAgentStore:
    """Create a FileAgentStore with a temp path and wire it in."""
    store = FileAgentStore(path=str(tmp_path / "agents.json"))
    set_agent_store(store)
    return store


@pytest.fixture
def mock_llm_provider(agent_store: FileAgentStore) -> MockLLMProvider:
    """Replace the LLM provider in the registry with our mock."""
    mock = MockLLMProvider()
    with patch("agentic_primitives_gateway.agents.runner.registry") as mock_registry:
        # We need the real registry for most things but mock gateway
        from agentic_primitives_gateway.registry import registry as real_registry

        # Copy real registry attributes
        mock_registry.memory = real_registry.memory
        mock_registry.observability = real_registry.observability
        mock_registry.tools = real_registry.tools
        mock_registry.identity = real_registry.identity
        mock_registry.code_interpreter = real_registry.code_interpreter
        mock_registry.browser = real_registry.browser
        mock_registry.llm = mock
        yield mock


@pytest.fixture
def agent_client(agent_store: FileAgentStore) -> TestClient:
    """TestClient with agent store initialized."""
    return TestClient(app)


# ── Helpers ──────────────────────────────────────────────────────────

SAMPLE_AGENT = {
    "name": "test-agent",
    "model": "mock-model",
    "system_prompt": "You are a test assistant.",
    "description": "A test agent",
    "max_turns": 5,
    "primitives": {
        "memory": {"enabled": True, "namespace": "test:{agent_name}"},
    },
    "hooks": {"auto_memory": False, "auto_trace": False},
}


# ── CRUD Tests ───────────────────────────────────────────────────────


class TestAgentCRUD:
    def test_create_agent(self, agent_client: TestClient, agent_store: FileAgentStore) -> None:
        resp = agent_client.post("/api/v1/agents", json=SAMPLE_AGENT)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "test-agent"
        assert data["model"] == "mock-model"
        assert data["system_prompt"] == "You are a test assistant."

    def test_create_duplicate_agent(self, agent_client: TestClient, agent_store: FileAgentStore) -> None:
        agent_client.post("/api/v1/agents", json=SAMPLE_AGENT)
        resp = agent_client.post("/api/v1/agents", json=SAMPLE_AGENT)
        assert resp.status_code == 409

    def test_list_agents(self, agent_client: TestClient, agent_store: FileAgentStore) -> None:
        agent_client.post("/api/v1/agents", json=SAMPLE_AGENT)
        resp = agent_client.get("/api/v1/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["agents"]) == 1
        assert data["agents"][0]["name"] == "test-agent"

    def test_get_agent(self, agent_client: TestClient, agent_store: FileAgentStore) -> None:
        agent_client.post("/api/v1/agents", json=SAMPLE_AGENT)
        resp = agent_client.get("/api/v1/agents/test-agent")
        assert resp.status_code == 200
        assert resp.json()["name"] == "test-agent"

    def test_get_agent_not_found(self, agent_client: TestClient, agent_store: FileAgentStore) -> None:
        resp = agent_client.get("/api/v1/agents/nonexistent")
        assert resp.status_code == 404

    def test_update_agent(self, agent_client: TestClient, agent_store: FileAgentStore) -> None:
        agent_client.post("/api/v1/agents", json=SAMPLE_AGENT)
        resp = agent_client.put(
            "/api/v1/agents/test-agent",
            json={"description": "Updated description", "max_turns": 10},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["description"] == "Updated description"
        assert data["max_turns"] == 10
        assert data["model"] == "mock-model"  # Unchanged

    def test_update_agent_not_found(self, agent_client: TestClient, agent_store: FileAgentStore) -> None:
        resp = agent_client.put("/api/v1/agents/nonexistent", json={"description": "x"})
        assert resp.status_code == 404

    def test_delete_agent(self, agent_client: TestClient, agent_store: FileAgentStore) -> None:
        agent_client.post("/api/v1/agents", json=SAMPLE_AGENT)
        resp = agent_client.delete("/api/v1/agents/test-agent")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        resp = agent_client.get("/api/v1/agents/test-agent")
        assert resp.status_code == 404

    def test_delete_agent_not_found(self, agent_client: TestClient, agent_store: FileAgentStore) -> None:
        resp = agent_client.delete("/api/v1/agents/nonexistent")
        assert resp.status_code == 404


# ── Chat Tests ───────────────────────────────────────────────────────


class TestAgentChat:
    def test_simple_text_response(
        self,
        agent_client: TestClient,
        mock_llm_provider: MockLLMProvider,
    ) -> None:
        # Create agent
        agent_client.post("/api/v1/agents", json=SAMPLE_AGENT)

        # Set mock to return a simple text response
        mock_llm_provider.set_responses(
            [
                {
                    "model": "mock-model",
                    "content": "Hello! I'm your test assistant.",
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 10, "output_tokens": 8},
                }
            ]
        )

        resp = agent_client.post(
            "/api/v1/agents/test-agent/chat",
            json={"message": "Hello!"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["response"] == "Hello! I'm your test assistant."
        assert data["agent_name"] == "test-agent"
        assert data["turns_used"] == 1
        assert data["tools_called"] == []
        assert "session_id" in data

    def test_tool_call_loop(
        self,
        agent_client: TestClient,
        mock_llm_provider: MockLLMProvider,
    ) -> None:
        # Create agent with memory tools
        agent_client.post("/api/v1/agents", json=SAMPLE_AGENT)

        # Set mock: first call returns tool_use, second returns text
        mock_llm_provider.set_responses(
            [
                {
                    "model": "mock-model",
                    "content": "",
                    "stop_reason": "tool_use",
                    "tool_calls": [
                        {
                            "id": "tc-1",
                            "name": "remember",
                            "input": {"key": "greeting", "content": "Hello world"},
                        }
                    ],
                    "usage": {"input_tokens": 10, "output_tokens": 20},
                },
                {
                    "model": "mock-model",
                    "content": "Done! I stored that in memory.",
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 30, "output_tokens": 10},
                },
            ]
        )

        resp = agent_client.post(
            "/api/v1/agents/test-agent/chat",
            json={"message": "Remember that my greeting is Hello world"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["response"] == "Done! I stored that in memory."
        assert data["turns_used"] == 2
        assert "remember" in data["tools_called"]

    def test_max_turns_safety(
        self,
        agent_client: TestClient,
        mock_llm_provider: MockLLMProvider,
    ) -> None:
        # Create agent with max_turns=2
        agent = {**SAMPLE_AGENT, "max_turns": 2}
        agent_client.post("/api/v1/agents", json=agent)

        # Set mock to always return tool_use (infinite loop)
        mock_llm_provider.set_responses(
            [
                {
                    "model": "mock-model",
                    "content": "thinking...",
                    "stop_reason": "tool_use",
                    "tool_calls": [{"id": f"tc-{i}", "name": "remember", "input": {"key": "k", "content": "v"}}],
                    "usage": {"input_tokens": 10, "output_tokens": 10},
                }
                for i in range(5)
            ]
        )

        resp = agent_client.post(
            "/api/v1/agents/test-agent/chat",
            json={"message": "Do something"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["turns_used"] == 2
        assert "maximum number of turns" in data["response"]

    def test_chat_agent_not_found(self, agent_client: TestClient, agent_store: FileAgentStore) -> None:
        resp = agent_client.post(
            "/api/v1/agents/nonexistent/chat",
            json={"message": "Hello"},
        )
        assert resp.status_code == 404

    def test_chat_with_session_id(
        self,
        agent_client: TestClient,
        mock_llm_provider: MockLLMProvider,
    ) -> None:
        agent_client.post("/api/v1/agents", json=SAMPLE_AGENT)
        mock_llm_provider.set_responses(
            [
                {
                    "model": "mock-model",
                    "content": "Response with session",
                    "stop_reason": "end_turn",
                    "usage": {},
                }
            ]
        )

        resp = agent_client.post(
            "/api/v1/agents/test-agent/chat",
            json={"message": "Hello", "session_id": "my-session-123"},
        )
        assert resp.status_code == 200
        assert resp.json()["session_id"] == "my-session-123"


# ── Store persistence tests ──────────────────────────────────────────


class TestAgentStore:
    def test_persistence_survives_reload(self, tmp_path: Any) -> None:
        path = str(tmp_path / "agents.json")

        # Create and save
        store1 = FileAgentStore(path=path)
        set_agent_store(store1)
        client = TestClient(app)
        client.post("/api/v1/agents", json=SAMPLE_AGENT)

        # Reload from disk
        store2 = FileAgentStore(path=path)
        set_agent_store(store2)
        agents = client.get("/api/v1/agents").json()["agents"]
        assert len(agents) == 1
        assert agents[0]["name"] == "test-agent"

    def test_seed_from_config(self, tmp_path: Any) -> None:
        store = FileAgentStore(path=str(tmp_path / "agents.json"))
        store.seed(
            {
                "seeded-agent": {
                    "model": "mock-model",
                    "system_prompt": "I was seeded.",
                }
            }
        )
        set_agent_store(store)
        client = TestClient(app)
        resp = client.get("/api/v1/agents/seeded-agent")
        assert resp.status_code == 200
        assert resp.json()["system_prompt"] == "I was seeded."

    def test_seed_overwrites_existing(self, tmp_path: Any) -> None:
        path = str(tmp_path / "agents.json")
        store = FileAgentStore(path=path)
        set_agent_store(store)
        client = TestClient(app)

        # Create via API
        client.post(
            "/api/v1/agents",
            json={**SAMPLE_AGENT, "description": "API created"},
        )

        # Seed with same name but different description — config wins
        store.seed({"test-agent": {"model": "other-model", "description": "Seeded"}})

        resp = client.get("/api/v1/agents/test-agent")
        assert resp.json()["description"] == "Seeded"  # Config overwrites
