from __future__ import annotations

from fastapi.testclient import TestClient


class TestStoreMemory:
    def test_store_returns_201(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/memory/agent:test",
            json={"key": "greeting", "content": "hello world"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["namespace"] == "agent:test"
        assert data["key"] == "greeting"
        assert data["content"] == "hello world"

    def test_store_with_metadata(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/memory/agent:test",
            json={
                "key": "fact",
                "content": "the sky is blue",
                "metadata": {"source": "observation"},
            },
        )
        assert resp.status_code == 201
        assert resp.json()["metadata"] == {"source": "observation"}

    def test_store_upsert(self, client: TestClient) -> None:
        client.post(
            "/api/v1/memory/agent:test",
            json={"key": "k1", "content": "version1"},
        )
        resp = client.post(
            "/api/v1/memory/agent:test",
            json={"key": "k1", "content": "version2"},
        )
        assert resp.status_code == 201
        assert resp.json()["content"] == "version2"

        # Retrieve should return the updated value
        get_resp = client.get("/api/v1/memory/agent:test/k1")
        assert get_resp.json()["content"] == "version2"


class TestRetrieveMemory:
    def test_retrieve_existing(self, client: TestClient) -> None:
        client.post(
            "/api/v1/memory/ns1",
            json={"key": "k1", "content": "hello"},
        )
        resp = client.get("/api/v1/memory/ns1/k1")
        assert resp.status_code == 200
        assert resp.json()["content"] == "hello"

    def test_retrieve_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/v1/memory/ns1/nonexistent")
        assert resp.status_code == 404

    def test_namespace_isolation(self, client: TestClient) -> None:
        client.post(
            "/api/v1/memory/ns-a",
            json={"key": "k1", "content": "from A"},
        )
        client.post(
            "/api/v1/memory/ns-b",
            json={"key": "k1", "content": "from B"},
        )
        assert client.get("/api/v1/memory/ns-a/k1").json()["content"] == "from A"
        assert client.get("/api/v1/memory/ns-b/k1").json()["content"] == "from B"


class TestListMemories:
    def test_list_empty_namespace(self, client: TestClient) -> None:
        resp = client.get("/api/v1/memory/empty-ns")
        assert resp.status_code == 200
        assert resp.json()["records"] == []
        assert resp.json()["total"] == 0

    def test_list_returns_records(self, client: TestClient) -> None:
        client.post("/api/v1/memory/ns1", json={"key": "a", "content": "aaa"})
        client.post("/api/v1/memory/ns1", json={"key": "b", "content": "bbb"})
        resp = client.get("/api/v1/memory/ns1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["records"]) == 2

    def test_list_pagination(self, client: TestClient) -> None:
        for i in range(5):
            client.post("/api/v1/memory/ns1", json={"key": f"k{i}", "content": f"val{i}"})
        resp = client.get("/api/v1/memory/ns1?limit=2&offset=0")
        assert len(resp.json()["records"]) == 2

        resp = client.get("/api/v1/memory/ns1?limit=2&offset=3")
        assert len(resp.json()["records"]) == 2


class TestSearchMemory:
    def test_search_finds_match(self, client: TestClient) -> None:
        client.post(
            "/api/v1/memory/ns1",
            json={"key": "fact1", "content": "Python is a programming language"},
        )
        client.post(
            "/api/v1/memory/ns1",
            json={"key": "fact2", "content": "The weather is sunny today"},
        )
        resp = client.post(
            "/api/v1/memory/ns1/search",
            json={"query": "programming"},
        )
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert len(results) == 1
        assert results[0]["record"]["key"] == "fact1"
        assert results[0]["score"] > 0

    def test_search_empty_results(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/memory/ns1/search",
            json={"query": "nonexistent"},
        )
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    def test_search_respects_top_k(self, client: TestClient) -> None:
        for i in range(5):
            client.post(
                "/api/v1/memory/ns1",
                json={"key": f"k{i}", "content": f"item number {i}"},
            )
        resp = client.post(
            "/api/v1/memory/ns1/search",
            json={"query": "item", "top_k": 2},
        )
        assert len(resp.json()["results"]) == 2


class TestDeleteMemory:
    def test_delete_existing(self, client: TestClient) -> None:
        client.post("/api/v1/memory/ns1", json={"key": "k1", "content": "hello"})
        resp = client.delete("/api/v1/memory/ns1/k1")
        assert resp.status_code == 204

        # Verify it's gone
        assert client.get("/api/v1/memory/ns1/k1").status_code == 404

    def test_delete_not_found(self, client: TestClient) -> None:
        resp = client.delete("/api/v1/memory/ns1/nonexistent")
        assert resp.status_code == 404


class TestHealthEndpoints:
    def test_liveness(self, client: TestClient) -> None:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_readiness(self, client: TestClient) -> None:
        resp = client.get("/readyz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["checks"]["memory/default"] == "ok"


class TestObservabilityEndpoints:
    def test_ingest_trace(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/observability/traces",
            json={"trace_id": "t1", "spans": [], "metadata": {}},
        )
        assert resp.status_code == 202
        assert resp.json()["status"] == "accepted"

    def test_ingest_log(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/observability/logs",
            json={"message": "test log"},
        )
        assert resp.status_code == 202
        assert resp.json()["status"] == "accepted"

    def test_query_traces(self, client: TestClient) -> None:
        resp = client.get("/api/v1/observability/traces")
        assert resp.status_code == 200
        assert resp.json()["traces"] == []


class TestGatewayEndpoints:
    def test_completions(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/gateway/completions",
            json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 200

    def test_list_models(self, client: TestClient) -> None:
        resp = client.get("/api/v1/gateway/models")
        assert resp.status_code == 200
        assert resp.json()["models"] == []


class TestToolsEndpoints:
    def test_register_tool(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/tools",
            json={"name": "search", "description": "Search the web"},
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "search"

    def test_list_tools(self, client: TestClient) -> None:
        resp = client.get("/api/v1/tools")
        assert resp.status_code == 200
        assert resp.json()["tools"] == []

    def test_invoke_tool(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/tools/search/invoke",
            json={"params": {"query": "test"}},
        )
        assert resp.status_code == 200
        assert resp.json()["tool_name"] == "search"


class TestIdentityEndpoints:
    def test_get_token(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/identity/token",
            json={"credential_provider": "github", "workload_token": "wt", "scopes": ["repo"]},
        )
        assert resp.status_code == 200

    def test_get_api_key(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/identity/api-key",
            json={"credential_provider": "openai", "workload_token": "wt"},
        )
        assert resp.status_code == 200

    def test_list_credential_providers(self, client: TestClient) -> None:
        resp = client.get("/api/v1/identity/credential-providers")
        assert resp.status_code == 200
        assert resp.json()["credential_providers"] == []


class TestCodeInterpreterEndpoints:
    def test_start_session(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/code-interpreter/sessions",
            json={"language": "python"},
        )
        assert resp.status_code == 201

    def test_list_sessions(self, client: TestClient) -> None:
        resp = client.get("/api/v1/code-interpreter/sessions")
        assert resp.status_code == 200
        assert resp.json()["sessions"] == []

    def test_stop_session(self, client: TestClient) -> None:
        resp = client.delete("/api/v1/code-interpreter/sessions/s1")
        assert resp.status_code == 204

    def test_execute_code(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/code-interpreter/sessions/s1/execute",
            json={"code": "print('hello')"},
        )
        assert resp.status_code == 200


class TestBrowserEndpoints:
    def test_start_session(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/browser/sessions",
            json={"viewport": {"width": 1920, "height": 1080}},
        )
        assert resp.status_code == 201

    def test_list_sessions(self, client: TestClient) -> None:
        resp = client.get("/api/v1/browser/sessions")
        assert resp.status_code == 200
        assert resp.json()["sessions"] == []

    def test_stop_session(self, client: TestClient) -> None:
        resp = client.delete("/api/v1/browser/sessions/s1")
        assert resp.status_code == 204

    def test_get_live_view(self, client: TestClient) -> None:
        resp = client.get("/api/v1/browser/sessions/s1/live-view")
        assert resp.status_code == 200
        assert "url" in resp.json()
