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
    if path == "/auth/config":
        return httpx.Response(200, json={"backend": "noop", "oidc": None})
    if path == "/api/v1/auth/whoami":
        return httpx.Response(
            200,
            json={
                "id": "noop",
                "type": "user",
                "is_admin": True,
                "groups": [],
                "scopes": ["admin"],
            },
        )

    # Audit endpoints (admin-only on the real server; mock returns stubs)
    if path == "/api/v1/audit/status":
        return httpx.Response(
            200,
            json={
                "stream_sink_configured": True,
                "stream_name": "gateway:audit",
                "length": 3,
                "maxlen": 100000,
            },
        )
    if path == "/api/v1/audit/events":
        return httpx.Response(
            200,
            json={
                "events": [
                    {
                        "schema_version": "1",
                        "event_id": "abc123",
                        "timestamp": "2026-04-17T00:00:00+00:00",
                        "action": "auth.success",
                        "outcome": "success",
                        "actor_id": "alice",
                        "actor_type": "user",
                        "actor_groups": [],
                        "resource_type": None,
                        "resource_id": None,
                        "request_id": "r1",
                        "correlation_id": "c1",
                        "source_ip": None,
                        "user_agent": None,
                        "http_method": "GET",
                        "http_path": "/api/v1/providers",
                        "http_status": None,
                        "duration_ms": None,
                        "reason": None,
                        "metadata": {"backend": "NoopAuthBackend"},
                    }
                ],
                "next": None,
                "scanned": 1,
            },
        )

    # Providers
    if path == "/api/v1/providers" and method == "GET":
        return httpx.Response(200, json={"memory": {"default": "in_memory", "available": ["in_memory"]}})

    # A2A endpoints
    if path == "/.well-known/agent.json" or path.startswith("/a2a/"):
        return _handle_a2a(method, path, request)

    # Admin proposals (must route before generic /agents, /teams prefixes)
    if path == "/api/v1/admin/agents/proposals" and method == "GET":
        versions = []
        for vlist in _agent_versions.values():
            for v in vlist:
                if v["version_id"] in _agent_proposals:
                    versions.append(v)
        return httpx.Response(200, json={"versions": versions})
    if path == "/api/v1/admin/teams/proposals" and method == "GET":
        versions = []
        for vlist in _team_versions.values():
            for v in vlist:
                if v["version_id"] in _team_proposals:
                    versions.append(v)
        return httpx.Response(200, json={"versions": versions})

    # Agent endpoints
    if path.startswith("/api/v1/agents"):
        return _handle_agents(method, path, request)

    # Team endpoints
    if path.startswith("/api/v1/teams"):
        return _handle_teams(method, path, request)

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

    # Policy endpoints
    if path.startswith("/api/v1/policy"):
        return _handle_policy(method, path, request)

    # Evaluations endpoints
    if path.startswith("/api/v1/evaluations"):
        return _handle_evaluations(method, path, request)

    # Browser endpoints
    if path.startswith("/api/v1/browser"):
        return _handle_browser(method, path, request)

    # Credentials endpoints
    if path == "/api/v1/credentials" or path.startswith("/api/v1/credentials/"):
        return _handle_credentials(method, path, request)

    # Audit stream (SSE)
    if path == "/api/v1/audit/events/stream":
        sse = 'data: {"action":"auth.success","outcome":"success"}\n\n'
        return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})

    # Providers status
    if path == "/api/v1/providers/status" and method == "GET":
        return httpx.Response(200, json={"checks": {"memory/default": "ok"}})

    # Stub endpoints → 501
    for prefix in ("/api/v1/llm",):
        if path.startswith(prefix):
            return httpx.Response(501, json={"detail": "Not implemented"})

    # Memory namespaces (before the general memory handler)
    if path == "/api/v1/memory/namespaces" and method == "GET":
        return httpx.Response(200, json={"namespaces": list(_store.keys())})

    # Memory endpoints
    if path.startswith("/api/v1/memory/"):
        return _handle_memory(method, path, request)

    return httpx.Response(404, json={"detail": "Not found"})


