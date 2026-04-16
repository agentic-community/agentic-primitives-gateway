from __future__ import annotations

from fastapi.testclient import TestClient


class TestExistingEndpoints:
    """Verify existing endpoints still work with noop provider."""

    def test_ingest_trace(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/observability/traces",
            json={"trace_id": "t1", "name": "test"},
        )
        assert resp.status_code == 202

    def test_ingest_log(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/observability/logs",
            json={"message": "test"},
        )
        assert resp.status_code == 202

    def test_query_traces(self, client: TestClient) -> None:
        resp = client.get("/api/v1/observability/traces")
        assert resp.status_code == 200
        assert resp.json()["traces"] == []


class TestGetTrace501:
    def test_get_trace_returns_501(self, client: TestClient) -> None:
        resp = client.get("/api/v1/observability/traces/t-1")
        assert resp.status_code == 501


class TestUpdateTrace501:
    def test_update_trace_returns_501(self, client: TestClient) -> None:
        resp = client.put(
            "/api/v1/observability/traces/t-1",
            json={"name": "updated"},
        )
        assert resp.status_code == 501


class TestLogGeneration501:
    def test_log_generation_returns_501(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/observability/traces/t-1/generations",
            json={"name": "chat", "model": "claude"},
        )
        assert resp.status_code == 501


class TestScoring501:
    def test_score_trace_returns_501(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/observability/traces/t-1/scores",
            json={"name": "quality", "value": 0.9},
        )
        assert resp.status_code == 501

    def test_list_scores_returns_501(self, client: TestClient) -> None:
        resp = client.get("/api/v1/observability/traces/t-1/scores")
        assert resp.status_code == 501


class TestSessions501:
    def test_list_sessions_returns_501(self, client: TestClient) -> None:
        resp = client.get("/api/v1/observability/sessions")
        assert resp.status_code == 501

    def test_get_session_returns_501(self, client: TestClient) -> None:
        resp = client.get("/api/v1/observability/sessions/s-1")
        assert resp.status_code == 501


class TestFlush501:
    def test_flush_returns_501(self, client: TestClient) -> None:
        resp = client.post("/api/v1/observability/flush")
        assert resp.status_code == 501
