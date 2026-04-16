from __future__ import annotations

from fastapi.testclient import TestClient


class TestPolicyEngines:
    def test_create_policy_engine(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/policy/engines",
            json={"name": "test-engine", "description": "A test engine"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "test-engine"
        assert data["description"] == "A test engine"
        assert "policy_engine_id" in data

    def test_list_policy_engines(self, client: TestClient) -> None:
        client.post("/api/v1/policy/engines", json={"name": "e1"})
        resp = client.get("/api/v1/policy/engines")
        assert resp.status_code == 200
        data = resp.json()
        assert "policy_engines" in data

    def test_get_policy_engine(self, client: TestClient) -> None:
        create_resp = client.post("/api/v1/policy/engines", json={"name": "e2"})
        engine_id = create_resp.json()["policy_engine_id"]
        resp = client.get(f"/api/v1/policy/engines/{engine_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "e2"

    def test_delete_policy_engine(self, client: TestClient) -> None:
        create_resp = client.post("/api/v1/policy/engines", json={"name": "e3"})
        engine_id = create_resp.json()["policy_engine_id"]
        resp = client.delete(f"/api/v1/policy/engines/{engine_id}")
        assert resp.status_code == 204


class TestPolicies:
    def _create_engine(self, client: TestClient) -> str:
        resp = client.post("/api/v1/policy/engines", json={"name": "pe"})
        return resp.json()["policy_engine_id"]

    def test_create_policy(self, client: TestClient) -> None:
        engine_id = self._create_engine(client)
        resp = client.post(
            f"/api/v1/policy/engines/{engine_id}/policies",
            json={"policy_body": "permit(principal, action, resource);", "description": "allow all"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["definition"] == "permit(principal, action, resource);"
        assert "policy_id" in data

    def test_list_policies(self, client: TestClient) -> None:
        engine_id = self._create_engine(client)
        client.post(
            f"/api/v1/policy/engines/{engine_id}/policies",
            json={"policy_body": "permit(principal, action, resource);"},
        )
        resp = client.get(f"/api/v1/policy/engines/{engine_id}/policies")
        assert resp.status_code == 200
        assert "policies" in resp.json()

    def test_get_policy(self, client: TestClient) -> None:
        engine_id = self._create_engine(client)
        create_resp = client.post(
            f"/api/v1/policy/engines/{engine_id}/policies",
            json={"policy_body": "forbid(principal, action, resource);"},
        )
        policy_id = create_resp.json()["policy_id"]
        resp = client.get(f"/api/v1/policy/engines/{engine_id}/policies/{policy_id}")
        assert resp.status_code == 200
        assert resp.json()["definition"] == "forbid(principal, action, resource);"

    def test_update_policy(self, client: TestClient) -> None:
        engine_id = self._create_engine(client)
        create_resp = client.post(
            f"/api/v1/policy/engines/{engine_id}/policies",
            json={"policy_body": "old body"},
        )
        policy_id = create_resp.json()["policy_id"]
        resp = client.put(
            f"/api/v1/policy/engines/{engine_id}/policies/{policy_id}",
            json={"policy_body": "new body", "description": "updated"},
        )
        assert resp.status_code == 200
        assert resp.json()["definition"] == "new body"

    def test_delete_policy(self, client: TestClient) -> None:
        engine_id = self._create_engine(client)
        create_resp = client.post(
            f"/api/v1/policy/engines/{engine_id}/policies",
            json={"policy_body": "body"},
        )
        policy_id = create_resp.json()["policy_id"]
        resp = client.delete(f"/api/v1/policy/engines/{engine_id}/policies/{policy_id}")
        assert resp.status_code == 204


class TestPolicyGeneration501:
    """Policy generation is not supported by the noop provider."""

    def test_start_generation_returns_501(self, client: TestClient) -> None:
        resp = client.post("/api/v1/policy/engines/fake/generations", json={})
        assert resp.status_code == 501

    def test_list_generations_returns_501(self, client: TestClient) -> None:
        resp = client.get("/api/v1/policy/engines/fake/generations")
        assert resp.status_code == 501

    def test_get_generation_returns_501(self, client: TestClient) -> None:
        resp = client.get("/api/v1/policy/engines/fake/generations/fake-gen")
        assert resp.status_code == 501

    def test_list_generation_assets_returns_501(self, client: TestClient) -> None:
        resp = client.get("/api/v1/policy/engines/fake/generations/fake-gen/assets")
        assert resp.status_code == 501