def _handle_a2a(method: str, path: str, request: httpx.Request) -> httpx.Response:
    """Mock handler for A2A protocol endpoints."""
    # Gateway-level agent card
    if path == "/.well-known/agent.json" and method == "GET":
        return httpx.Response(
            200,
            json={
                "name": "Test Gateway",
                "description": "Mock LLM",
                "version": "0.1.0",
                "supported_interfaces": [
                    {"url": "http://test/a2a", "protocol_binding": "http+json", "protocol_version": "0.2"}
                ],
                "capabilities": {"streaming": True},
                "skills": [
                    {"id": "test-agent", "name": "test-agent", "description": "A test agent", "tags": ["memory"]}
                ],
                "default_input_modes": ["text/plain"],
                "default_output_modes": ["text/plain"],
            },
        )

    # Per-agent card
    if path.endswith("/.well-known/agent.json") and method == "GET":
        # Extract agent name from /a2a/agents/{name}/.well-known/agent.json
        name = path.removeprefix("/a2a/agents/").removesuffix("/.well-known/agent.json")
        return httpx.Response(
            200,
            json={
                "name": name,
                "description": f"Agent: {name}",
                "version": "0.1.0",
                "supported_interfaces": [
                    {
                        "url": f"http://test/a2a/agents/{name}",
                        "protocol_binding": "http+json",
                        "protocol_version": "0.2",
                    }
                ],
                "capabilities": {"streaming": True},
                "skills": [{"id": name, "name": name, "description": f"Agent: {name}", "tags": []}],
                "default_input_modes": ["text/plain"],
                "default_output_modes": ["text/plain"],
            },
        )

    # POST /a2a/agents/{name}/message:send
    if path.endswith("/message:send") and method == "POST":
        body = json.loads(request.content)
        task_id = body.get("message", {}).get("task_id") or "task-mock"
        return httpx.Response(
            200,
            json={
                "id": task_id,
                "context_id": task_id,
                "status": {"state": "completed", "timestamp": "2025-01-01T00:00:00Z"},
                "artifacts": [{"artifact_id": "art-1", "name": "response", "parts": [{"text": "Mock A2A response"}]}],
                "metadata": {"agent_name": "test"},
            },
        )

    # POST /a2a/agents/{name}/message:stream
    if path.endswith("/message:stream") and method == "POST":
        sse = 'data: {"type":"status_update","task_id":"t1","context_id":"t1","status":{"state":"working"}}\n\n'
        sse += 'data: {"type":"message","message_id":"m1","role":"agent","parts":[{"text":"Hello"}]}\n\n'
        sse += 'data: {"type":"task","id":"t1","status":{"state":"completed"}}\n\n'
        return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})

    # GET /a2a/agents/{name}/tasks/{task_id}
    if "/tasks/" in path and method == "GET" and ":subscribe" not in path:
        task_id = path.split("/tasks/")[-1]
        return httpx.Response(
            200,
            json={
                "id": task_id,
                "context_id": task_id,
                "status": {"state": "completed", "timestamp": "2025-01-01T00:00:00Z"},
                "metadata": {},
            },
        )

    # POST /a2a/agents/{name}/tasks/{task_id}:cancel
    if ":cancel" in path and method == "POST":
        task_id = path.split("/tasks/")[-1].removesuffix(":cancel")
        return httpx.Response(
            200,
            json={
                "id": task_id,
                "context_id": task_id,
                "status": {"state": "canceled", "timestamp": "2025-01-01T00:00:00Z"},
            },
        )

    # GET /a2a/agents/{name}/tasks/{task_id}:subscribe
    if ":subscribe" in path and method == "GET":
        sse = 'data: {"type":"task","id":"t1","status":{"state":"completed"}}\n\n'
        return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})

    return httpx.Response(404, json={"detail": "Not found"})


_tools_store: dict[str, dict] = {}

_agents_store: dict[str, dict] = {}
_teams_store: dict[str, dict] = {}
_team_runs: dict[str, dict] = {}  # run_id -> {events, status}

# Version history — mirrors the versioned store's layout.  Used by the
# version/fork/lineage mock endpoints.  ``name`` and ``team_name`` keys are
# the bare name only; the tests don't exercise the qualified addressing
# via the mock transport.
_agent_versions: dict[str, list[dict]] = {}  # name -> list[version_dict]
_team_versions: dict[str, list[dict]] = {}
_agent_proposals: list[str] = []  # version_ids waiting for admin approval
_team_proposals: list[str] = []

_ci_sessions: dict[str, dict] = {}

_policy_engines: dict[str, dict] = {}
_policies: dict[str, dict[str, dict]] = {}  # engine_id -> {policy_id -> policy}
_evaluators: dict[str, dict] = {}
_policy_engine_counter = 0
_policy_counter = 0
_evaluator_counter = 0


def _make_agent_version(
    name: str,
    spec: dict,
    *,
    version_number: int,
    status: str = "deployed",
    parent_version_id: str | None = None,
    forked_from: dict | None = None,
    commit_message: str | None = None,
) -> dict:
    """Build a minimal agent-version record matching the server schema."""
    import uuid as _uuid
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    now = _dt.now(_UTC).isoformat()
    return {
        "version_id": _uuid.uuid4().hex,
        "agent_name": name,
        "owner_id": spec.get("owner_id", "noop"),
        "version_number": version_number,
        "spec": spec,
        "created_at": now,
        "created_by": spec.get("owner_id", "noop"),
        "parent_version_id": parent_version_id,
        "forked_from": forked_from,
        "status": status,
        "approved_by": None,
        "approved_at": None,
        "deployed_at": now if status == "deployed" else None,
        "commit_message": commit_message,
    }


