"""Integration tests that boot a real uvicorn server and send HTTP traffic."""

from __future__ import annotations

import re
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from agentic_primitives_gateway.config import Settings
from agentic_primitives_gateway.registry import registry

HEX32 = re.compile(r"[0-9a-f]{32}")


def _free_port() -> int:
    """Find an available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(base_url: str, *, timeout: float = 5.0) -> None:
    """Poll the health endpoint until the server is ready."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base_url}/healthz", timeout=1.0)
            if r.status_code == 200:
                return
        except httpx.ConnectError:
            pass
        time.sleep(0.05)
    raise RuntimeError(f"Server at {base_url} did not start within {timeout}s")


@pytest.fixture(scope="module")
def live_server() -> str:
    """Start a real uvicorn server in a background thread, return its base URL."""
    # Initialise registry with in-memory/noop providers (same as conftest)
    test_settings = Settings(
        providers={
            "memory": {
                "backend": "agentic_primitives_gateway.primitives.memory.in_memory.InMemoryProvider",
                "config": {},
            },
            "observability": {
                "backend": "agentic_primitives_gateway.primitives.observability.noop.NoopObservabilityProvider",
                "config": {},
            },
            "gateway": {
                "backend": "agentic_primitives_gateway.primitives.gateway.noop.NoopGatewayProvider",
                "config": {},
            },
            "tools": {
                "backend": "agentic_primitives_gateway.primitives.tools.noop.NoopToolsProvider",
                "config": {},
            },
            "identity": {
                "backend": "agentic_primitives_gateway.primitives.identity.noop.NoopIdentityProvider",
                "config": {},
            },
            "code_interpreter": {
                "backend": "agentic_primitives_gateway.primitives.code_interpreter.noop.NoopCodeInterpreterProvider",
                "config": {},
            },
            "browser": {
                "backend": "agentic_primitives_gateway.primitives.browser.noop.NoopBrowserProvider",
                "config": {},
            },
        }
    )
    registry.initialize(test_settings)

    from agentic_primitives_gateway.main import app

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    _wait_for_server(base_url)
    yield base_url  # type: ignore[misc]

    server.should_exit = True
    thread.join(timeout=3)


# ── Tests against the live server ────────────────────────────────


