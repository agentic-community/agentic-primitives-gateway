from __future__ import annotations

from fastapi.testclient import TestClient


class TestExistingEndpoints:
    """Verify existing endpoints still work with noop provider."""

    def test_start_session(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/code-interpreter/sessions",
            json={"language": "python"},
        )
        assert resp.status_code == 201
        assert "session_id" in resp.json()

    def test_list_sessions(self, client: TestClient) -> None:
        resp = client.get("/api/v1/code-interpreter/sessions")
        assert resp.status_code == 200
        assert "sessions" in resp.json()

    def test_stop_session(self, client: TestClient) -> None:
        # Start first
        start_resp = client.post(
            "/api/v1/code-interpreter/sessions",
            json={"session_id": "s-1", "language": "python"},
        )
        assert start_resp.status_code == 201

        resp = client.delete("/api/v1/code-interpreter/sessions/s-1")
        assert resp.status_code == 204


class TestGetSession:
    def test_get_session_after_start(self, client: TestClient) -> None:
        client.post(
            "/api/v1/code-interpreter/sessions",
            json={"session_id": "s-1", "language": "python"},
        )
        resp = client.get("/api/v1/code-interpreter/sessions/s-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "s-1"
        assert data["status"] == "active"
        assert "created_at" in data

    def test_get_session_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/v1/code-interpreter/sessions/nonexistent")
        assert resp.status_code == 404


class TestExecutionHistory:
    def test_history_empty(self, client: TestClient) -> None:
        client.post(
            "/api/v1/code-interpreter/sessions",
            json={"session_id": "s-1", "language": "python"},
        )
        resp = client.get("/api/v1/code-interpreter/sessions/s-1/history")
        assert resp.status_code == 200
        assert resp.json()["entries"] == []

    def test_history_session_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/v1/code-interpreter/sessions/nonexistent/history")
        assert resp.status_code == 404


class TestNoopStateful:
    """Verify the noop provider now tracks session state."""

    def test_list_sessions_tracks_started(self, client: TestClient) -> None:
        client.post(
            "/api/v1/code-interpreter/sessions",
            json={"session_id": "s-a", "language": "python"},
        )
        client.post(
            "/api/v1/code-interpreter/sessions",
            json={"session_id": "s-b", "language": "python"},
        )
        resp = client.get("/api/v1/code-interpreter/sessions")
        sessions = resp.json()["sessions"]
        session_ids = [s["session_id"] for s in sessions]
        assert "s-a" in session_ids
        assert "s-b" in session_ids

    def test_stop_removes_from_list(self, client: TestClient) -> None:
        client.post(
            "/api/v1/code-interpreter/sessions",
            json={"session_id": "s-1", "language": "python"},
        )
        client.delete("/api/v1/code-interpreter/sessions/s-1")
        resp = client.get("/api/v1/code-interpreter/sessions")
        session_ids = [s["session_id"] for s in resp.json()["sessions"]]
        assert "s-1" not in session_ids