def _handle_agents(method: str, path: str, request: httpx.Request) -> httpx.Response:
    """Mock handler for agent CRUD and chat endpoints."""
    rest = path.removeprefix("/api/v1/agents")

    # GET /tool-catalog
    if rest == "/tool-catalog" and method == "GET":
        return httpx.Response(200, json={"primitives": {"memory": [{"name": "remember", "description": "store"}]}})

    # GET /{name}/export
    if rest.endswith("/export") and method == "GET":
        name = rest.removeprefix("/").removesuffix("/export")
        if name not in _agents_store:
            return httpx.Response(404, json={"detail": f"Agent '{name}' not found"})
        return httpx.Response(
            200,
            content=f"# Exported agent: {name}\n".encode(),
            headers={"content-type": "text/x-python"},
        )

    # POST "" (create agent)
    if rest == "" and method == "POST":
        body = json.loads(request.content)
        agent = {
            "name": body["name"],
            "description": body.get("description", ""),
            "model": body["model"],
            "system_prompt": body.get("system_prompt", "You are a helpful assistant."),
            "primitives": body.get("primitives", {}),
            "hooks": body.get("hooks", {"auto_memory": True, "auto_trace": True}),
            "provider_overrides": body.get("provider_overrides", {}),
            "max_turns": body.get("max_turns", 20),
            "temperature": body.get("temperature", 1.0),
            "max_tokens": body.get("max_tokens"),
            "owner_id": body.get("owner_id", "noop"),
        }
        if agent["name"] in _agents_store:
            return httpx.Response(409, json={"detail": f"Agent '{agent['name']}' already exists"})
        _agents_store[agent["name"]] = agent
        # Seed version 1 for the versioning endpoints below.
        _agent_versions[agent["name"]] = [
            _make_agent_version(agent["name"], agent, version_number=1, commit_message="initial version")
        ]
        return httpx.Response(201, json=agent)

    # GET "" (list agents)
    if rest == "" and method == "GET":
        return httpx.Response(200, json={"agents": list(_agents_store.values())})

    # Routes with /{name}
    if rest.startswith("/"):
        parts = rest.lstrip("/").split("/")
        name = parts[0]

        # POST /{name}/chat
        if len(parts) == 2 and parts[1] == "chat" and method == "POST":
            agent = _agents_store.get(name)
            if agent is None:
                return httpx.Response(404, json={"detail": f"Agent '{name}' not found"})
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "response": f"Mock response to: {body['message']}",
                    "session_id": body.get("session_id") or "mock-session",
                    "agent_name": name,
                    "turns_used": 1,
                    "tools_called": [],
                    "metadata": {},
                },
            )

        # GET /{name}/tools
        if len(parts) == 2 and parts[1] == "tools" and method == "GET":
            agent = _agents_store.get(name)
            if agent is None:
                return httpx.Response(404, json={"detail": f"Agent '{name}' not found"})
            tools = []
            for prim, cfg in agent.get("primitives", {}).items():
                if cfg.get("enabled", True):
                    tools.append(
                        {"name": f"{prim}_tool", "description": "mock", "primitive": prim, "provider": "default"}
                    )
            return httpx.Response(200, json={"agent_name": name, "tools": tools})

        # GET /{name}/memory
        if len(parts) == 2 and parts[1] == "memory" and method == "GET":
            agent = _agents_store.get(name)
            if agent is None:
                return httpx.Response(404, json={"detail": f"Agent '{name}' not found"})
            mem = agent.get("primitives", {}).get("memory", {})
            return httpx.Response(
                200,
                json={
                    "agent_name": name,
                    "memory_enabled": mem.get("enabled", False),
                    "namespace": mem.get("namespace", ""),
                    "stores": [],
                },
            )

        # GET /{name}/sessions
        if len(parts) == 2 and parts[1] == "sessions" and method == "GET":
            agent = _agents_store.get(name)
            if agent is None:
                return httpx.Response(404, json={"detail": f"Agent '{name}' not found"})
            return httpx.Response(200, json={"agent_name": name, "sessions": []})

        # POST /{name}/sessions/cleanup
        if len(parts) == 2 and parts[1] == "sessions" and method == "POST":
            # This is the cleanup endpoint (rest is /{name}/sessions with POST)
            # Actually the cleanup is at /{name}/sessions/cleanup
            pass

        if rest == f"/{name}/sessions/cleanup" and method == "POST":
            return httpx.Response(200, json={"deleted_count": 0})

        # /{name}/sessions/{session_id}[/status|/run|/stream]
        if len(parts) >= 3 and parts[1] == "sessions":
            agent = _agents_store.get(name)
            if agent is None:
                return httpx.Response(404, json={"detail": f"Agent '{name}' not found"})
            session_id = parts[2]
            if len(parts) == 4 and parts[3] == "status" and method == "GET":
                return httpx.Response(200, json={"status": "idle"})
            if len(parts) == 4 and parts[3] == "run" and method == "DELETE":
                return httpx.Response(200, json={"status": "cancelled"})
            if len(parts) == 4 and parts[3] == "stream" and method == "GET":
                return httpx.Response(
                    200, content=b'data: {"type":"done"}\n\n', headers={"content-type": "text/event-stream"}
                )
            if len(parts) == 3 and method == "GET":
                return httpx.Response(200, json={"agent_name": name, "session_id": session_id, "messages": []})
            if len(parts) == 3 and method == "DELETE":
                return httpx.Response(200, json={"status": "deleted"})

        # Single-segment routes: GET, PUT, DELETE /{name}
        if len(parts) == 1:
            if method == "GET":
                agent = _agents_store.get(name)
                if agent is None:
                    return httpx.Response(404, json={"detail": f"Agent '{name}' not found"})
                return httpx.Response(200, json=agent)

            if method == "PUT":
                agent = _agents_store.get(name)
                if agent is None:
                    return httpx.Response(404, json={"detail": f"Agent '{name}' not found"})
                body = json.loads(request.content)
                for k, v in body.items():
                    if v is not None:
                        agent[k] = v
                return httpx.Response(200, json=agent)

            if method == "DELETE":
                if name not in _agents_store:
                    return httpx.Response(404, json={"detail": f"Agent '{name}' not found"})
                del _agents_store[name]
                _agent_versions.pop(name, None)
                return httpx.Response(200, json={"status": "deleted"})

        # GET /{name}/versions
        if len(parts) == 2 and parts[1] == "versions" and method == "GET":
            versions = _agent_versions.get(name, [])
            return httpx.Response(200, json={"versions": versions})

        # POST /{name}/versions  → create new version
        if len(parts) == 2 and parts[1] == "versions" and method == "POST":
            agent = _agents_store.get(name)
            if agent is None:
                return httpx.Response(404, json={"detail": f"Agent '{name}' not found"})
            body = json.loads(request.content)
            new_spec = {
                **agent,
                **{k: v for k, v in body.items() if v is not None and k not in {"commit_message", "parent_version_id"}},
            }
            vlist = _agent_versions.setdefault(name, [])
            parent = body.get("parent_version_id") or (vlist[-1]["version_id"] if vlist else None)
            version = _make_agent_version(
                name,
                new_spec,
                version_number=len(vlist) + 1,
                parent_version_id=parent,
                commit_message=body.get("commit_message"),
            )
            vlist.append(version)
            _agents_store[name] = new_spec
            return httpx.Response(201, json=version)

        # GET /{name}/versions/{version_id}
        if len(parts) == 3 and parts[1] == "versions" and method == "GET":
            vlist = _agent_versions.get(name, [])
            for v in vlist:
                if v["version_id"] == parts[2]:
                    return httpx.Response(200, json=v)
            return httpx.Response(404, json={"detail": f"Version '{parts[2]}' not found"})

        # POST /{name}/versions/{version_id}/{action}
        if len(parts) == 4 and parts[1] == "versions" and method == "POST":
            version_id = parts[2]
            action = parts[3]
            vlist = _agent_versions.get(name, [])
            target = next((v for v in vlist if v["version_id"] == version_id), None)
            if target is None:
                return httpx.Response(404, json={"detail": f"Version '{version_id}' not found"})
            if action == "propose":
                target["status"] = "proposed"
                if version_id not in _agent_proposals:
                    _agent_proposals.append(version_id)
                return httpx.Response(200, json=target)
            if action == "approve":
                target["approved_by"] = "admin"
                return httpx.Response(200, json=target)
            if action == "reject":
                target["status"] = "rejected"
                if version_id in _agent_proposals:
                    _agent_proposals.remove(version_id)
                return httpx.Response(200, json=target)
            if action == "deploy":
                for v in vlist:
                    if v["status"] == "deployed" and v["version_id"] != version_id:
                        v["status"] = "archived"
                target["status"] = "deployed"
                from datetime import UTC as _UTC
                from datetime import datetime as _dt

                target["deployed_at"] = _dt.now(_UTC).isoformat()
                _agents_store[name] = target["spec"]
                if version_id in _agent_proposals:
                    _agent_proposals.remove(version_id)
                return httpx.Response(200, json=target)

        # POST /{name}/fork
        if len(parts) == 2 and parts[1] == "fork" and method == "POST":
            source = _agents_store.get(name)
            if source is None:
                return httpx.Response(404, json={"detail": f"Agent '{name}' not found"})
            body = json.loads(request.content)
            target_name = body.get("target_name") or name
            if target_name in _agents_store:
                return httpx.Response(409, json={"detail": f"Agent '{target_name}' already exists"})
            source_version = _agent_versions.get(name, [{}])[-1]
            forked_spec = {**source, "name": target_name, "owner_id": "noop"}
            _agents_store[target_name] = forked_spec
            version = _make_agent_version(
                target_name,
                forked_spec,
                version_number=1,
                forked_from={
                    "name": name,
                    "owner_id": source.get("owner_id", "noop"),
                    "version_id": source_version.get("version_id", ""),
                },
                commit_message=body.get("commit_message"),
            )
            _agent_versions[target_name] = [version]
            return httpx.Response(201, json=version)

        # GET /{name}/lineage
        if len(parts) == 2 and parts[1] == "lineage" and method == "GET":
            vlist = _agent_versions.get(name, [])
            owner = vlist[0].get("owner_id") if vlist else "noop"
            nodes = [{"version": v, "children_ids": [], "forks_out": []} for v in vlist]
            deployed = next((v["version_id"] for v in vlist if v["status"] == "deployed"), None)
            deployed_map = {f"{owner}:{name}": deployed} if deployed else {}
            return httpx.Response(
                200,
                json={
                    "root_identity": {"owner_id": owner, "name": name},
                    "nodes": nodes,
                    "deployed": deployed_map,
                },
            )

    return httpx.Response(404, json={"detail": "Not found"})


