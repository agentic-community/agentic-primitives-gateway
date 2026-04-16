from __future__ import annotations

from fastapi.testclient import TestClient


class TestConversationEvents:
    """Tests for conversation event CRUD using the in_memory provider."""

    def test_create_event(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/memory/sessions/actor-1/sess-1/events",
            json={
                "messages": [{"text": "Hello", "role": "user"}],
                "metadata": {"source": "test"},
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["actor_id"] == "actor-1"
        assert data["session_id"] == "sess-1"
        assert len(data["messages"]) == 1
        assert data["messages"][0]["text"] == "Hello"
        assert data["messages"][0]["role"] == "user"
        assert "event_id" in data

    def test_create_event_multiple_messages(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/memory/sessions/actor-1/sess-1/events",
            json={
                "messages": [
                    {"text": "Hello", "role": "user"},
                    {"text": "Hi there!", "role": "assistant"},
                ],
            },
        )
        assert resp.status_code == 201
        assert len(resp.json()["messages"]) == 2

    def test_list_events(self, client: TestClient) -> None:
        client.post(
            "/api/v1/memory/sessions/actor-1/sess-1/events",
            json={"messages": [{"text": "msg1", "role": "user"}]},
        )
        client.post(
            "/api/v1/memory/sessions/actor-1/sess-1/events",
            json={"messages": [{"text": "msg2", "role": "assistant"}]},
        )
        resp = client.get("/api/v1/memory/sessions/actor-1/sess-1/events")
        assert resp.status_code == 200
        events = resp.json()["events"]
        assert len(events) == 2

    def test_list_events_with_limit(self, client: TestClient) -> None:
        for i in range(5):
            client.post(
                "/api/v1/memory/sessions/actor-1/sess-1/events",
                json={"messages": [{"text": f"msg{i}", "role": "user"}]},
            )
        resp = client.get("/api/v1/memory/sessions/actor-1/sess-1/events?limit=3")
        assert resp.status_code == 200
        assert len(resp.json()["events"]) == 3

    def test_get_event(self, client: TestClient) -> None:
        create_resp = client.post(
            "/api/v1/memory/sessions/actor-1/sess-1/events",
            json={"messages": [{"text": "Hello", "role": "user"}]},
        )
        event_id = create_resp.json()["event_id"]

        resp = client.get(f"/api/v1/memory/sessions/actor-1/sess-1/events/{event_id}")
        assert resp.status_code == 200
        assert resp.json()["event_id"] == event_id
        assert resp.json()["messages"][0]["text"] == "Hello"

    def test_get_event_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/v1/memory/sessions/actor-1/sess-1/events/nonexistent")
        assert resp.status_code == 404

    def test_delete_event(self, client: TestClient) -> None:
        create_resp = client.post(
            "/api/v1/memory/sessions/actor-1/sess-1/events",
            json={"messages": [{"text": "to delete", "role": "user"}]},
        )
        event_id = create_resp.json()["event_id"]

        resp = client.delete(f"/api/v1/memory/sessions/actor-1/sess-1/events/{event_id}")
        assert resp.status_code == 204

        # Verify it's gone
        get_resp = client.get(f"/api/v1/memory/sessions/actor-1/sess-1/events/{event_id}")
        assert get_resp.status_code == 404

    def test_delete_event_not_found(self, client: TestClient) -> None:
        resp = client.delete("/api/v1/memory/sessions/actor-1/sess-1/events/nonexistent")
        assert resp.status_code == 404


class TestGetLastTurns:
    def test_get_last_turns(self, client: TestClient) -> None:
        for i in range(5):
            client.post(
                "/api/v1/memory/sessions/actor-1/sess-1/events",
                json={"messages": [{"text": f"turn {i}", "role": "user"}]},
            )
        resp = client.get("/api/v1/memory/sessions/actor-1/sess-1/turns?k=3")
        assert resp.status_code == 200
        turns = resp.json()["turns"]
        assert len(turns) == 3
        # Should be the last 3 turns
        assert turns[0]["messages"][0]["text"] == "turn 2"
        assert turns[2]["messages"][0]["text"] == "turn 4"

    def test_get_last_turns_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v1/memory/sessions/actor-1/sess-1/turns")
        assert resp.status_code == 200
        assert resp.json()["turns"] == []


class TestSessionManagement:
    def test_list_actors(self, client: TestClient) -> None:
        client.post(
            "/api/v1/memory/sessions/actor-1/sess-1/events",
            json={"messages": [{"text": "hi", "role": "user"}]},
        )
        client.post(
            "/api/v1/memory/sessions/actor-2/sess-1/events",
            json={"messages": [{"text": "hi", "role": "user"}]},
        )
        resp = client.get("/api/v1/memory/actors")
        assert resp.status_code == 200
        actors = resp.json()["actors"]
        actor_ids = [a["actor_id"] for a in actors]
        assert "actor-1" in actor_ids
        assert "actor-2" in actor_ids

    def test_list_actors_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v1/memory/actors")
        assert resp.status_code == 200
        assert resp.json()["actors"] == []

    def test_list_sessions(self, client: TestClient) -> None:
        client.post(
            "/api/v1/memory/sessions/actor-1/sess-a/events",
            json={"messages": [{"text": "hi", "role": "user"}]},
        )
        client.post(
            "/api/v1/memory/sessions/actor-1/sess-b/events",
            json={"messages": [{"text": "hi", "role": "user"}]},
        )
        resp = client.get("/api/v1/memory/actors/actor-1/sessions")
        assert resp.status_code == 200
        sessions = resp.json()["sessions"]
        session_ids = [s["session_id"] for s in sessions]
        assert "sess-a" in session_ids
        assert "sess-b" in session_ids

    def test_list_sessions_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v1/memory/actors/nonexistent/sessions")
        assert resp.status_code == 200
        assert resp.json()["sessions"] == []


class TestBranchManagement501:
    """Branch management is not implemented by the in_memory provider."""

    def test_fork_returns_501(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/memory/sessions/actor-1/sess-1/branches",
            json={
                "root_event_id": "evt-1",
                "branch_name": "branch-1",
                "messages": [{"text": "hello", "role": "user"}],
            },
        )
        assert resp.status_code == 501

    def test_list_branches_returns_501(self, client: TestClient) -> None:
        resp = client.get("/api/v1/memory/sessions/actor-1/sess-1/branches")
        assert resp.status_code == 501


class TestControlPlane501:
    """Control plane is not implemented by the in_memory provider."""

    def test_create_resource_returns_501(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/memory/resources",
            json={"name": "test-resource"},
        )
        assert resp.status_code == 501

    def test_list_resources_returns_501(self, client: TestClient) -> None:
        resp = client.get("/api/v1/memory/resources")
        assert resp.status_code == 501

    def test_get_resource_returns_501(self, client: TestClient) -> None:
        resp = client.get("/api/v1/memory/resources/mem-1")
        assert resp.status_code == 501

    def test_delete_resource_returns_501(self, client: TestClient) -> None:
        resp = client.delete("/api/v1/memory/resources/mem-1")
        assert resp.status_code == 501


class TestStrategyManagement501:
    """Strategy management is not implemented by the in_memory provider."""

    def test_list_strategies_returns_501(self, client: TestClient) -> None:
        resp = client.get("/api/v1/memory/resources/mem-1/strategies")
        assert resp.status_code == 501

    def test_add_strategy_returns_501(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/memory/resources/mem-1/strategies",
            json={"strategy": {"type": "semantic"}},
        )
        assert resp.status_code == 501

    def test_delete_strategy_returns_501(self, client: TestClient) -> None:
        resp = client.delete("/api/v1/memory/resources/mem-1/strategies/strat-1")
        assert resp.status_code == 501


class TestExistingEndpointsUnchanged:
    """Verify existing key-value endpoints still work after route reordering."""

    def test_store_and_retrieve(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/memory/agent:test",
            json={"key": "k1", "content": "hello"},
        )
        assert resp.status_code == 201

        resp = client.get("/api/v1/memory/agent:test/k1")
        assert resp.status_code == 200
        assert resp.json()["content"] == "hello"

    def test_search(self, client: TestClient) -> None:
        client.post(
            "/api/v1/memory/ns1",
            json={"key": "k1", "content": "python programming"},
        )
        resp = client.post(
            "/api/v1/memory/ns1/search",
            json={"query": "programming"},
        )
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 1

    def test_list_memories(self, client: TestClient) -> None:
        client.post("/api/v1/memory/ns1", json={"key": "a", "content": "aaa"})
        resp = client.get("/api/v1/memory/ns1")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_delete(self, client: TestClient) -> None:
        client.post("/api/v1/memory/ns1", json={"key": "k1", "content": "hello"})
        resp = client.delete("/api/v1/memory/ns1/k1")
        assert resp.status_code == 204
