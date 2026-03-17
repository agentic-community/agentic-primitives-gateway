"""Integration tests for the policy enforcement middleware.

Full stack without mocks:
HTTP client → ASGI → PolicyEnforcementMiddleware → CedarPolicyEnforcer
 → cedarpy (real evaluation) → route → provider

Uses NoopPolicyProvider (in-memory) for policy CRUD and CedarPolicyEnforcer
for real Cedar evaluation. No AWS credentials required.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from agentic_primitives_gateway.config import Settings
from agentic_primitives_gateway.enforcement.cedar import CedarPolicyEnforcer
from agentic_primitives_gateway.enforcement.middleware import PolicyEnforcementMiddleware
from agentic_primitives_gateway.enforcement.noop import NoopPolicyEnforcer

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _init_registry():
    """Override conftest autouse fixture — enforcement tests manage their own registry."""
    yield


def _build_app() -> FastAPI:
    """Build a minimal app with memory routes + enforcement middleware."""
    from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
    from starlette.responses import Response

    from agentic_primitives_gateway.routes import memory

    inner_app = FastAPI()
    inner_app.include_router(memory.router)

    @inner_app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @inner_app.get("/api/v1/providers")
    async def providers():
        return {}

    # Add a dummy RequestContextMiddleware to set up contextvars
    from agentic_primitives_gateway.auth.models import NOOP_PRINCIPAL
    from agentic_primitives_gateway.context import (
        set_authenticated_principal,
        set_aws_credentials,
        set_provider_overrides,
        set_request_id,
        set_service_credentials,
    )

    class MinimalContextMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next: RequestResponseEndpoint) -> Response:
            set_request_id("test")
            set_aws_credentials(None)
            set_service_credentials({})
            set_provider_overrides({})
            set_authenticated_principal(NOOP_PRINCIPAL)
            return await call_next(request)

    inner_app.add_middleware(MinimalContextMiddleware)
    inner_app.add_middleware(PolicyEnforcementMiddleware)
    return inner_app


@pytest.fixture
def _init_noop_registry():
    """Initialize the global registry with noop/in-memory providers."""
    from agentic_primitives_gateway.registry import registry

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
            "policy": {
                "backend": "agentic_primitives_gateway.primitives.policy.noop.NoopPolicyProvider",
                "config": {},
            },
            "evaluations": {
                "backend": "agentic_primitives_gateway.primitives.evaluations.noop.NoopEvaluationsProvider",
                "config": {},
            },
        },
    )
    registry.initialize(test_settings)
    yield registry


class TestNoopEnforcerIntegration:
    """With NoopPolicyEnforcer, all requests pass through (gateway default)."""

    @pytest.mark.asyncio
    async def test_all_requests_allowed(self, _init_noop_registry):
        app = _build_app()
        enforcer = NoopPolicyEnforcer()
        app.state.enforcer = enforcer

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/memory/test-ns",
                json={"key": "k1", "content": "hello"},
            )
            assert resp.status_code == 201

            resp = await client.get("/api/v1/memory/test-ns/k1")
            assert resp.status_code == 200
            assert resp.json()["content"] == "hello"

        await enforcer.close()


class TestCedarEnforcerIntegration:
    """Full Cedar enforcement with real cedarpy evaluation."""

    @pytest.mark.asyncio
    async def test_default_deny_no_policies(self, _init_noop_registry):
        """Cedar enforcer with no policies denies all requests."""
        app = _build_app()
        enforcer = CedarPolicyEnforcer()
        # Don't load policies — empty set = deny all
        app.state.enforcer = enforcer

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/memory/test-ns",
                json={"key": "k1", "content": "hello"},
            )
            assert resp.status_code == 403
            assert resp.json()["detail"] == "Forbidden by policy"

        await enforcer.close()

    @pytest.mark.asyncio
    async def test_exempt_paths_allowed_despite_deny(self, _init_noop_registry):
        """Exempt paths pass through even with deny-all enforcement."""
        app = _build_app()
        enforcer = CedarPolicyEnforcer()
        app.state.enforcer = enforcer

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
            assert resp.status_code == 200

            resp = await client.get("/api/v1/providers")
            assert resp.status_code == 200

        await enforcer.close()

    @pytest.mark.asyncio
    async def test_permit_all_allows_requests(self, _init_noop_registry):
        """A wildcard permit policy allows all requests."""
        app = _build_app()
        enforcer = CedarPolicyEnforcer()
        enforcer._policies = ["permit(principal, action, resource);"]
        app.state.enforcer = enforcer

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/memory/test-ns",
                json={"key": "k1", "content": "hello"},
            )
            assert resp.status_code == 201

            resp = await client.get("/api/v1/memory/test-ns/k1")
            assert resp.status_code == 200
            assert resp.json()["content"] == "hello"

        await enforcer.close()

    @pytest.mark.asyncio
    async def test_principal_scoped_permit(self, _init_noop_registry):
        """A principal-scoped permit only allows that agent."""
        app = _build_app()
        enforcer = CedarPolicyEnforcer()
        enforcer._policies = ['permit(principal == Agent::"allowed-agent", action, resource);']
        app.state.enforcer = enforcer

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Allowed agent
            resp = await client.post(
                "/api/v1/memory/test-ns",
                json={"key": "k1", "content": "hello"},
                headers={"X-Agent-Id": "allowed-agent"},
            )
            assert resp.status_code == 201

            # Denied agent
            resp = await client.post(
                "/api/v1/memory/test-ns",
                json={"key": "k2", "content": "world"},
                headers={"X-Agent-Id": "denied-agent"},
            )
            assert resp.status_code == 403

        await enforcer.close()

    @pytest.mark.asyncio
    async def test_load_policies_from_noop_provider(self, _init_noop_registry):
        """CedarPolicyEnforcer loads policies from the NoopPolicyProvider."""
        reg = _init_noop_registry

        app = _build_app()
        enforcer = CedarPolicyEnforcer()
        engine_id = await enforcer.ensure_engine()

        # Create a policy in the enforcer's engine
        policy_provider = reg.policy
        await policy_provider.create_policy(
            engine_id=engine_id,
            policy_body="permit(principal, action, resource);",
        )

        await enforcer.load_policies()
        app.state.enforcer = enforcer

        assert len(enforcer._policies) == 1

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/memory/test-ns",
                json={"key": "k1", "content": "hello"},
            )
            assert resp.status_code == 201

        await enforcer.close()

    @pytest.mark.asyncio
    async def test_forbid_overrides_permit(self, _init_noop_registry):
        """A forbid policy takes precedence over a permit."""
        app = _build_app()
        enforcer = CedarPolicyEnforcer()
        enforcer._policies = [
            "permit(principal, action, resource);",
            'forbid(principal == Agent::"blocked", action, resource);',
        ]
        app.state.enforcer = enforcer

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Permitted agent
            resp = await client.post(
                "/api/v1/memory/test-ns",
                json={"key": "k1", "content": "hello"},
                headers={"X-Agent-Id": "good-agent"},
            )
            assert resp.status_code == 201

            # Blocked agent
            resp = await client.post(
                "/api/v1/memory/test-ns",
                json={"key": "k2", "content": "world"},
                headers={"X-Agent-Id": "blocked"},
            )
            assert resp.status_code == 403

        await enforcer.close()

    @pytest.mark.asyncio
    async def test_multi_tenant_isolation(self, _init_noop_registry):
        """Different agents have different access based on Cedar policies."""
        app = _build_app()
        enforcer = CedarPolicyEnforcer()
        enforcer._policies = [
            'permit(principal == Agent::"tenant-a", action, resource);',
            'permit(principal == Agent::"tenant-b", action, resource);',
        ]
        app.state.enforcer = enforcer

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # tenant-a can write
            resp = await client.post(
                "/api/v1/memory/ns-a",
                json={"key": "k1", "content": "a-data"},
                headers={"X-Agent-Id": "tenant-a"},
            )
            assert resp.status_code == 201

            # tenant-b can write
            resp = await client.post(
                "/api/v1/memory/ns-b",
                json={"key": "k1", "content": "b-data"},
                headers={"X-Agent-Id": "tenant-b"},
            )
            assert resp.status_code == 201

            # unknown tenant is denied
            resp = await client.post(
                "/api/v1/memory/ns-c",
                json={"key": "k1", "content": "c-data"},
                headers={"X-Agent-Id": "tenant-c"},
            )
            assert resp.status_code == 403

        await enforcer.close()