def _make_team_version(
    name: str,
    spec: dict,
    *,
    version_number: int,
    status: str = "deployed",
    parent_version_id: str | None = None,
    forked_from: dict | None = None,
    commit_message: str | None = None,
) -> dict:
    """Build a minimal team-version record matching the server schema."""
    import uuid as _uuid
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    now = _dt.now(_UTC).isoformat()
    return {
        "version_id": _uuid.uuid4().hex,
        "team_name": name,
        "owner_id": spec.get("owner_id", "noop"),
        "version_number": version_number,
        "spec": spec,
        "created_at": now,
        "created_by": spec.get("owner_id", "noop"),
        "parent_version_id": parent_version_id,
        "forked_from": forked_from,
        "status": status,
        "approved_by": None,
        "approved_at": None,
        "deployed_at": now if status == "deployed" else None,
        "commit_message": commit_message,
    }


def _handle_teams(method: str, path: str, request: httpx.Request) -> httpx.Response:
    """Mock handler for team CRUD and run endpoints."""
    rest = path.removeprefix("/api/v1/teams")

    # POST "" (create team)
    if rest == "" and method == "POST":
        body = json.loads(request.content)
        team = {
            "name": body["name"],
            "description": body.get("description", ""),
            "planner": body["planner"],
            "synthesizer": body["synthesizer"],
            "workers": body["workers"],
            "max_concurrent": body.get("max_concurrent"),
            "global_max_turns": body.get("global_max_turns", 100),
            "global_timeout_seconds": body.get("global_timeout_seconds", 300),
            "owner_id": body.get("owner_id", "noop"),
        }
        if team["name"] in _teams_store:
            return httpx.Response(409, json={"detail": f"Team '{team['name']}' already exists"})
        _teams_store[team["name"]] = team
        _team_versions[team["name"]] = [
            _make_team_version(team["name"], team, version_number=1, commit_message="initial version")
        ]
        return httpx.Response(201, json=team)

    # GET "" (list teams)
    if rest == "" and method == "GET":
        return httpx.Response(200, json={"teams": list(_teams_store.values())})

    if rest.startswith("/"):
        parts = rest.lstrip("/").split("/")
        name = parts[0]

        # GET /{name}/runs
        if len(parts) == 2 and parts[1] == "runs" and method == "GET":
            if name not in _teams_store:
                return httpx.Response(404, json={"detail": f"Team '{name}' not found"})
            return httpx.Response(200, json={"team_name": name, "runs": []})

        # /{name}/runs/{run_id}[/status|/events|/cancel|/stream]
        if len(parts) >= 3 and parts[1] == "runs":
            run_id = parts[2]
            if len(parts) == 4 and parts[3] == "status" and method == "GET":
                return httpx.Response(200, json={"status": "idle"})
            if len(parts) == 4 and parts[3] == "events" and method == "GET":
                return httpx.Response(200, json={"team_run_id": run_id, "status": "unknown", "events": []})
            if len(parts) == 4 and parts[3] == "cancel" and method == "DELETE":
                return httpx.Response(200, json={"status": "cancelled"})
            if len(parts) == 4 and parts[3] == "stream" and method == "GET":
                return httpx.Response(
                    200, content=b'data: {"type":"done"}\n\n', headers={"content-type": "text/event-stream"}
                )
            if len(parts) == 3 and method == "GET":
                return httpx.Response(
                    200,
                    json={
                        "team_run_id": run_id,
                        "team_name": name,
                        "status": "idle",
                        "tasks": [],
                        "tasks_created": 0,
                        "tasks_completed": 0,
                    },
                )
            if len(parts) == 3 and method == "DELETE":
                return httpx.Response(200, json={"status": "deleted"})

        # Single-segment routes: GET, PUT, DELETE /{name}
        if len(parts) == 1:
            if method == "GET":
                team = _teams_store.get(name)
                if team is None:
                    return httpx.Response(404, json={"detail": f"Team '{name}' not found"})
                return httpx.Response(200, json=team)

            if method == "PUT":
                team = _teams_store.get(name)
                if team is None:
                    return httpx.Response(404, json={"detail": f"Team '{name}' not found"})
                body = json.loads(request.content)
                for k, v in body.items():
                    if v is not None:
                        team[k] = v
                return httpx.Response(200, json=team)

            if method == "DELETE":
                if name not in _teams_store:
                    return httpx.Response(404, json={"detail": f"Team '{name}' not found"})
                del _teams_store[name]
                _team_versions.pop(name, None)
                return httpx.Response(200, json={"status": "deleted"})

        # GET /{name}/export
        if len(parts) == 2 and parts[1] == "export" and method == "GET":
            if name not in _teams_store:
                return httpx.Response(404, json={"detail": f"Team '{name}' not found"})
            return httpx.Response(
                200,
                content=f"# Exported team: {name}\n".encode(),
                headers={"content-type": "text/x-python"},
            )

        # GET /{name}/versions
        if len(parts) == 2 and parts[1] == "versions" and method == "GET":
            versions = _team_versions.get(name, [])
            return httpx.Response(200, json={"versions": versions})

        # POST /{name}/versions
        if len(parts) == 2 and parts[1] == "versions" and method == "POST":
            team = _teams_store.get(name)
            if team is None:
                return httpx.Response(404, json={"detail": f"Team '{name}' not found"})
            body = json.loads(request.content)
            new_spec = {
                **team,
                **{k: v for k, v in body.items() if v is not None and k not in {"commit_message", "parent_version_id"}},
            }
            vlist = _team_versions.setdefault(name, [])
            parent = body.get("parent_version_id") or (vlist[-1]["version_id"] if vlist else None)
            version = _make_team_version(
                name,
                new_spec,
                version_number=len(vlist) + 1,
                parent_version_id=parent,
                commit_message=body.get("commit_message"),
            )
            vlist.append(version)
            _teams_store[name] = new_spec
            return httpx.Response(201, json=version)

        # GET /{name}/versions/{version_id}
        if len(parts) == 3 and parts[1] == "versions" and method == "GET":
            vlist = _team_versions.get(name, [])
            for v in vlist:
                if v["version_id"] == parts[2]:
                    return httpx.Response(200, json=v)
            return httpx.Response(404, json={"detail": "Version not found"})

        # POST /{name}/versions/{version_id}/{action}
        if len(parts) == 4 and parts[1] == "versions" and method == "POST":
            version_id = parts[2]
            action = parts[3]
            vlist = _team_versions.get(name, [])
            target = next((v for v in vlist if v["version_id"] == version_id), None)
            if target is None:
                return httpx.Response(404, json={"detail": "Version not found"})
            if action == "propose":
                target["status"] = "proposed"
                if version_id not in _team_proposals:
                    _team_proposals.append(version_id)
                return httpx.Response(200, json=target)
            if action == "approve":
                target["approved_by"] = "admin"
                return httpx.Response(200, json=target)
            if action == "reject":
                target["status"] = "rejected"
                if version_id in _team_proposals:
                    _team_proposals.remove(version_id)
                return httpx.Response(200, json=target)
            if action == "deploy":
                for v in vlist:
                    if v["status"] == "deployed" and v["version_id"] != version_id:
                        v["status"] = "archived"
                target["status"] = "deployed"
                from datetime import UTC as _UTC
                from datetime import datetime as _dt

                target["deployed_at"] = _dt.now(_UTC).isoformat()
                _teams_store[name] = target["spec"]
                if version_id in _team_proposals:
                    _team_proposals.remove(version_id)
                return httpx.Response(200, json=target)

        # POST /{name}/fork
        if len(parts) == 2 and parts[1] == "fork" and method == "POST":
            source = _teams_store.get(name)
            if source is None:
                return httpx.Response(404, json={"detail": f"Team '{name}' not found"})
            body = json.loads(request.content)
            target_name = body.get("target_name") or name
            if target_name in _teams_store:
                return httpx.Response(409, json={"detail": f"Team '{target_name}' already exists"})
            source_version = _team_versions.get(name, [{}])[-1]
            forked_spec = {**source, "name": target_name, "owner_id": "noop"}
            _teams_store[target_name] = forked_spec
            version = _make_team_version(
                target_name,
                forked_spec,
                version_number=1,
                forked_from={
                    "name": name,
                    "owner_id": source.get("owner_id", "noop"),
                    "version_id": source_version.get("version_id", ""),
                },
                commit_message=body.get("commit_message"),
            )
            _team_versions[target_name] = [version]
            return httpx.Response(201, json=version)

        # GET /{name}/lineage
        if len(parts) == 2 and parts[1] == "lineage" and method == "GET":
            vlist = _team_versions.get(name, [])
            owner = vlist[0].get("owner_id") if vlist else "noop"
            nodes = [{"version": v, "children_ids": [], "forks_out": []} for v in vlist]
            deployed = next((v["version_id"] for v in vlist if v["status"] == "deployed"), None)
            deployed_map = {f"{owner}:{name}": deployed} if deployed else {}
            return httpx.Response(
                200,
                json={
                    "root_identity": {"owner_id": owner, "name": name},
                    "nodes": nodes,
                    "deployed": deployed_map,
                },
            )

        # POST /{name}/runs/{team_run_id}/tasks/{task_id}/retry
        if len(parts) == 6 and parts[1] == "runs" and parts[3] == "tasks" and parts[5] == "retry" and method == "POST":
            return httpx.Response(
                200, content=b'data: {"type":"done"}\n\n', headers={"content-type": "text/event-stream"}
            )

    return httpx.Response(404, json={"detail": "Not found"})


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


