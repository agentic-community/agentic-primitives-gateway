from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from agentic_primitives_gateway.enforcement.middleware import (
    PolicyEnforcementMiddleware,
    _resolve_action,
    _resolve_principal,
    _resolve_resource,
)


class TestResolveAction:
    """Tests for the _resolve_action mapping function."""

    def test_memory_store(self):
        assert _resolve_action("POST", "/api/v1/memory/my-ns") == "memory:store"

    def test_memory_search(self):
        assert _resolve_action("POST", "/api/v1/memory/my-ns/search") == "memory:search"

    def test_memory_recall(self):
        assert _resolve_action("GET", "/api/v1/memory/my-ns/key1") == "memory:recall"

    def test_memory_list(self):
        assert _resolve_action("GET", "/api/v1/memory/my-ns") == "memory:list"

    def test_memory_delete(self):
        assert _resolve_action("DELETE", "/api/v1/memory/my-ns/key1") == "memory:delete"

    def test_gateway_completions(self):
        assert _resolve_action("POST", "/api/v1/gateway/completions") == "gateway:completions"

    def test_gateway_models(self):
        assert _resolve_action("GET", "/api/v1/gateway/models") == "gateway:models"

    def test_tools_invoke(self):
        assert _resolve_action("POST", "/api/v1/tools/my-tool/invoke") == "tools:invoke"

    def test_tools_list(self):
        assert _resolve_action("GET", "/api/v1/tools") == "tools:list"

    def test_tools_register(self):
        assert _resolve_action("POST", "/api/v1/tools") == "tools:register"

    def test_tools_search(self):
        assert _resolve_action("GET", "/api/v1/tools/search") == "tools:search"

    def test_code_interpreter_execute(self):
        assert _resolve_action("POST", "/api/v1/code-interpreter/sessions/sess-1/execute") == "code_interpreter:execute"

    def test_code_interpreter_create_session(self):
        assert _resolve_action("POST", "/api/v1/code-interpreter/sessions") == "code_interpreter:create_session"

    def test_browser_navigate(self):
        assert _resolve_action("POST", "/api/v1/browser/sessions/s1/navigate") == "browser:navigate"

    def test_browser_create_session(self):
        assert _resolve_action("POST", "/api/v1/browser/sessions") == "browser:create_session"

    def test_observability_flush(self):
        assert _resolve_action("POST", "/api/v1/observability/flush") == "observability:flush"

    def test_evaluations_evaluate(self):
        assert _resolve_action("POST", "/api/v1/evaluations/evaluate") == "evaluations:evaluate"

    def test_agents_chat(self):
        assert _resolve_action("POST", "/api/v1/agents/my-agent/chat") == "agents:chat"

    def test_agents_list(self):
        assert _resolve_action("GET", "/api/v1/agents") == "agents:list"

    def test_agents_create(self):
        assert _resolve_action("POST", "/api/v1/agents") == "agents:create"

    def test_unknown_route_returns_none(self):
        assert _resolve_action("GET", "/some/unknown/path") is None

    def test_identity_token(self):
        assert _resolve_action("POST", "/api/v1/identity/token") == "identity:token"


def _make_request(headers: dict[str, str]) -> MagicMock:
    """Create a mock request with dict-based headers."""
    request = MagicMock()
    request.headers = headers
    return request


class TestResolvePrincipal:
    """Tests for the _resolve_principal function."""

    def test_agent_id_header(self):
        request = _make_request({"x-agent-id": "my-agent"})
        assert _resolve_principal(request) == 'Agent::"my-agent"'

    def test_service_credential_fallback(self):
        request = _make_request({"x-cred-langfuse-public-key": "pk-xxx"})
        assert _resolve_principal(request) == 'Service::"langfuse"'

    def test_aws_key_fallback(self):
        request = _make_request({"x-aws-access-key-id": "AKIAEXAMPLE"})
        assert _resolve_principal(request) == 'AWSPrincipal::"AKIAEXAMPLE"'

    def test_anonymous_fallback(self):
        request = _make_request({})
        assert _resolve_principal(request) == 'Agent::"anonymous"'


class TestResolveResource:
    """Tests for the _resolve_resource function."""

    def test_memory_resource(self):
        assert _resolve_resource("/api/v1/memory/my-ns") == "memory/my-ns"

    def test_gateway_resource(self):
        assert _resolve_resource("/api/v1/gateway/completions") == "gateway/completions"

    def test_non_api_path(self):
        assert _resolve_resource("/healthz") == "/healthz"


