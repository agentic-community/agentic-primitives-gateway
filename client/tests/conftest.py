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

    # Identity data plane endpoints
    if path.startswith("/api/v1/identity"):
        return _handle_identity(method, path, request)

    # Observability endpoints
    if path.startswith("/api/v1/observability"):
        return _handle_observability(method, path, request)

    # Tools endpoints
    if path.startswith("/api/v1/tools"):
        return _handle_tools(method, path, request)

    # Code interpreter endpoints
    if path.startswith("/api/v1/code-interpreter"):
        return _handle_code_interpreter(method, path, request)

    # Stub endpoints → 501
    for prefix in (
        "/api/v1/gateway",
        "/api/v1/browser",
    ):
        if path.startswith(prefix):
            return httpx.Response(501, json={"detail": "Not implemented"})

    # Memory endpoints
    if path.startswith("/api/v1/memory/"):
        return _handle_memory(method, path, request)

    return httpx.Response(404, json={"detail": "Not found"})


_tools_store: dict[str, dict] = {}


_ci_sessions: dict[str, dict] = {}


def _handle_code_interpreter(method: str, path: str, request: httpx.Request) -> httpx.Response:
    """Mock handler for code interpreter endpoints."""
    rest = path.removeprefix("/api/v1/code-interpreter")

    # POST /sessions (start)
    if rest == "/sessions" and method == "POST":
        body = json.loads(request.content)
        sid = body.get("session_id") or "mock-session"
        session = {
            "session_id": sid,
            "status": "active",
            "language": body.get("language", "python"),
            "created_at": "2025-01-01T00:00:00Z",
        }
        _ci_sessions[sid] = session
        return httpx.Response(201, json=session)

    # GET /sessions (list)
    if rest == "/sessions" and method == "GET":
        return httpx.Response(200, json={"sessions": list(_ci_sessions.values())})

    if rest.startswith("/sessions/"):
        parts = rest.removeprefix("/sessions/").split("/")
        session_id = parts[0]

        # DELETE /sessions/{id}
        if len(parts) == 1 and method == "DELETE":
            _ci_sessions.pop(session_id, None)
            return httpx.Response(204)

        # GET /sessions/{id} (get session)
        if len(parts) == 1 and method == "GET":
            session = _ci_sessions.get(session_id)
            if not session:
                return httpx.Response(404, json={"detail": "Session not found"})
            return httpx.Response(200, json=session)

        # POST /sessions/{id}/execute
        if len(parts) == 2 and parts[1] == "execute" and method == "POST":
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "session_id": session_id,
                    "stdout": "",
                    "stderr": "",
                    "exit_code": 0,
                },
            )

        # GET /sessions/{id}/history
        if len(parts) == 2 and parts[1] == "history" and method == "GET":
            if session_id not in _ci_sessions:
                return httpx.Response(404, json={"detail": "Session not found"})
            return httpx.Response(200, json={"entries": []})

        # POST /sessions/{id}/files
        if len(parts) == 2 and parts[1] == "files" and method == "POST":
            return httpx.Response(200, json={"filename": "test.py", "size": 0, "session_id": session_id})

        # GET /sessions/{id}/files/{filename}
        if len(parts) == 3 and parts[1] == "files" and method == "GET":
            return httpx.Response(200, content=b"file content")

    return httpx.Response(404, json={"detail": "Not found"})


def _handle_tools(method: str, path: str, request: httpx.Request) -> httpx.Response:
    """Mock handler for tools endpoints."""
    rest = path.removeprefix("/api/v1/tools")

    # POST "" (register tool)
    if rest == "" and method == "POST":
        body = json.loads(request.content)
        tool = {
            "name": body["name"],
            "description": body.get("description", ""),
            "parameters": body.get("parameters", {}),
            "metadata": body.get("metadata", {}),
        }
        _tools_store[body["name"]] = tool
        return httpx.Response(201, json=tool)

    # GET "" (list tools)
    if rest == "" and method == "GET":
        return httpx.Response(200, json={"tools": list(_tools_store.values())})

    # GET /search
    if rest == "/search" and method == "GET":
        return httpx.Response(200, json={"tools": list(_tools_store.values())})

    # GET /servers
    if rest == "/servers" and method == "GET":
        return httpx.Response(200, json={"servers": []})

    # POST /servers
    if rest == "/servers" and method == "POST":
        body = json.loads(request.content)
        return httpx.Response(201, json={"name": body.get("name", ""), "status": "registered"})

    # GET /servers/{name}
    if rest.startswith("/servers/") and method == "GET":
        server_name = rest.removeprefix("/servers/")
        return httpx.Response(
            200,
            json={"name": server_name, "url": "", "health_status": "healthy", "tools_count": 0, "metadata": {}},
        )

    # POST /{name}/invoke
    if rest.endswith("/invoke") and method == "POST":
        tool_name = rest.removesuffix("/invoke").lstrip("/")
        return httpx.Response(200, json={"tool_name": tool_name, "result": "mock result"})

    # GET /{name} (get tool)
    if method == "GET" and rest.startswith("/"):
        tool_name = rest.lstrip("/")
        tool = _tools_store.get(tool_name)
        if tool:
            return httpx.Response(200, json=tool)
        return httpx.Response(200, json={"name": tool_name, "description": "mock", "parameters": {}, "metadata": {}})

    # DELETE /{name}
    if method == "DELETE" and rest.startswith("/"):
        tool_name = rest.lstrip("/")
        _tools_store.pop(tool_name, None)
        return httpx.Response(204)

    return httpx.Response(404, json={"detail": "Not found"})