def _handle_policy(method: str, path: str, request: httpx.Request) -> httpx.Response:
    """Mock handler for policy endpoints."""
    global _policy_engine_counter, _policy_counter
    rest = path.removeprefix("/api/v1/policy")

    # POST /engines (create engine)
    if rest == "/engines" and method == "POST":
        body = json.loads(request.content)
        _policy_engine_counter += 1
        engine_id = f"engine-{_policy_engine_counter}"
        engine = {
            "policy_engine_id": engine_id,
            "name": body["name"],
            "description": body.get("description", ""),
            "config": body.get("config", {}),
        }
        _policy_engines[engine_id] = engine
        _policies[engine_id] = {}
        return httpx.Response(201, json=engine)

    # GET /engines (list engines)
    if rest == "/engines" and method == "GET":
        return httpx.Response(200, json={"policy_engines": list(_policy_engines.values())})

    if rest.startswith("/engines/"):
        parts = rest.removeprefix("/engines/").split("/")
        engine_id = parts[0]

        # GET /engines/{id}
        if len(parts) == 1 and method == "GET":
            engine = _policy_engines.get(engine_id)
            if engine is None:
                return httpx.Response(404, json={"detail": "Engine not found"})
            return httpx.Response(200, json=engine)

        # DELETE /engines/{id}
        if len(parts) == 1 and method == "DELETE":
            _policy_engines.pop(engine_id, None)
            _policies.pop(engine_id, None)
            return httpx.Response(204)

        # POST /engines/{id}/policies (create policy)
        if len(parts) == 2 and parts[1] == "policies" and method == "POST":
            body = json.loads(request.content)
            _policy_counter += 1
            policy_id = f"policy-{_policy_counter}"
            policy = {
                "policy_id": policy_id,
                "policy_engine_id": engine_id,
                "definition": body["policy_body"],
                "description": body.get("description", ""),
            }
            _policies.setdefault(engine_id, {})[policy_id] = policy
            return httpx.Response(201, json=policy)

        # GET /engines/{id}/policies (list policies)
        if len(parts) == 2 and parts[1] == "policies" and method == "GET":
            engine_policies = _policies.get(engine_id, {})
            return httpx.Response(200, json={"policies": list(engine_policies.values())})

        # GET /engines/{id}/policies/{pid}
        if len(parts) == 3 and parts[1] == "policies" and method == "GET":
            policy_id = parts[2]
            policy = _policies.get(engine_id, {}).get(policy_id)
            if policy is None:
                return httpx.Response(404, json={"detail": "Policy not found"})
            return httpx.Response(200, json=policy)

        # PUT /engines/{id}/policies/{pid}
        if len(parts) == 3 and parts[1] == "policies" and method == "PUT":
            policy_id = parts[2]
            policy = _policies.get(engine_id, {}).get(policy_id)
            if policy is None:
                return httpx.Response(404, json={"detail": "Policy not found"})
            body = json.loads(request.content)
            policy["policy_body"] = body["policy_body"]
            if "description" in body:
                policy["description"] = body["description"]
            return httpx.Response(200, json=policy)

        # DELETE /engines/{id}/policies/{pid}
        if len(parts) == 3 and parts[1] == "policies" and method == "DELETE":
            policy_id = parts[2]
            _policies.get(engine_id, {}).pop(policy_id, None)
            return httpx.Response(204)

        # ── Generations ────────────────────────────────────────────────
        if len(parts) == 2 and parts[1] == "generations":
            if method == "POST":
                gid = f"gen-{len(_policy_generations) + 1}"
                generation = {"generation_id": gid, "policy_engine_id": engine_id, "status": "STARTED"}
                _policy_generations[gid] = generation
                return httpx.Response(201, json=generation)
            if method == "GET":
                return httpx.Response(200, json={"generations": list(_policy_generations.values())})

        if len(parts) == 3 and parts[1] == "generations":
            gid = parts[2]
            if method == "GET":
                gen = _policy_generations.get(gid)
                if gen is None:
                    return httpx.Response(404, json={"detail": "Generation not found"})
                return httpx.Response(200, json=gen)

        if len(parts) == 4 and parts[1] == "generations" and parts[3] == "assets":
            return httpx.Response(200, json={"assets": []})

    return httpx.Response(404, json={"detail": "Not found"})