class TestPolicyEnforcementMiddleware:
    """Integration tests for the middleware using a real FastAPI app."""

    @pytest.mark.asyncio
    async def test_no_enforcer_passes_through(self):
        """When no enforcer is set, all requests pass through."""
        from fastapi import FastAPI

        app = FastAPI()

        @app.get("/api/v1/memory/test")
        async def memory_list():
            return {"status": "ok"}

        app.add_middleware(PolicyEnforcementMiddleware)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/memory/test")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_exempt_paths_pass_through(self):
        """Exempt paths are never enforced even with a deny-all enforcer."""
        from fastapi import FastAPI

        app = FastAPI()

        @app.get("/healthz")
        async def healthz():
            return {"status": "ok"}

        @app.get("/api/v1/providers")
        async def providers():
            return {}

        @app.get("/api/v1/policy/engines")
        async def policy_engines():
            return {"policy_engines": []}

        app.add_middleware(PolicyEnforcementMiddleware)

        # Set up a deny-all enforcer
        enforcer = AsyncMock()
        enforcer.authorize = AsyncMock(return_value=False)
        app.state.enforcer = enforcer

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
            assert resp.status_code == 200

            resp = await client.get("/api/v1/providers")
            assert resp.status_code == 200

            resp = await client.get("/api/v1/policy/engines")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_denied_returns_403(self):
        """When the enforcer denies, return 403."""
        from fastapi import FastAPI

        app = FastAPI()

        @app.get("/api/v1/memory/{namespace}")
        async def memory_list(namespace: str):
            return {"status": "ok"}

        app.add_middleware(PolicyEnforcementMiddleware)

        enforcer = AsyncMock()
        enforcer.authorize = AsyncMock(return_value=False)
        app.state.enforcer = enforcer

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/memory/test-ns")
            assert resp.status_code == 403
            assert resp.json()["detail"] == "Forbidden by policy"

    @pytest.mark.asyncio
    async def test_allowed_passes_through(self):
        """When the enforcer allows, the request proceeds."""
        from fastapi import FastAPI

        app = FastAPI()

        @app.get("/api/v1/memory/{namespace}")
        async def memory_list(namespace: str):
            return {"status": "ok"}

        app.add_middleware(PolicyEnforcementMiddleware)

        enforcer = AsyncMock()
        enforcer.authorize = AsyncMock(return_value=True)
        app.state.enforcer = enforcer

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/memory/test-ns")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_unknown_route_passes_through(self):
        """Routes without an action mapping are not enforced."""
        from fastapi import FastAPI

        app = FastAPI()

        @app.get("/api/v1/custom/endpoint")
        async def custom():
            return {"status": "ok"}

        app.add_middleware(PolicyEnforcementMiddleware)

        enforcer = AsyncMock()
        enforcer.authorize = AsyncMock(return_value=False)
        app.state.enforcer = enforcer

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/custom/endpoint")
            assert resp.status_code == 200

        enforcer.authorize.assert_not_called()

    @pytest.mark.asyncio
    async def test_principal_from_agent_id_header(self):
        """The middleware passes the X-Agent-Id header as principal."""
        from fastapi import FastAPI

        app = FastAPI()

        @app.get("/api/v1/memory/{namespace}")
        async def memory_list(namespace: str):
            return {"status": "ok"}

        app.add_middleware(PolicyEnforcementMiddleware)

        enforcer = AsyncMock()
        enforcer.authorize = AsyncMock(return_value=True)
        app.state.enforcer = enforcer

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get(
                "/api/v1/memory/ns1",
                headers={"X-Agent-Id": "my-agent"},
            )

        enforcer.authorize.assert_called_once()
        call_kwargs = enforcer.authorize.call_args
        assert call_kwargs.kwargs["principal"] == 'Agent::"my-agent"'
        assert call_kwargs.kwargs["action"] == "memory:list"
        assert call_kwargs.kwargs["resource"] == "memory/ns1"

    @pytest.mark.asyncio
    async def test_docs_exempt(self):
        """API docs paths are exempt from enforcement."""
        from fastapi import FastAPI

        app = FastAPI()
        app.add_middleware(PolicyEnforcementMiddleware)

        enforcer = AsyncMock()
        enforcer.authorize = AsyncMock(return_value=False)
        app.state.enforcer = enforcer

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/docs")
            # FastAPI's /docs returns HTML (200) or redirect
            assert resp.status_code in (200, 307)

        enforcer.authorize.assert_not_called()

    @pytest.mark.asyncio
    async def test_metrics_exempt(self):
        """The /metrics path is exempt from enforcement."""
        from fastapi import FastAPI

        app = FastAPI()

        @app.get("/metrics")
        async def metrics():
            return "# metrics"

        app.add_middleware(PolicyEnforcementMiddleware)

        enforcer = AsyncMock()
        enforcer.authorize = AsyncMock(return_value=False)
        app.state.enforcer = enforcer

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/metrics")
            assert resp.status_code == 200

        enforcer.authorize.assert_not_called()