class TestRequestIdAcrossEndpoints:
    """X-Request-Id is returned on every primitive endpoint over real HTTP."""

    def test_health_liveness(self, live_server: str) -> None:
        r = httpx.get(f"{live_server}/healthz", headers={"x-request-id": "health-live"})
        assert r.status_code == 200
        assert r.headers["x-request-id"] == "health-live"

    def test_health_readiness(self, live_server: str) -> None:
        r = httpx.get(f"{live_server}/readyz", headers={"x-request-id": "health-ready"})
        assert r.headers["x-request-id"] == "health-ready"

    def test_memory_store(self, live_server: str) -> None:
        r = httpx.post(
            f"{live_server}/api/v1/memory/ns-rid",
            json={"key": "k1", "content": "hello"},
            headers={"x-request-id": "mem-store"},
        )
        assert r.status_code == 201
        assert r.headers["x-request-id"] == "mem-store"

    def test_memory_retrieve(self, live_server: str) -> None:
        httpx.post(f"{live_server}/api/v1/memory/ns-rid", json={"key": "k2", "content": "data"})
        r = httpx.get(f"{live_server}/api/v1/memory/ns-rid/k2", headers={"x-request-id": "mem-get"})
        assert r.status_code == 200
        assert r.headers["x-request-id"] == "mem-get"

    def test_memory_list(self, live_server: str) -> None:
        r = httpx.get(f"{live_server}/api/v1/memory/ns-rid", headers={"x-request-id": "mem-list"})
        assert r.status_code == 200
        assert r.headers["x-request-id"] == "mem-list"

    def test_memory_search(self, live_server: str) -> None:
        r = httpx.post(
            f"{live_server}/api/v1/memory/ns-rid/search",
            json={"query": "hello"},
            headers={"x-request-id": "mem-search"},
        )
        assert r.status_code == 200
        assert r.headers["x-request-id"] == "mem-search"

    def test_memory_delete(self, live_server: str) -> None:
        httpx.post(f"{live_server}/api/v1/memory/ns-rid", json={"key": "del1", "content": "bye"})
        r = httpx.delete(f"{live_server}/api/v1/memory/ns-rid/del1", headers={"x-request-id": "mem-del"})
        assert r.status_code == 204
        assert r.headers["x-request-id"] == "mem-del"

    def test_observability_traces(self, live_server: str) -> None:
        r = httpx.post(
            f"{live_server}/api/v1/observability/traces",
            json={"trace_id": "t1", "spans": [], "metadata": {}},
            headers={"x-request-id": "obs-trace"},
        )
        assert r.headers["x-request-id"] == "obs-trace"

    def test_observability_logs(self, live_server: str) -> None:
        r = httpx.post(
            f"{live_server}/api/v1/observability/logs",
            json={"message": "test"},
            headers={"x-request-id": "obs-log"},
        )
        assert r.headers["x-request-id"] == "obs-log"

    def test_gateway_completions(self, live_server: str) -> None:
        r = httpx.post(
            f"{live_server}/api/v1/gateway/completions",
            json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
            headers={"x-request-id": "gw-comp"},
        )
        assert r.headers["x-request-id"] == "gw-comp"

    def test_tools_list(self, live_server: str) -> None:
        r = httpx.get(f"{live_server}/api/v1/tools", headers={"x-request-id": "tools-list"})
        assert r.headers["x-request-id"] == "tools-list"

    def test_identity_providers(self, live_server: str) -> None:
        r = httpx.get(f"{live_server}/api/v1/identity/providers", headers={"x-request-id": "id-prov"})
        assert r.headers["x-request-id"] == "id-prov"

    def test_code_interpreter_sessions(self, live_server: str) -> None:
        r = httpx.get(f"{live_server}/api/v1/code-interpreter/sessions", headers={"x-request-id": "ci-list"})
        assert r.headers["x-request-id"] == "ci-list"

    def test_browser_sessions(self, live_server: str) -> None:
        r = httpx.get(f"{live_server}/api/v1/browser/sessions", headers={"x-request-id": "br-list"})
        assert r.headers["x-request-id"] == "br-list"

    def test_providers_endpoint(self, live_server: str) -> None:
        r = httpx.get(f"{live_server}/api/v1/providers", headers={"x-request-id": "prov-list"})
        assert r.headers["x-request-id"] == "prov-list"


class TestRequestIdOnErrorResponses:
    """Request ID must be present even when the server returns an error."""

    def test_404_not_found(self, live_server: str) -> None:
        r = httpx.get(f"{live_server}/api/v1/memory/ns-rid/nonexistent", headers={"x-request-id": "err-404"})
        assert r.status_code == 404
        assert r.headers["x-request-id"] == "err-404"

    def test_404_delete(self, live_server: str) -> None:
        r = httpx.delete(f"{live_server}/api/v1/memory/ns-rid/nope", headers={"x-request-id": "err-404-del"})
        assert r.status_code == 404
        assert r.headers["x-request-id"] == "err-404-del"

    def test_422_validation_error(self, live_server: str) -> None:
        r = httpx.post(
            f"{live_server}/api/v1/memory/ns-rid",
            json={},
            headers={"x-request-id": "err-422"},
        )
        assert r.status_code == 422
        assert r.headers["x-request-id"] == "err-422"

    def test_server_generated_id_on_error(self, live_server: str) -> None:
        """Error responses without a client ID still get a generated one."""
        r = httpx.get(f"{live_server}/api/v1/memory/ns-rid/nonexistent")
        assert r.status_code == 404
        assert HEX32.fullmatch(r.headers["x-request-id"])