def _handle_evaluations(method: str, path: str, request: httpx.Request) -> httpx.Response:
    """Mock handler for evaluations endpoints."""
    global _evaluator_counter
    rest = path.removeprefix("/api/v1/evaluations")

    # POST /evaluators (create evaluator)
    if rest == "/evaluators" and method == "POST":
        body = json.loads(request.content)
        _evaluator_counter += 1
        evaluator_id = f"evaluator-{_evaluator_counter}"
        evaluator = {
            "evaluator_id": evaluator_id,
            "name": body["name"],
            "evaluator_type": body["evaluator_type"],
            "config": body.get("config", {}),
            "description": body.get("description", ""),
        }
        _evaluators[evaluator_id] = evaluator
        return httpx.Response(201, json=evaluator)

    # GET /evaluators (list evaluators)
    if rest == "/evaluators" and method == "GET":
        return httpx.Response(200, json={"evaluators": list(_evaluators.values())})

    # POST /evaluate
    if rest == "/evaluate" and method == "POST":
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "evaluation_results": [{"evaluator_id": body.get("evaluator_id", ""), "value": 0.85, "label": "good"}],
            },
        )

    if rest.startswith("/evaluators/"):
        parts = rest.removeprefix("/evaluators/").split("/")
        evaluator_id = parts[0]

        # GET /evaluators/{id}
        if len(parts) == 1 and method == "GET":
            evaluator = _evaluators.get(evaluator_id)
            if evaluator is None:
                return httpx.Response(404, json={"detail": "Evaluator not found"})
            return httpx.Response(200, json=evaluator)

        # PUT /evaluators/{id}
        if len(parts) == 1 and method == "PUT":
            evaluator = _evaluators.get(evaluator_id)
            if evaluator is None:
                return httpx.Response(404, json={"detail": "Evaluator not found"})
            body = json.loads(request.content)
            for k, v in body.items():
                if v is not None:
                    evaluator[k] = v
            return httpx.Response(200, json=evaluator)

        # DELETE /evaluators/{id}
        if len(parts) == 1 and method == "DELETE":
            _evaluators.pop(evaluator_id, None)
            return httpx.Response(204)

    # ── Scores ──────────────────────────────────────────────────────────
    if rest == "/scores" and method == "POST":
        body = json.loads(request.content)
        sid = f"score-{len(_eval_scores) + 1}"
        score = {"score_id": sid, "name": body["name"], "value": body["value"], "trace_id": body.get("trace_id")}
        _eval_scores[sid] = score
        return httpx.Response(201, json=score)
    if rest == "/scores" and method == "GET":
        return httpx.Response(200, json={"scores": list(_eval_scores.values())})
    if rest.startswith("/scores/"):
        sid = rest.removeprefix("/scores/")
        if method == "GET":
            score = _eval_scores.get(sid)
            if score is None:
                return httpx.Response(404, json={"detail": "Score not found"})
            return httpx.Response(200, json=score)
        if method == "DELETE":
            _eval_scores.pop(sid, None)
            return httpx.Response(204)

    # ── Online eval configs ─────────────────────────────────────────────
    if rest == "/online-configs" and method == "POST":
        body = json.loads(request.content)
        cid = f"online-{len(_online_configs) + 1}"
        config = {"config_id": cid, "name": body["name"], "evaluator_ids": body["evaluator_ids"]}
        _online_configs[cid] = config
        return httpx.Response(201, json=config)
    if rest == "/online-configs" and method == "GET":
        return httpx.Response(200, json={"configs": list(_online_configs.values())})
    if rest.startswith("/online-configs/"):
        cid = rest.removeprefix("/online-configs/")
        if method == "GET":
            config = _online_configs.get(cid)
            if config is None:
                return httpx.Response(404, json={"detail": "Config not found"})
            return httpx.Response(200, json=config)
        if method == "DELETE":
            _online_configs.pop(cid, None)
            return httpx.Response(204)

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

    # ── Credential providers CRUD ─────────────────────────────────────
    if path == "/api/v1/identity/credential-providers" and method == "GET":
        return httpx.Response(200, json={"credential_providers": list(_credential_providers.values())})

    if path == "/api/v1/identity/credential-providers" and method == "POST":
        body = json.loads(request.content)
        provider = {"name": body["name"], "provider_type": body["provider_type"], "config": body.get("config", {})}
        _credential_providers[body["name"]] = provider
        return httpx.Response(201, json=provider)

    if path.startswith("/api/v1/identity/credential-providers/"):
        name = path.removeprefix("/api/v1/identity/credential-providers/")
        if method == "GET":
            provider = _credential_providers.get(name)
            if provider is None:
                return httpx.Response(404, json={"detail": "Not found"})
            return httpx.Response(200, json=provider)
        if method == "PUT":
            provider = _credential_providers.get(name)
            if provider is None:
                return httpx.Response(404, json={"detail": "Not found"})
            body = json.loads(request.content)
            provider["config"] = body.get("config", provider.get("config", {}))
            return httpx.Response(200, json=provider)
        if method == "DELETE":
            _credential_providers.pop(name, None)
            return httpx.Response(204)

    # ── Workload identities CRUD ──────────────────────────────────────
    if path == "/api/v1/identity/workload-identities" and method == "GET":
        return httpx.Response(200, json={"workload_identities": list(_workload_identities.values())})

    if path == "/api/v1/identity/workload-identities" and method == "POST":
        body = json.loads(request.content)
        wi = {"name": body["name"], "allowed_return_urls": body.get("allowed_return_urls", [])}
        _workload_identities[body["name"]] = wi
        return httpx.Response(201, json=wi)

    if path.startswith("/api/v1/identity/workload-identities/"):
        name = path.removeprefix("/api/v1/identity/workload-identities/")
        if method == "GET":
            wi = _workload_identities.get(name)
            if wi is None:
                return httpx.Response(404, json={"detail": "Not found"})
            return httpx.Response(200, json=wi)
        if method == "PUT":
            wi = _workload_identities.get(name)
            if wi is None:
                return httpx.Response(404, json={"detail": "Not found"})
            body = json.loads(request.content)
            wi["allowed_return_urls"] = body.get("allowed_return_urls", wi.get("allowed_return_urls", []))
            return httpx.Response(200, json=wi)
        if method == "DELETE":
            _workload_identities.pop(name, None)
            return httpx.Response(204)

    if method == "POST" and path == "/api/v1/identity/auth/complete":
        return httpx.Response(204)

    # Other identity endpoints → 501
    return httpx.Response(501, json={"detail": "Not implemented"})


