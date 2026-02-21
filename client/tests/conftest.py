from __future__ import annotations

import json

import httpx
import pytest

from agentic_primitives_gateway_client import AgenticPlatformClient

# In-memory store used by the mock transport to simulate server state
_store: dict[str, dict] = {}


def _mock_handler(request: httpx.Request) -> httpx.Response:
    """Minimal mock that simulates the agentic-primitives-gateway memory API."""
    path = request.url.path
    method = request.method

    # Health
    if path == "/healthz":
        return httpx.Response(200, json={"status": "ok"})
    if path == "/readyz":
        return httpx.Response(200, json={"status": "ok", "checks": {"memory": True}})

    # Stub endpoints → 501
    for prefix in (
        "/api/v1/observability",
        "/api/v1/gateway",
        "/api/v1/tools",
        "/api/v1/identity",
        "/api/v1/code-interpreter",
        "/api/v1/browser",
    ):
        if path.startswith(prefix):
            return httpx.Response(501, json={"detail": "Not implemented"})

    # Memory endpoints
    if path.startswith("/api/v1/memory/"):
        return _handle_memory(method, path, request)

    return httpx.Response(404, json={"detail": "Not found"})


def _handle_memory(method: str, path: str, request: httpx.Request) -> httpx.Response:
    parts = path.removeprefix("/api/v1/memory/").split("/")
    namespace = parts[0]

    # POST /{namespace}/search
    if method == "POST" and len(parts) == 2 and parts[1] == "search":
        body = json.loads(request.content)
        query = body["query"].lower()
        top_k = body.get("top_k", 10)
        ns_store = _store.get(namespace, {})
        results = []
        for record in ns_store.values():
            if query in record["content"].lower():
                results.append({"record": record, "score": 0.9})
        return httpx.Response(200, json={"results": results[:top_k]})

    # POST /{namespace}  (store)
    if method == "POST" and len(parts) == 1:
        body = json.loads(request.content)
        record = {
            "namespace": namespace,
            "key": body["key"],
            "content": body["content"],
            "metadata": body.get("metadata", {}),
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:00Z",
        }
        _store.setdefault(namespace, {})[body["key"]] = record
        return httpx.Response(201, json=record)

    # GET /{namespace}  (list)
    if method == "GET" and len(parts) == 1:
        ns_store = _store.get(namespace, {})
        records = list(ns_store.values())
        return httpx.Response(200, json={"records": records, "total": len(records)})

    # GET /{namespace}/{key}  (retrieve)
    if method == "GET" and len(parts) == 2:
        key = parts[1]
        record = _store.get(namespace, {}).get(key)
        if record is None:
            return httpx.Response(404, json={"detail": "Memory not found"})
        return httpx.Response(200, json=record)

    # DELETE /{namespace}/{key}
    if method == "DELETE" and len(parts) == 2:
        key = parts[1]
        ns_store = _store.get(namespace, {})
        if key in ns_store:
            del ns_store[key]
            return httpx.Response(204)
        return httpx.Response(404, json={"detail": "Memory not found"})

    return httpx.Response(404, json={"detail": "Not found"})


@pytest.fixture(autouse=True)
def _clear_store():
    _store.clear()
    yield
    _store.clear()


@pytest.fixture
def make_client():
    """Factory that yields a client backed by the mock transport."""

    def _factory(**kwargs):
        class _Ctx:
            async def __aenter__(self):
                transport = httpx.MockTransport(_mock_handler)
                self._http = await httpx.AsyncClient(transport=transport, base_url="http://test").__aenter__()
                client = AgenticPlatformClient.__new__(AgenticPlatformClient)
                client._client = self._http
                client._aws_headers = {}
                client._aws_from_environment = False
                client._provider_headers = {}
                client._service_cred_headers = {}
                client._max_retries = kwargs.get("max_retries", 3)
                client._retry_backoff = kwargs.get("retry_backoff", 0.5)
                client._retry_status_codes = kwargs.get("retry_status_codes", {502, 503, 504})
                return client

            async def __aexit__(self, *args):
                await self._http.__aexit__(*args)

        return _Ctx()

    return _factory