class TestRequestIdGeneration:
    """Server generates valid unique IDs when the client omits the header."""

    def test_server_generates_hex_id(self, live_server: str) -> None:
        r = httpx.get(f"{live_server}/healthz")
        assert r.status_code == 200
        assert HEX32.fullmatch(r.headers["x-request-id"])

    def test_each_request_gets_unique_id(self, live_server: str) -> None:
        ids = {httpx.get(f"{live_server}/healthz").headers["x-request-id"] for _ in range(5)}
        assert len(ids) == 5


class TestRequestIdMultiStepWorkflows:
    """Request IDs stay correct across related sequential requests."""

    def test_store_then_retrieve_different_ids(self, live_server: str) -> None:
        r1 = httpx.post(
            f"{live_server}/api/v1/memory/ns-wf",
            json={"key": "wf1", "content": "workflow data"},
            headers={"x-request-id": "wf-store"},
        )
        assert r1.headers["x-request-id"] == "wf-store"

        r2 = httpx.get(f"{live_server}/api/v1/memory/ns-wf/wf1", headers={"x-request-id": "wf-get"})
        assert r2.headers["x-request-id"] == "wf-get"
        assert r2.json()["content"] == "workflow data"

    def test_generated_ids_unique_across_workflow(self, live_server: str) -> None:
        httpx.post(f"{live_server}/api/v1/memory/ns-wf2", json={"key": "wf2", "content": "temp"})
        r1 = httpx.post(f"{live_server}/api/v1/memory/ns-wf3", json={"key": "wf3", "content": "temp"})
        r2 = httpx.get(f"{live_server}/api/v1/memory/ns-wf3/wf3")
        r3 = httpx.delete(f"{live_server}/api/v1/memory/ns-wf3/wf3")

        ids = {r1.headers["x-request-id"], r2.headers["x-request-id"], r3.headers["x-request-id"]}
        assert len(ids) == 3


class TestRequestIdWithOtherMiddleware:
    """Request ID works alongside AWS creds and provider routing headers."""

    def test_with_aws_credentials(self, live_server: str) -> None:
        r = httpx.get(
            f"{live_server}/healthz",
            headers={
                "x-request-id": "with-aws",
                "x-aws-access-key-id": "AKIAIOSFODNN7EXAMPLE",
                "x-aws-secret-access-key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                "x-aws-session-token": "FwoGZXIvYXdzEBAaDH...",
                "x-aws-region": "us-west-2",
            },
        )
        assert r.status_code == 200
        assert r.headers["x-request-id"] == "with-aws"

    def test_with_service_credentials(self, live_server: str) -> None:
        r = httpx.get(
            f"{live_server}/healthz",
            headers={
                "x-request-id": "with-svc",
                "x-cred-langfuse-public-key": "pk-test",
                "x-cred-langfuse-secret-key": "sk-test",
            },
        )
        assert r.status_code == 200
        assert r.headers["x-request-id"] == "with-svc"

    def test_with_provider_routing(self, live_server: str) -> None:
        r = httpx.get(
            f"{live_server}/api/v1/memory/ns-prov",
            headers={
                "x-request-id": "with-prov",
                "x-provider-memory": "default",
            },
        )
        assert r.status_code == 200
        assert r.headers["x-request-id"] == "with-prov"

    def test_with_all_headers_combined(self, live_server: str) -> None:
        r = httpx.post(
            f"{live_server}/api/v1/memory/ns-combo",
            json={"key": "combo", "content": "all headers"},
            headers={
                "x-request-id": "all-combined",
                "x-aws-access-key-id": "AKIAIOSFODNN7EXAMPLE",
                "x-aws-secret-access-key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                "x-cred-langfuse-public-key": "pk-test",
                "x-provider-memory": "default",
            },
        )
        assert r.status_code == 201
        assert r.headers["x-request-id"] == "all-combined"