def _handle_observability(method: str, path: str, request: httpx.Request) -> httpx.Response:
    """Mock handler for observability endpoints."""
    rest = path.removeprefix("/api/v1/observability")

    # POST /flush
    if rest == "/flush" and method == "POST":
        return httpx.Response(202, json={"status": "accepted"})

    # GET /sessions
    if rest == "/sessions" and method == "GET":
        return httpx.Response(200, json={"sessions": []})

    # GET /sessions/{session_id}
    if rest.startswith("/sessions/") and method == "GET":
        session_id = rest.removeprefix("/sessions/")
        return httpx.Response(200, json={"session_id": session_id, "trace_count": 0, "metadata": {}})

    # POST /traces (ingest)
    if rest == "/traces" and method == "POST":
        return httpx.Response(202, json={"status": "accepted"})

    # GET /traces (query)
    if rest == "/traces" and method == "GET":
        return httpx.Response(200, json={"traces": []})

    # POST /logs
    if rest == "/logs" and method == "POST":
        return httpx.Response(202, json={"status": "accepted"})

    # Routes with /traces/{trace_id}
    if rest.startswith("/traces/"):
        parts = rest.removeprefix("/traces/").split("/")
        trace_id = parts[0]

        # POST /traces/{trace_id}/generations
        if len(parts) == 2 and parts[1] == "generations" and method == "POST":
            body = json.loads(request.content)
            return httpx.Response(
                201,
                json={
                    "generation_id": "gen-mock",
                    "trace_id": trace_id,
                    "name": body.get("name", ""),
                    "model": body.get("model", ""),
                },
            )

        # POST /traces/{trace_id}/scores
        if len(parts) == 2 and parts[1] == "scores" and method == "POST":
            body = json.loads(request.content)
            return httpx.Response(
                201,
                json={
                    "score_id": "score-mock",
                    "trace_id": trace_id,
                    "name": body.get("name", ""),
                    "value": body.get("value", 0),
                },
            )

        # GET /traces/{trace_id}/scores
        if len(parts) == 2 and parts[1] == "scores" and method == "GET":
            return httpx.Response(200, json={"scores": []})

        # GET /traces/{trace_id}
        if len(parts) == 1 and method == "GET":
            return httpx.Response(
                200,
                json={"trace_id": trace_id, "name": "mock-trace", "tags": [], "spans": [], "metadata": {}},
            )

        # PUT /traces/{trace_id}
        if len(parts) == 1 and method == "PUT":
            return httpx.Response(200, json={"trace_id": trace_id, "status": "updated"})

    return httpx.Response(404, json={"detail": "Not found"})


def _handle_identity(method: str, path: str, request: httpx.Request) -> httpx.Response:
    """Mock handler for identity endpoints."""
    if method == "POST" and path == "/api/v1/identity/token":
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "access_token": "mock-token",
                "token_type": "Bearer",
                "scopes": body.get("scopes", []),
            },
        )

    if method == "POST" and path == "/api/v1/identity/api-key":
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "api_key": "mock-api-key",
                "credential_provider": body.get("credential_provider", ""),
            },
        )

    if method == "POST" and path == "/api/v1/identity/workload-token":
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "workload_token": "mock-workload-token",
                "workload_name": body.get("workload_name", ""),
            },
        )

    if method == "GET" and path == "/api/v1/identity/credential-providers":
        return httpx.Response(200, json={"credential_providers": []})

    # Control plane and other identity endpoints → 501
    return httpx.Response(501, json={"detail": "Not implemented"})


_events: dict[str, dict[str, list[dict]]] = {}
_event_counter = 0


