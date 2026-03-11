from __future__ import annotations

from fastapi.testclient import TestClient


class TestEvaluatorCRUD:
    def test_create_evaluator(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/evaluations/evaluators",
            json={"name": "quality-check", "evaluator_type": "llm", "description": "test"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "quality-check"
        assert "evaluator_id" in data

    def test_list_evaluators(self, client: TestClient) -> None:
        client.post(
            "/api/v1/evaluations/evaluators",
            json={"name": "ev1", "evaluator_type": "llm"},
        )
        resp = client.get("/api/v1/evaluations/evaluators")
        assert resp.status_code == 200
        assert "evaluators" in resp.json()

    def test_get_evaluator(self, client: TestClient) -> None:
        create_resp = client.post(
            "/api/v1/evaluations/evaluators",
            json={"name": "ev2", "evaluator_type": "llm"},
        )
        evaluator_id = create_resp.json()["evaluator_id"]
        resp = client.get(f"/api/v1/evaluations/evaluators/{evaluator_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "ev2"

    def test_update_evaluator(self, client: TestClient) -> None:
        create_resp = client.post(
            "/api/v1/evaluations/evaluators",
            json={"name": "ev3", "evaluator_type": "llm"},
        )
        evaluator_id = create_resp.json()["evaluator_id"]
        resp = client.put(
            f"/api/v1/evaluations/evaluators/{evaluator_id}",
            json={"description": "updated desc"},
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "updated desc"

    def test_delete_evaluator(self, client: TestClient) -> None:
        create_resp = client.post(
            "/api/v1/evaluations/evaluators",
            json={"name": "ev4", "evaluator_type": "llm"},
        )
        evaluator_id = create_resp.json()["evaluator_id"]
        resp = client.delete(f"/api/v1/evaluations/evaluators/{evaluator_id}")
        assert resp.status_code == 204


class TestEvaluate:
    def test_evaluate(self, client: TestClient) -> None:
        create_resp = client.post(
            "/api/v1/evaluations/evaluators",
            json={"name": "scorer", "evaluator_type": "llm"},
        )
        evaluator_id = create_resp.json()["evaluator_id"]
        resp = client.post(
            "/api/v1/evaluations/evaluate",
            json={
                "evaluator_id": evaluator_id,
                "target": "some text",
                "input_data": "input",
                "output_data": "output",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert data["results"][0]["label"] == "PASS"


class TestOnlineEvalConfig501:
    """Online eval configs are not supported by the noop provider."""

    def test_create_online_config_returns_501(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/evaluations/online-configs",
            json={"name": "cfg", "evaluator_ids": ["a"]},
        )
        assert resp.status_code == 501

    def test_list_online_configs_returns_501(self, client: TestClient) -> None:
        resp = client.get("/api/v1/evaluations/online-configs")
        assert resp.status_code == 501

    def test_get_online_config_returns_501(self, client: TestClient) -> None:
        resp = client.get("/api/v1/evaluations/online-configs/fake-id")
        assert resp.status_code == 501

    def test_delete_online_config_returns_501(self, client: TestClient) -> None:
        resp = client.delete("/api/v1/evaluations/online-configs/fake-id")
        assert resp.status_code == 501