_browser_sessions: dict[str, dict] = {}
_credentials_store: dict[str, str] = {}
_credential_providers: dict[str, dict] = {}
_workload_identities: dict[str, dict] = {}
_eval_scores: dict[str, dict] = {}
_online_configs: dict[str, dict] = {}
_policy_generations: dict[str, dict] = {}

_events: dict[str, dict[str, list[dict]]] = {}
_event_counter = 0


def _handle_browser(method: str, path: str, request: httpx.Request) -> httpx.Response:
    """Mock handler for browser endpoints."""
    rest = path.removeprefix("/api/v1/browser")

    if rest == "/sessions" and method == "POST":
        body = json.loads(request.content)
        sid = body.get("session_id") or "browser-mock"
        session = {
            "session_id": sid,
            "status": "active",
            "viewport": body.get("viewport", {"width": 1280, "height": 720}),
        }
        _browser_sessions[sid] = session
        return httpx.Response(201, json=session)

    if rest == "/sessions" and method == "GET":
        return httpx.Response(200, json={"sessions": list(_browser_sessions.values())})

    if rest.startswith("/sessions/"):
        parts = rest.removeprefix("/sessions/").split("/")
        session_id = parts[0]

        if len(parts) == 1 and method == "DELETE":
            _browser_sessions.pop(session_id, None)
            return httpx.Response(204)
        if len(parts) == 1 and method == "GET":
            session = _browser_sessions.get(session_id)
            if session is None:
                return httpx.Response(404, json={"detail": "Session not found"})
            return httpx.Response(200, json=session)

        if len(parts) == 2:
            action = parts[1]
            if action == "live-view" and method == "GET":
                return httpx.Response(200, json={"url": "http://live/session", "expires_in": 300})
            if action == "screenshot" and method == "GET":
                return httpx.Response(200, json={"format": "png", "data": "AAAA"})
            if action == "content" and method == "GET":
                return httpx.Response(200, json={"content": "<html></html>"})
            if action == "navigate" and method == "POST":
                return httpx.Response(200, json={"url": json.loads(request.content)["url"], "status": "ok"})
            if action == "click" and method == "POST":
                return httpx.Response(200, json={"clicked": True})
            if action == "type" and method == "POST":
                return httpx.Response(200, json={"typed": True})
            if action == "evaluate" and method == "POST":
                return httpx.Response(200, json={"result": 42})

    return httpx.Response(404, json={"detail": "Not found"})


