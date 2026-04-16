from __future__ import annotations

import re

from fastapi.testclient import TestClient


class TestRequestIdMiddleware:
    def test_client_provided_id_is_echoed_back(self, client: TestClient) -> None:
        """When X-Request-Id is sent, the same value appears in the response."""
        resp = client.get("/healthz", headers={"x-request-id": "test-123"})
        assert resp.status_code == 200
        assert resp.headers["x-request-id"] == "test-123"

    def test_server_generates_id_when_absent(self, client: TestClient) -> None:
        """Without X-Request-Id, the server generates a valid hex UUID."""
        resp = client.get("/healthz")
        assert resp.status_code == 200
        request_id = resp.headers["x-request-id"]
        assert re.fullmatch(r"[0-9a-f]{32}", request_id)

    def test_each_request_gets_unique_id(self, client: TestClient) -> None:
        """Consecutive requests without X-Request-Id get different IDs."""
        ids = {client.get("/healthz").headers["x-request-id"] for _ in range(5)}
        assert len(ids) == 5

    def test_id_available_in_context(self, client: TestClient) -> None:
        """get_request_id() returns the correct value during a request."""
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        from agentic_primitives_gateway.context import get_request_id
        from agentic_primitives_gateway.main import app

        async def _ctx_endpoint(request):  # type: ignore[no-untyped-def]
            return JSONResponse({"request_id": get_request_id()})

        # Temporarily add a test route
        test_route = Route("/test-request-id-ctx", _ctx_endpoint)
        app.routes.append(test_route)
        try:
            resp = client.get(
                "/test-request-id-ctx",
                headers={"x-request-id": "from-header"},
            )
            assert resp.status_code == 200
            assert resp.json()["request_id"] == "from-header"
        finally:
            app.routes.remove(test_route)
