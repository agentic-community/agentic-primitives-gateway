from __future__ import annotations

import re

from fastapi.testclient import TestClient


class TestCorrelationIdHeader:
    def test_client_provided_correlation_id_echoed(self, client: TestClient) -> None:
        resp = client.get("/healthz", headers={"x-correlation-id": "corr-xyz"})
        assert resp.status_code == 200
        assert resp.headers["x-correlation-id"] == "corr-xyz"

    def test_defaults_to_request_id(self, client: TestClient) -> None:
        resp = client.get("/healthz", headers={"x-request-id": "req-abc"})
        # Without an explicit correlation header, correlation defaults to request_id.
        assert resp.headers["x-correlation-id"] == "req-abc"

    def test_generated_when_absent(self, client: TestClient) -> None:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        corr = resp.headers["x-correlation-id"]
        # Matches the generated request_id (hex uuid).
        assert re.fullmatch(r"[0-9a-f]{32}", corr)