def _handle_credentials(method: str, path: str, request: httpx.Request) -> httpx.Response:
    """Mock handler for credentials endpoints."""
    if path == "/api/v1/credentials" and method == "GET":
        masked = {k: "***" for k in _credentials_store}
        return httpx.Response(200, json={"attributes": masked})

    if path == "/api/v1/credentials" and method == "PUT":
        body = json.loads(request.content)
        _credentials_store.update(body.get("attributes") or {})
        return httpx.Response(200, json={"status": "updated"})

    if path == "/api/v1/credentials/status" and method == "GET":
        return httpx.Response(200, json={"source": "noop", "attributes": list(_credentials_store.keys())})

    if path.startswith("/api/v1/credentials/") and method == "DELETE":
        key = path.removeprefix("/api/v1/credentials/")
        _credentials_store.pop(key, None)
        return httpx.Response(200, json={"status": "deleted"})

    return httpx.Response(404, json={"detail": "Not found"})


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
    global _event_counter, _policy_engine_counter, _policy_counter, _evaluator_counter
    _store.clear()
    _events.clear()
    _tools_store.clear()
    _ci_sessions.clear()
    _agents_store.clear()
    _teams_store.clear()
    _team_runs.clear()
    _agent_versions.clear()
    _team_versions.clear()
    _agent_proposals.clear()
    _team_proposals.clear()
    _policy_engines.clear()
    _policies.clear()
    _evaluators.clear()
    _browser_sessions.clear()
    _credentials_store.clear()
    _credential_providers.clear()
    _workload_identities.clear()
    _eval_scores.clear()
    _online_configs.clear()
    _policy_generations.clear()
    _event_counter = 0
    _policy_engine_counter = 0
    _policy_counter = 0
    _evaluator_counter = 0
    yield
    _store.clear()
    _events.clear()
    _tools_store.clear()
    _ci_sessions.clear()
    _agents_store.clear()
    _teams_store.clear()
    _team_runs.clear()
    _agent_versions.clear()
    _team_versions.clear()
    _agent_proposals.clear()
    _team_proposals.clear()
    _policy_engines.clear()
    _policies.clear()
    _evaluators.clear()
    _browser_sessions.clear()
    _credentials_store.clear()
    _credential_providers.clear()
    _workload_identities.clear()
    _eval_scores.clear()
    _online_configs.clear()
    _policy_generations.clear()
    _event_counter = 0
    _policy_engine_counter = 0
    _policy_counter = 0
    _evaluator_counter = 0


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
