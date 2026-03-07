"""Tests for new agent endpoints: tools, memory, tool-catalog, streaming."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient

from agentic_primitives_gateway.agents.store import FileAgentStore
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.routes.agents import set_agent_store

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
        assert data["namespace"] == "agent:test-agent"

    def test_404_for_unknown_agent(self) -> None:
        client = TestClient(app)
        resp = client.get("/api/v1/agents/nonexistent/memory")
        assert resp.status_code == 404


class TestStreamingEndpoint:
    def test_returns_sse_response(self) -> None:
        """Noop gateway returns empty content, but SSE envelope should work."""
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
