from __future__ import annotations

from fastapi.testclient import TestClient


class TestExistingEndpoints:
    """Verify existing endpoints still work with noop provider."""

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

    def test_search_tools(self, client: TestClient) -> None:
        resp = client.get("/api/v1/tools/search?query=test")
        assert resp.status_code == 200
        assert "tools" in resp.json()

    def test_invoke_tool(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/tools/search/invoke",
            json={"params": {"query": "test"}},
        )
        assert resp.status_code == 200
        assert resp.json()["tool_name"] == "search"


class TestGetTool501:
    def test_get_tool_returns_501(self, client: TestClient) -> None:
        resp = client.get("/api/v1/tools/my-tool")
        assert resp.status_code == 501


class TestDeleteTool501:
    def test_delete_tool_returns_501(self, client: TestClient) -> None:
        resp = client.delete("/api/v1/tools/my-tool")
        assert resp.status_code == 501


class TestServerManagement501:
    def test_list_servers_returns_501(self, client: TestClient) -> None:
        resp = client.get("/api/v1/tools/servers")
        assert resp.status_code == 501

    def test_register_server_returns_501(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/tools/servers",
            json={"name": "test-server", "url": "http://localhost:9000"},
        )
        assert resp.status_code == 501

    def test_get_server_returns_501(self, client: TestClient) -> None:
        resp = client.get("/api/v1/tools/servers/my-server")
        assert resp.status_code == 501