def _handle_memory(method: str, path: str, request: httpx.Request) -> httpx.Response:
    global _event_counter
    rest = path.removeprefix("/api/v1/memory/")

    # ── Conversation events ──────────────────────────────────────────
    # POST /sessions/{actor_id}/{session_id}/events
    if rest.startswith("sessions/"):
        session_parts = rest.removeprefix("sessions/").split("/")

        # /sessions/{actor_id}/{session_id}/events
        if len(session_parts) >= 3 and session_parts[2] == "events":
            actor_id, session_id = session_parts[0], session_parts[1]

            if len(session_parts) == 3:
                if method == "POST":
                    body = json.loads(request.content)
                    _event_counter += 1
                    event = {
                        "event_id": f"evt-{_event_counter}",
                        "actor_id": actor_id,
                        "session_id": session_id,
                        "messages": body["messages"],
                        "timestamp": "2025-01-01T00:00:00Z",
                        "metadata": body.get("metadata", {}),
                    }
                    _events.setdefault(actor_id, {}).setdefault(session_id, []).append(event)
                    return httpx.Response(201, json=event)

                if method == "GET":
                    events = _events.get(actor_id, {}).get(session_id, [])
                    return httpx.Response(200, json={"events": events})

            # /sessions/{actor_id}/{session_id}/events/{event_id}
            if len(session_parts) == 4:
                event_id = session_parts[3]
                events = _events.get(actor_id, {}).get(session_id, [])

                if method == "GET":
                    for e in events:
                        if e["event_id"] == event_id:
                            return httpx.Response(200, json=e)
                    return httpx.Response(404, json={"detail": "Event not found"})

                if method == "DELETE":
                    for i, e in enumerate(events):
                        if e["event_id"] == event_id:
                            events.pop(i)
                            return httpx.Response(204)
                    return httpx.Response(404, json={"detail": "Event not found"})

        # /sessions/{actor_id}/{session_id}/turns
        if len(session_parts) == 3 and session_parts[2] == "turns":
            actor_id, session_id = session_parts[0], session_parts[1]
            if method == "GET":
                events = _events.get(actor_id, {}).get(session_id, [])
                turns = [{"messages": e["messages"]} for e in events[-5:]]
                return httpx.Response(200, json={"turns": turns})

        # /sessions/{actor_id}/{session_id}/branches
        if len(session_parts) >= 3 and session_parts[2] == "branches":
            if method == "POST":
                return httpx.Response(201, json={"name": "branch-1", "root_event_id": "evt-1"})
            if method == "GET":
                return httpx.Response(200, json={"branches": []})

    # ── Session management ───────────────────────────────────────────
    if rest == "actors" and method == "GET":
        actors = [{"actor_id": aid, "metadata": {}} for aid in _events]
        return httpx.Response(200, json={"actors": actors})

    if rest.startswith("actors/") and rest.endswith("/sessions") and method == "GET":
        actor_id = rest.removeprefix("actors/").removesuffix("/sessions")
        sessions = [{"session_id": sid, "actor_id": actor_id, "metadata": {}} for sid in _events.get(actor_id, {})]
        return httpx.Response(200, json={"sessions": sessions})

    # ── Control plane ────────────────────────────────────────────────
    if rest == "resources" and method == "POST":
        body = json.loads(request.content)
        return httpx.Response(
            201,
            json={"memory_id": "mem-new", "name": body["name"], "status": "ACTIVE"},
        )
    if rest == "resources" and method == "GET":
        return httpx.Response(200, json={"resources": []})

    if rest.startswith("resources/"):
        res_parts = rest.removeprefix("resources/").split("/")
        memory_id = res_parts[0]

        # /resources/{memory_id}/strategies
        if len(res_parts) == 2 and res_parts[1] == "strategies":
            if method == "GET":
                return httpx.Response(200, json={"strategies": []})
            if method == "POST":
                return httpx.Response(201, json={"strategy_id": "strat-1", "type": "semantic"})

        # /resources/{memory_id}/strategies/{strategy_id}
        if len(res_parts) == 3 and res_parts[1] == "strategies" and method == "DELETE":
            return httpx.Response(204)

        # /resources/{memory_id}
        if len(res_parts) == 1:
            if method == "GET":
                return httpx.Response(
                    200,
                    json={"memory_id": memory_id, "name": "test", "status": "ACTIVE"},
                )
            if method == "DELETE":
                return httpx.Response(204)

    # ── Key-value memory (original) ──────────────────────────────────
    parts = rest.split("/")
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
    global _event_counter
    _store.clear()
    _events.clear()
    _tools_store.clear()
    _ci_sessions.clear()
    _event_counter = 0
    yield
    _store.clear()
    _events.clear()
    _tools_store.clear()
    _ci_sessions.clear()
    _event_counter = 0


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
