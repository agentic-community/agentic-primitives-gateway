from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from agentic_primitives_gateway.main import app


class TestIdentityTokenRoutes:
    """Route-level tests for identity token operations (Noop provider)."""

    def setup_method(self):
        self.client = TestClient(app)

    def test_get_token(self):
        resp = self.client.post(
            "/api/v1/identity/token",
            json={
                "credential_provider": "github",
                "workload_token": "wt-123",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "Bearer"

    def test_get_token_with_auth_flow(self):
        resp = self.client.post(
            "/api/v1/identity/token",
            json={
                "credential_provider": "github",
                "workload_token": "wt-123",
                "auth_flow": "USER_FEDERATION",
                "scopes": ["repo"],
                "callback_url": "https://example.com/callback",
            },
        )
        assert resp.status_code == 200

    def test_get_api_key(self):
        resp = self.client.post(
            "/api/v1/identity/api-key",
            json={
                "credential_provider": "openai",
                "workload_token": "wt-123",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "api_key" in body
        assert body["credential_provider"] == "openai"

    def test_get_workload_token(self):
        resp = self.client.post(
            "/api/v1/identity/workload-token",
            json={"workload_name": "my-agent"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "workload_token" in body
        assert body["workload_name"] == "my-agent"

    def test_get_workload_token_with_user_id(self):
        resp = self.client.post(
            "/api/v1/identity/workload-token",
            json={"workload_name": "my-agent", "user_id": "user-123"},
        )
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_non_admin_operator_token_grants_denied_before_backend() -> None:
    from agentic_primitives_gateway.models.identity import TokenRequest, WorkloadTokenRequest
    from agentic_primitives_gateway.primitives.identity.entra import EntraIdentityProvider
    from agentic_primitives_gateway.primitives.identity.keycloak import KeycloakIdentityProvider
    from agentic_primitives_gateway.primitives.identity.okta import OktaIdentityProvider
    from agentic_primitives_gateway.routes import identity as identity_routes

    providers = (
        (KeycloakIdentityProvider(), WorkloadTokenRequest(workload_name="worker")),
        (EntraIdentityProvider(), WorkloadTokenRequest(workload_name="worker")),
        (OktaIdentityProvider(), WorkloadTokenRequest(workload_name="worker", user_token="user-token")),
    )
    for provider, request in providers:
        with (
            patch.object(identity_routes, "registry", MagicMock(identity=provider)),
            patch.object(identity_routes, "require_admin", side_effect=HTTPException(403, "Forbidden")),
            patch.object(provider, "get_workload_token", new_callable=AsyncMock) as fetch,
            pytest.raises(HTTPException) as denied,
        ):
            await identity_routes.get_workload_token(request)
        assert denied.value.status_code == 403
        fetch.assert_not_awaited()

    okta = OktaIdentityProvider()
    with (
        patch.object(identity_routes, "registry", MagicMock(identity=okta)),
        patch.object(identity_routes, "require_admin", side_effect=HTTPException(403, "Forbidden")),
        patch.object(okta, "get_token", new_callable=AsyncMock) as fetch,
        pytest.raises(HTTPException) as denied,
    ):
        await identity_routes.get_token(TokenRequest(credential_provider="api", workload_token="token"))
    assert denied.value.status_code == 403
    fetch.assert_not_awaited()


class TestIdentityControlPlaneRoutes:
    """Route-level tests for identity control plane operations (Noop provider returns 501)."""

    def setup_method(self):
        self.client = TestClient(app)

    def test_complete_auth_returns_501(self):
        resp = self.client.post(
            "/api/v1/identity/auth/complete",
            json={"session_uri": "session-abc", "user_token": "jwt"},
        )
        assert resp.status_code == 501

    def test_list_credential_providers(self):
        resp = self.client.get("/api/v1/identity/credential-providers")
        assert resp.status_code == 200
        body = resp.json()
        assert body["credential_providers"] == []

    def test_create_credential_provider_returns_501(self):
        resp = self.client.post(
            "/api/v1/identity/credential-providers",
            json={"name": "github", "provider_type": "oauth2", "config": {}},
        )
        assert resp.status_code == 501

    def test_get_credential_provider_returns_501(self):
        resp = self.client.get("/api/v1/identity/credential-providers/github")
        assert resp.status_code == 501

    def test_update_credential_provider_returns_501(self):
        resp = self.client.put(
            "/api/v1/identity/credential-providers/github",
            json={"config": {}},
        )
        assert resp.status_code == 501

    def test_delete_credential_provider_returns_501(self):
        resp = self.client.delete("/api/v1/identity/credential-providers/github")
        assert resp.status_code == 501

    def test_list_workload_identities_returns_501(self):
        resp = self.client.get("/api/v1/identity/workload-identities")
        assert resp.status_code == 501

    def test_create_workload_identity_returns_501(self):
        resp = self.client.post(
            "/api/v1/identity/workload-identities",
            json={"name": "my-agent"},
        )
        assert resp.status_code == 501

    def test_get_workload_identity_returns_501(self):
        resp = self.client.get("/api/v1/identity/workload-identities/my-agent")
        assert resp.status_code == 501

    def test_update_workload_identity_returns_501(self):
        resp = self.client.put(
            "/api/v1/identity/workload-identities/my-agent",
            json={"allowed_return_urls": ["https://example.com/callback"]},
        )
        assert resp.status_code == 501

    def test_delete_workload_identity_returns_501(self):
        resp = self.client.delete("/api/v1/identity/workload-identities/my-agent")
        assert resp.status_code == 501
