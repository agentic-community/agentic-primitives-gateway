"""Tests for A2A (Agent-to-Agent) protocol routes."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import set_authenticated_principal
from agentic_primitives_gateway.models.agents import AgentSpec, ChatResponse, PrimitiveConfig
from agentic_primitives_gateway.routes import a2a as a2a_module

# ── Helpers ───────────────────────────────────────────────────────────

ADMIN_PRINCIPAL = AuthenticatedPrincipal(id="admin", type="user", scopes=frozenset({"admin"}))


def _make_spec(
    name: str = "test-agent",
    description: str = "A test agent",
    shared_with: list[str] | None = None,
    owner_id: str = "admin",
    primitives: dict[str, PrimitiveConfig] | None = None,
) -> AgentSpec:
    return AgentSpec(
        name=name,
        model="test-model",
        description=description,
        owner_id=owner_id,
        shared_with=shared_with if shared_with is not None else ["*"],
        primitives=primitives or {},
    )


def _chat_response(response: str = "Hello from agent", session_id: str = "sess-1") -> ChatResponse:
    return ChatResponse(
        response=response,
        session_id=session_id,
        agent_name="test-agent",
        turns_used=1,
        tools_called=["remember"],
    )


def _set_admin_principal() -> None:
    set_authenticated_principal(ADMIN_PRINCIPAL)


@pytest.fixture(autouse=True)
def _auth() -> None:
    """Set an admin principal for all tests by default."""
    _set_admin_principal()


@pytest.fixture()
def mock_store() -> AsyncMock:
    store = AsyncMock()
    return store


@pytest.fixture()
def mock_runner() -> AsyncMock:
    runner = AsyncMock()
    return runner


@pytest.fixture()
def mock_bg() -> MagicMock:
    bg = MagicMock()
    bg.get_status_async = AsyncMock(return_value="idle")
    bg.get_events_async = AsyncMock(return_value=[])
    bg.get_owner_async = AsyncMock(return_value=None)
    bg.cancel = AsyncMock(return_value=True)
    return bg


@pytest.fixture(autouse=True)
def _inject_mocks(mock_store: AsyncMock, mock_runner: AsyncMock, mock_bg: MagicMock) -> Any:
    """Inject mock store, runner, and bg into the a2a module for every test."""
    original_store = a2a_module._store
    original_runner = a2a_module._runner

    a2a_module._store = mock_store
    a2a_module._runner = mock_runner

    with patch("agentic_primitives_gateway.routes.agents._bg", mock_bg):
        yield

    a2a_module._store = original_store
    a2a_module._runner = original_runner


# ── GET /.well-known/agent.json ──────────────────────────────────────


class TestGatewayAgentCard:
    @pytest.mark.asyncio
    async def test_returns_valid_card_with_skills(self, mock_store: AsyncMock) -> None:
        """Gateway card includes skills from public agents."""
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        agents = [
            _make_spec("agent-a", "Agent A", shared_with=["*"]),
            _make_spec("agent-b", "Agent B", shared_with=["*"]),
            _make_spec("private-agent", "Private", shared_with=[]),
        ]
        mock_store.list = AsyncMock(return_value=agents)

        client = TestClient(app)
        resp = client.get("/.well-known/agent.json")
        assert resp.status_code == 200

        data = resp.json()
        assert data["name"] == "Agentic Primitives Gateway"
        assert data["capabilities"]["streaming"] is True
        assert data["capabilities"]["push_notifications"] is False

        skill_ids = [s["id"] for s in data["skills"]]
        assert "agent-a" in skill_ids
        assert "agent-b" in skill_ids
        # Private agent should NOT appear as a skill
        assert "private-agent" not in skill_ids

    @pytest.mark.asyncio
    async def test_no_public_agents_returns_empty_skills(self, mock_store: AsyncMock) -> None:
        """Gateway card with no public agents has empty skills."""
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        mock_store.list = AsyncMock(return_value=[])

        client = TestClient(app)
        resp = client.get("/.well-known/agent.json")
        assert resp.status_code == 200

        data = resp.json()
        assert data["skills"] == []
        assert data["supported_interfaces"][0]["protocol_binding"] == "http+json"

    @pytest.mark.asyncio
    async def test_security_scheme_noop(self, mock_store: AsyncMock) -> None:
        """Noop auth backend produces no security schemes."""
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        mock_store.list = AsyncMock(return_value=[])

        with patch.object(a2a_module.settings.auth, "backend", "noop"):
            client = TestClient(app)
            resp = client.get("/.well-known/agent.json")

        data = resp.json()
        assert data["security_schemes"] is None
        assert data["security_requirements"] is None

    @pytest.mark.asyncio
    async def test_security_scheme_api_key(self, mock_store: AsyncMock) -> None:
        """api_key auth backend produces apiKey security scheme."""
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        mock_store.list = AsyncMock(return_value=[])

        with patch.object(a2a_module.settings.auth, "backend", "api_key"):
            client = TestClient(app)
            resp = client.get("/.well-known/agent.json")

        data = resp.json()
        assert data["security_schemes"] is not None
        assert "apiKey" in data["security_schemes"]
        assert data["security_schemes"]["apiKey"]["type"] == "apiKey"
        assert data["security_requirements"] == [{"apiKey": []}]

    @pytest.mark.asyncio
    async def test_security_scheme_jwt(self, mock_store: AsyncMock) -> None:
        """jwt auth backend produces openIdConnect security scheme."""
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        mock_store.list = AsyncMock(return_value=[])

        with (
            patch.object(a2a_module.settings.auth, "backend", "jwt"),
            patch.object(a2a_module.settings.auth, "jwt", {"jwks_url": "https://example.com/.well-known/jwks.json"}),
        ):
            client = TestClient(app)
            resp = client.get("/.well-known/agent.json")

        data = resp.json()
        assert data["security_schemes"] is not None
        assert "openIdConnect" in data["security_schemes"]
        scheme = data["security_schemes"]["openIdConnect"]
        assert scheme["type"] == "openIdConnect"
        assert scheme["open_id_connect_url"] == "https://example.com/.well-known/jwks.json"
        assert data["security_requirements"] == [{"openIdConnect": []}]


# ── GET /a2a/agents/{name}/.well-known/agent.json ───────────────────


class TestPerAgentCard:
    @pytest.mark.asyncio
    async def test_returns_card_for_existing_public_agent(self, mock_store: AsyncMock) -> None:
        """Per-agent card for an existing public agent."""
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        spec = _make_spec(
            "my-agent",
            "My agent",
            shared_with=["*"],
            primitives={"memory": PrimitiveConfig(enabled=True)},
        )
        mock_store.get = AsyncMock(return_value=spec)

        client = TestClient(app)
        resp = client.get("/a2a/agents/my-agent/.well-known/agent.json")
        assert resp.status_code == 200

        data = resp.json()
        assert data["name"] == "my-agent"
        assert data["description"] == "My agent"
        assert len(data["skills"]) == 1
        assert data["skills"][0]["id"] == "my-agent"
        assert "memory" in data["skills"][0]["tags"]

    @pytest.mark.asyncio
    async def test_404_for_missing_agent(self, mock_store: AsyncMock) -> None:
        """Per-agent card returns 404 for nonexistent agent."""
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        mock_store.get = AsyncMock(return_value=None)

        client = TestClient(app)
        resp = client.get("/a2a/agents/nonexistent/.well-known/agent.json")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_public_agent_no_auth_required(self, mock_store: AsyncMock) -> None:
        """Public agents (shared_with=['*']) are discoverable without strict access checks."""
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        spec = _make_spec("public-bot", "Public bot", shared_with=["*"])
        mock_store.get = AsyncMock(return_value=spec)

        client = TestClient(app)
        resp = client.get("/a2a/agents/public-bot/.well-known/agent.json")
        assert resp.status_code == 200
        assert resp.json()["name"] == "public-bot"

    @pytest.mark.asyncio
    async def test_private_agent_returns_403_for_anonymous(self, mock_store: AsyncMock) -> None:
        """Private agent card returns 403 for anonymous users.

        The ``.well-known/agent.json`` paths are auth-exempt, so the auth
        middleware sets ``ANONYMOUS_PRINCIPAL`` (not an admin). Private agents
        therefore reject unauthenticated discovery.
        """
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        spec = _make_spec("private-bot", "Private bot", shared_with=[], owner_id="alice")
        mock_store.get = AsyncMock(return_value=spec)

        client = TestClient(app)
        resp = client.get("/a2a/agents/private-bot/.well-known/agent.json")
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_skill_uses_description_fallback(self, mock_store: AsyncMock) -> None:
        """Skill description falls back to 'Agent: {name}' if no description."""
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        spec = _make_spec("no-desc", shared_with=["*"])
        spec = spec.model_copy(update={"description": None})
        mock_store.get = AsyncMock(return_value=spec)

        client = TestClient(app)
        resp = client.get("/a2a/agents/no-desc/.well-known/agent.json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["skills"][0]["description"] == "Agent: no-desc"


# ── POST /a2a/agents/{name}/message:send ─────────────────────────────


class TestSendMessage:
    def _build_request(self, text: str = "Hello", task_id: str | None = None) -> dict[str, Any]:
        msg: dict[str, Any] = {
            "message_id": str(uuid.uuid4()),
            "role": "user",
            "parts": [{"text": text}],
        }
        if task_id:
            msg["task_id"] = task_id
        return {"message": msg}

    @pytest.mark.asyncio
    async def test_sync_send_returns_completed_task(self, mock_store: AsyncMock, mock_runner: AsyncMock) -> None:
        """Sync send returns a completed A2A task with an artifact."""
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        spec = _make_spec("test-agent", shared_with=["*"])
        mock_store.get = AsyncMock(return_value=spec)
        mock_runner.run = AsyncMock(return_value=_chat_response("Agent says hi"))

        client = TestClient(app)
        resp = client.post("/a2a/agents/test-agent/message:send", json=self._build_request("Hi"))
        assert resp.status_code == 200

        data = resp.json()
        assert data["status"]["state"] == "completed"
        assert data["artifacts"] is not None
        assert len(data["artifacts"]) == 1
        assert data["artifacts"][0]["parts"][0]["text"] == "Agent says hi"
        assert data["artifacts"][0]["name"] == "response"
        assert len(data["history"]) == 2
        assert data["history"][0]["role"] == "user"
        assert data["history"][1]["role"] == "agent"
        assert data["metadata"]["agent_name"] == "test-agent"

    @pytest.mark.asyncio
    async def test_send_with_custom_task_id(self, mock_store: AsyncMock, mock_runner: AsyncMock) -> None:
        """Custom task_id in the message is used as the A2A task id."""
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        spec = _make_spec("test-agent", shared_with=["*"])
        mock_store.get = AsyncMock(return_value=spec)
        mock_runner.run = AsyncMock(return_value=_chat_response())

        client = TestClient(app)
        resp = client.post(
            "/a2a/agents/test-agent/message:send",
            json=self._build_request("Hi", task_id="custom-task-123"),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "custom-task-123"
        assert data["context_id"] == "custom-task-123"

    @pytest.mark.asyncio
    async def test_send_404_for_missing_agent(self, mock_store: AsyncMock) -> None:
        """Send message returns 404 for nonexistent agent."""
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        mock_store.get = AsyncMock(return_value=None)

        client = TestClient(app)
        resp = client.post("/a2a/agents/nonexistent/message:send", json=self._build_request())
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_send_runner_error_returns_failed_task(self, mock_store: AsyncMock, mock_runner: AsyncMock) -> None:
        """When runner.run raises, return a failed A2A task."""
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        spec = _make_spec("test-agent", shared_with=["*"])
        mock_store.get = AsyncMock(return_value=spec)
        mock_runner.run = AsyncMock(side_effect=RuntimeError("LLM unavailable"))

        client = TestClient(app)
        resp = client.post("/a2a/agents/test-agent/message:send", json=self._build_request())
        assert resp.status_code == 200

        data = resp.json()
        assert data["status"]["state"] == "failed"
        assert "LLM unavailable" in data["status"]["message"]["parts"][0]["text"]

    @pytest.mark.asyncio
    async def test_send_extracts_data_parts(self, mock_store: AsyncMock, mock_runner: AsyncMock) -> None:
        """Data parts are extracted and passed as JSON string to the runner."""
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        spec = _make_spec("test-agent", shared_with=["*"])
        mock_store.get = AsyncMock(return_value=spec)
        mock_runner.run = AsyncMock(return_value=_chat_response())

        request_body = {
            "message": {
                "message_id": str(uuid.uuid4()),
                "role": "user",
                "parts": [{"data": {"key": "value"}, "media_type": "application/json"}],
            }
        }

        client = TestClient(app)
        resp = client.post("/a2a/agents/test-agent/message:send", json=request_body)
        assert resp.status_code == 200

        # Verify the runner received the JSON-serialized data
        call_kwargs = mock_runner.run.call_args.kwargs
        assert '"key"' in call_kwargs["message"]
        assert '"value"' in call_kwargs["message"]


# ── POST /a2a/agents/{name}/message:stream ───────────────────────────


class TestSendMessageStream:
    def _build_request(self, text: str = "Hello") -> dict[str, Any]:
        return {
            "message": {
                "message_id": str(uuid.uuid4()),
                "role": "user",
                "parts": [{"text": text}],
            }
        }

    @pytest.mark.asyncio
    async def test_streaming_returns_sse_events(self, mock_store: AsyncMock, mock_runner: AsyncMock) -> None:
        """Streaming endpoint returns SSE with status_update, message, artifact_update, and task."""
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        spec = _make_spec("test-agent", shared_with=["*"])
        mock_store.get = AsyncMock(return_value=spec)

        async def fake_stream(**kwargs: Any) -> Any:
            yield {"type": "token", "content": "Hello "}
            yield {"type": "token", "content": "world"}
            yield {"type": "done", "response": "Hello world", "session_id": "s1"}

        mock_runner.run_stream = MagicMock(return_value=fake_stream())

        client = TestClient(app)
        resp = client.post("/a2a/agents/test-agent/message:stream", json=self._build_request())
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

        body = resp.text
        # Should contain status_update (initial "working" state)
        assert '"status_update"' in body or '"type": "status_update"' in body
        # Should contain message events for tokens
        assert '"type": "message"' in body or '"type":"message"' in body
        # Should contain artifact_update
        assert '"artifact_update"' in body
        # Should contain final task
        assert '"type": "task"' in body or '"type":"task"' in body

    @pytest.mark.asyncio
    async def test_streaming_error_returns_failed_state(self, mock_store: AsyncMock, mock_runner: AsyncMock) -> None:
        """Streaming endpoint handles runner errors gracefully."""
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        spec = _make_spec("test-agent", shared_with=["*"])
        mock_store.get = AsyncMock(return_value=spec)

        async def failing_stream(**kwargs: Any) -> Any:
            raise RuntimeError("Stream exploded")
            yield  # make it an async generator

        mock_runner.run_stream = MagicMock(return_value=failing_stream())

        client = TestClient(app)
        resp = client.post("/a2a/agents/test-agent/message:stream", json=self._build_request())
        assert resp.status_code == 200

        body = resp.text
        # Should contain the final task with failed state
        assert '"failed"' in body

    @pytest.mark.asyncio
    async def test_streaming_404_for_missing_agent(self, mock_store: AsyncMock) -> None:
        """Streaming returns 404 for nonexistent agent."""
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        mock_store.get = AsyncMock(return_value=None)

        client = TestClient(app)
        resp = client.post("/a2a/agents/nonexistent/message:stream", json=self._build_request())
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_streaming_tool_call_events(self, mock_store: AsyncMock, mock_runner: AsyncMock) -> None:
        """Tool call events are translated to A2A message events."""
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        spec = _make_spec("test-agent", shared_with=["*"])
        mock_store.get = AsyncMock(return_value=spec)

        async def tool_stream(**kwargs: Any) -> Any:
            yield {"type": "tool_call_start", "tool_name": "remember", "call_id": "c1"}
            yield {"type": "tool_call_result", "tool_name": "remember", "result": "stored", "call_id": "c1"}
            yield {"type": "done", "response": "Done", "session_id": "s1"}

        mock_runner.run_stream = MagicMock(return_value=tool_stream())

        client = TestClient(app)
        resp = client.post("/a2a/agents/test-agent/message:stream", json=self._build_request())
        assert resp.status_code == 200

        body = resp.text
        assert "remember" in body
        assert "tool_call_start" in body or "tool_call_result" in body


# ── GET /a2a/agents/{name}/tasks/{task_id} ───────────────────────────


class TestGetTask:
    @pytest.mark.asyncio
    async def test_get_running_task(self, mock_store: AsyncMock, mock_bg: MagicMock) -> None:
        """Get task returns working state when run is active."""
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        spec = _make_spec("test-agent", shared_with=["*"])
        mock_store.get = AsyncMock(return_value=spec)
        mock_bg.get_status_async = AsyncMock(return_value="running")
        mock_bg.get_events_async = AsyncMock(return_value=[{"type": "token", "content": "partial"}])

        client = TestClient(app)
        resp = client.get("/a2a/agents/test-agent/tasks/task-123")
        assert resp.status_code == 200

        data = resp.json()
        assert data["id"] == "task-123"
        assert data["status"]["state"] == "working"
        assert data["metadata"]["agent_name"] == "test-agent"

    @pytest.mark.asyncio
    async def test_get_completed_task(self, mock_store: AsyncMock, mock_bg: MagicMock) -> None:
        """Completed task includes artifacts with accumulated text."""
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        spec = _make_spec("test-agent", shared_with=["*"])
        mock_store.get = AsyncMock(return_value=spec)
        mock_bg.get_status_async = AsyncMock(return_value="idle")
        mock_bg.get_events_async = AsyncMock(
            return_value=[
                {"type": "token", "content": "Hello "},
                {"type": "token", "content": "world"},
                {"type": "done", "response": "Hello world"},
            ]
        )

        client = TestClient(app)
        resp = client.get("/a2a/agents/test-agent/tasks/task-456")
        assert resp.status_code == 200

        data = resp.json()
        assert data["status"]["state"] == "completed"
        assert data["artifacts"] is not None
        assert len(data["artifacts"]) == 1
        assert data["artifacts"][0]["parts"][0]["text"] == "Hello world"

    @pytest.mark.asyncio
    async def test_get_submitted_task_no_events(self, mock_store: AsyncMock, mock_bg: MagicMock) -> None:
        """Task with no events returns submitted state."""
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        spec = _make_spec("test-agent", shared_with=["*"])
        mock_store.get = AsyncMock(return_value=spec)
        mock_bg.get_status_async = AsyncMock(return_value="idle")
        mock_bg.get_events_async = AsyncMock(return_value=[])

        client = TestClient(app)
        resp = client.get("/a2a/agents/test-agent/tasks/task-789")
        assert resp.status_code == 200

        data = resp.json()
        assert data["status"]["state"] == "submitted"
        assert data["artifacts"] is None

    @pytest.mark.asyncio
    async def test_get_failed_task(self, mock_store: AsyncMock, mock_bg: MagicMock) -> None:
        """Task with error event returns failed state."""
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        spec = _make_spec("test-agent", shared_with=["*"])
        mock_store.get = AsyncMock(return_value=spec)
        mock_bg.get_status_async = AsyncMock(return_value="idle")
        mock_bg.get_events_async = AsyncMock(return_value=[{"type": "error", "detail": "Something broke"}])

        client = TestClient(app)
        resp = client.get("/a2a/agents/test-agent/tasks/task-err")
        assert resp.status_code == 200

        data = resp.json()
        assert data["status"]["state"] == "failed"

    @pytest.mark.asyncio
    async def test_get_task_404_for_missing_agent(self, mock_store: AsyncMock) -> None:
        """Get task returns 404 when agent does not exist."""
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        mock_store.get = AsyncMock(return_value=None)

        client = TestClient(app)
        resp = client.get("/a2a/agents/nonexistent/tasks/task-123")
        assert resp.status_code == 404


# ── POST /a2a/agents/{name}/tasks/{task_id}:cancel ──────────────────


class TestCancelTask:
    @pytest.mark.asyncio
    async def test_cancel_running_task(self, mock_store: AsyncMock, mock_bg: MagicMock) -> None:
        """Cancel returns canceled state when task is successfully cancelled."""
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        spec = _make_spec("test-agent", shared_with=["*"])
        mock_store.get = AsyncMock(return_value=spec)
        mock_bg.cancel = AsyncMock(return_value=True)
        mock_bg.get_owner_async = AsyncMock(return_value=None)

        client = TestClient(app)
        resp = client.post("/a2a/agents/test-agent/tasks/task-123:cancel")
        assert resp.status_code == 200

        data = resp.json()
        assert data["id"] == "task-123"
        assert data["status"]["state"] == "canceled"

    @pytest.mark.asyncio
    async def test_cancel_already_completed(self, mock_store: AsyncMock, mock_bg: MagicMock) -> None:
        """Cancel of an already-completed task returns completed state."""
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        spec = _make_spec("test-agent", shared_with=["*"])
        mock_store.get = AsyncMock(return_value=spec)
        mock_bg.cancel = AsyncMock(return_value=False)
        mock_bg.get_owner_async = AsyncMock(return_value=None)

        client = TestClient(app)
        resp = client.post("/a2a/agents/test-agent/tasks/task-done:cancel")
        assert resp.status_code == 200

        data = resp.json()
        assert data["status"]["state"] == "completed"

    @pytest.mark.asyncio
    async def test_cancel_404_for_missing_agent(self, mock_store: AsyncMock) -> None:
        """Cancel returns 404 for nonexistent agent."""
        from starlette.testclient import TestClient

        from agentic_primitives_gateway.main import app

        mock_store.get = AsyncMock(return_value=None)

        client = TestClient(app)
        resp = client.post("/a2a/agents/nonexistent/tasks/task-123:cancel")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_cancel_forbidden_for_non_owner(self, mock_store: AsyncMock, mock_bg: MagicMock) -> None:
        """Cancel returns 403 when non-owner, non-admin tries to cancel.

        Calls the route function directly (bypassing the noop auth middleware
        which always sets an admin principal) so we can test with a non-admin
        principal.
        """
        from fastapi import HTTPException

        spec = _make_spec("test-agent", shared_with=["*"])
        mock_store.get = AsyncMock(return_value=spec)
        mock_bg.get_owner_async = AsyncMock(return_value="other-user")

        # Set a non-admin, non-owner principal directly in the contextvar
        set_authenticated_principal(AuthenticatedPrincipal(id="not-the-owner", type="user", scopes=frozenset()))

        with patch("agentic_primitives_gateway.routes.agents._bg", mock_bg), pytest.raises(HTTPException, match="403"):
            await a2a_module.cancel_task(name="test-agent", task_id="task-123")


# ── GET /a2a/agents/{name}/tasks/{task_id}:subscribe ─────────────────


class TestSubscribeTask:
    """Tests for task subscription SSE endpoint.

    NOTE: The ``GET /a2a/agents/{name}/tasks/{task_id}:subscribe`` route has a
    routing conflict with ``GET /a2a/agents/{name}/tasks/{task_id}`` because
    Starlette's ``{task_id}`` path parameter greedily captures ``task-123:subscribe``.
    As a result, HTTP-level tests via ``TestClient`` hit the wrong route.  These
    tests therefore call the route function directly to validate the logic.
    """

    @pytest.mark.asyncio
    async def test_subscribe_returns_sse(self, mock_store: AsyncMock, mock_bg: MagicMock) -> None:
        """Subscribe returns a StreamingResponse with SSE events."""
        from starlette.responses import StreamingResponse

        spec = _make_spec("test-agent", shared_with=["*"])
        mock_store.get = AsyncMock(return_value=spec)

        # Return events that include a "done" event so the stream ends
        mock_bg.get_events_async = AsyncMock(
            return_value=[
                {"type": "token", "content": "Hi"},
                {"type": "done", "response": "Hi"},
            ]
        )
        mock_bg.get_status_async = AsyncMock(return_value="idle")

        with patch("agentic_primitives_gateway.routes.agents._bg", mock_bg):
            result = await a2a_module.subscribe_task(name="test-agent", task_id="task-123")

        assert isinstance(result, StreamingResponse)
        assert result.media_type == "text/event-stream"

        # Consume the async generator to verify it yields SSE data
        body_parts: list[str] = []
        async for chunk in result.body_iterator:
            body_parts.append(chunk if isinstance(chunk, str) else chunk.decode())
        body = "".join(body_parts)
        assert "data:" in body

    @pytest.mark.asyncio
    async def test_subscribe_404_for_missing_agent(self, mock_store: AsyncMock) -> None:
        """Subscribe raises 404 when agent does not exist."""
        from fastapi import HTTPException

        mock_store.get = AsyncMock(return_value=None)

        with pytest.raises(HTTPException, match="404"):
            await a2a_module.subscribe_task(name="nonexistent", task_id="task-123")

    @pytest.mark.asyncio
    async def test_subscribe_cancelled_task_exits(self, mock_store: AsyncMock, mock_bg: MagicMock) -> None:
        """Subscribe stream terminates when task status is cancelled."""
        from starlette.responses import StreamingResponse

        spec = _make_spec("test-agent", shared_with=["*"])
        mock_store.get = AsyncMock(return_value=spec)
        mock_bg.get_events_async = AsyncMock(return_value=[])
        mock_bg.get_status_async = AsyncMock(return_value="cancelled")

        with patch("agentic_primitives_gateway.routes.agents._bg", mock_bg):
            result = await a2a_module.subscribe_task(name="test-agent", task_id="task-cancel")

        assert isinstance(result, StreamingResponse)

        # Consume the stream — it should exit quickly without yielding events
        body_parts: list[str] = []
        async for chunk in result.body_iterator:
            body_parts.append(chunk if isinstance(chunk, str) else chunk.decode())
        body = "".join(body_parts)
        # No meaningful A2A events since there were no events and status was cancelled
        assert body == "" or "data:" not in body or len(body_parts) == 0


# ── Unit tests for helper functions ──────────────────────────────────


class TestTranslateStreamEvent:
    """Test _translate_stream_event directly."""

    def test_token_event(self) -> None:
        result = a2a_module._translate_stream_event({"type": "token", "content": "hello"}, "t1", "c1")
        assert result is not None
        assert result["type"] == "message"
        assert result["role"] == "agent"
        assert result["parts"][0]["text"] == "hello"

    def test_sub_agent_token_event(self) -> None:
        result = a2a_module._translate_stream_event(
            {"type": "sub_agent_token", "content": "sub-hello", "agent_name": "helper"},
            "t1",
            "c1",
        )
        assert result is not None
        assert result["type"] == "message"
        assert result["metadata"]["sub_agent"] == "helper"

    def test_tool_call_start_event(self) -> None:
        result = a2a_module._translate_stream_event({"type": "tool_call_start", "tool_name": "remember"}, "t1", "c1")
        assert result is not None
        assert result["type"] == "message"
        assert result["parts"][0]["data"]["tool_name"] == "remember"
        assert result["metadata"]["event_type"] == "tool_call_start"

    def test_tool_call_result_event(self) -> None:
        result = a2a_module._translate_stream_event(
            {"type": "tool_call_result", "tool_name": "recall", "result": "found it"},
            "t1",
            "c1",
        )
        assert result is not None
        assert result["parts"][0]["data"]["result"] == "found it"

    def test_done_event(self) -> None:
        result = a2a_module._translate_stream_event({"type": "done", "response": "all done"}, "t1", "c1")
        assert result is not None
        assert result["type"] == "artifact_update"
        assert result["artifact"]["parts"][0]["text"] == "all done"
        assert result["last_chunk"] is True

    def test_error_event(self) -> None:
        result = a2a_module._translate_stream_event({"type": "error", "detail": "boom"}, "t1", "c1")
        assert result is not None
        assert result["type"] == "status_update"
        assert result["status"]["state"] == "failed"
        assert result["status"]["message"]["parts"][0]["text"] == "boom"

    def test_cancelled_event(self) -> None:
        result = a2a_module._translate_stream_event({"type": "cancelled"}, "t1", "c1")
        assert result is not None
        assert result["type"] == "status_update"
        assert result["status"]["state"] == "canceled"

    def test_unknown_event_returns_none(self) -> None:
        result = a2a_module._translate_stream_event({"type": "unknown_event"}, "t1", "c1")
        assert result is None


class TestExtractText:
    """Test _extract_text helper."""

    def test_text_parts(self) -> None:
        from agentic_primitives_gateway.models.a2a import A2AMessage, A2APart

        msg = A2AMessage(
            message_id="m1",
            role="user",
            parts=[A2APart(text="hello"), A2APart(text="world")],
        )
        result = a2a_module._extract_text(msg)
        assert result == "hello\nworld"

    def test_data_parts(self) -> None:
        from agentic_primitives_gateway.models.a2a import A2AMessage, A2APart

        msg = A2AMessage(
            message_id="m1",
            role="user",
            parts=[A2APart(data={"key": "value"})],
        )
        result = a2a_module._extract_text(msg)
        assert '"key"' in result
        assert '"value"' in result

    def test_empty_parts(self) -> None:
        from agentic_primitives_gateway.models.a2a import A2AMessage, A2APart

        msg = A2AMessage(
            message_id="m1",
            role="user",
            parts=[A2APart()],
        )
        result = a2a_module._extract_text(msg)
        assert result == ""


class TestBuildSkill:
    """Test _build_skill helper."""

    def test_skill_from_agent_with_primitives(self) -> None:
        spec = _make_spec(
            "my-agent",
            "My agent",
            primitives={
                "memory": PrimitiveConfig(enabled=True),
                "browser": PrimitiveConfig(enabled=True),
            },
        )
        skill = a2a_module._build_skill(spec)
        assert skill.id == "my-agent"
        assert skill.name == "my-agent"
        assert skill.description == "My agent"
        assert skill.tags == ["browser", "memory"]  # sorted

    def test_skill_description_fallback(self) -> None:
        spec = _make_spec("bot")
        spec = spec.model_copy(update={"description": None})
        skill = a2a_module._build_skill(spec)
        assert skill.description == "Agent: bot"

    def test_skill_no_primitives(self) -> None:
        spec = _make_spec("bot", primitives={})
        skill = a2a_module._build_skill(spec)
        assert skill.tags == []
