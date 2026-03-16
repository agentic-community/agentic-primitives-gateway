"""Tests for Keycloak credential writer."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.credentials.models import CredentialUpdateRequest
from agentic_primitives_gateway.credentials.writer.keycloak import KeycloakCredentialWriter


@pytest.fixture
def principal():
    return AuthenticatedPrincipal(id="user-123", type="user")


def _mock_response(status_code: int, json_data: dict | None = None) -> httpx.Response:
    """Create an httpx.Response with a request set (needed for raise_for_status)."""
    request = httpx.Request("GET", "https://kc.example.com/admin/realms/test/users/user-123")
    return httpx.Response(status_code, json=json_data, request=request)


class TestKeycloakWriterInit:
    def test_requires_config(self):
        with pytest.raises(ValueError, match="requires"):
            KeycloakCredentialWriter()

    def test_base_url_and_realm(self):
        w = KeycloakCredentialWriter(base_url="https://kc.example.com", realm="test")
        assert w._admin_url == "https://kc.example.com/admin/realms/test"
        assert w._account_url == "https://kc.example.com/realms/test/account"

    def test_issuer_derivation(self):
        w = KeycloakCredentialWriter(issuer="https://kc.example.com/realms/test")
        assert w._base_url == "https://kc.example.com"
        assert w._realm == "test"
        assert w._admin_url == "https://kc.example.com/admin/realms/test"

    def test_issuer_without_realms_fails(self):
        with pytest.raises(ValueError, match="Cannot derive realm"):
            KeycloakCredentialWriter(issuer="https://kc.example.com/something")


class TestKeycloakWriterAdminApi:
    @pytest.mark.asyncio
    async def test_read_via_admin_api(self, principal):
        writer = KeycloakCredentialWriter(
            base_url="https://kc.example.com",
            realm="test",
            admin_client_id="admin-client",
            admin_client_secret="secret",
        )
        # Mock token fetch
        token_resp = _mock_response(200, {"access_token": "admin-token"})
        user_resp = _mock_response(
            200,
            {
                "id": "user-123",
                "attributes": {
                    "apg.langfuse_pk": ["pk-abc"],
                    "apg.langfuse_sk": ["sk-xyz"],
                },
            },
        )
        writer._client = AsyncMock()
        writer._client.post = AsyncMock(return_value=token_resp)
        writer._client.get = AsyncMock(return_value=user_resp)

        result = await writer.read(principal, "user-token")
        assert result["apg.langfuse_pk"] == "pk-abc"
        assert result["apg.langfuse_sk"] == "sk-xyz"

    @pytest.mark.asyncio
    async def test_write_via_admin_api(self, principal):
        writer = KeycloakCredentialWriter(
            base_url="https://kc.example.com",
            realm="test",
            admin_client_id="admin-client",
            admin_client_secret="secret",
        )
        token_resp = _mock_response(200, {"access_token": "admin-token"})
        user_resp = _mock_response(
            200,
            {
                "id": "user-123",
                "attributes": {"existing": ["old-value"]},
            },
        )
        put_resp = _mock_response(204)

        writer._client = AsyncMock()
        writer._client.post = AsyncMock(return_value=token_resp)
        writer._client.get = AsyncMock(return_value=user_resp)
        writer._client.put = AsyncMock(return_value=put_resp)
        # Skip User Profile declaration (tested separately)
        writer._ensure_attributes_declared = AsyncMock()

        updates = CredentialUpdateRequest(attributes={"apg.langfuse_pk": "new-pk"})
        await writer.write(principal, "user-token", updates)

        # Verify PUT was called with merged attributes
        call_args = writer._client.put.call_args
        posted_data = call_args.kwargs["json"]
        assert posted_data["attributes"]["existing"] == ["old-value"]
        assert posted_data["attributes"]["apg.langfuse_pk"] == ["new-pk"]


class TestKeycloakWriterAccountApiFallback:
    @pytest.mark.asyncio
    async def test_read_via_account_api_fallback(self, principal):
        """When no admin credentials, falls back to Account API."""
        writer = KeycloakCredentialWriter(issuer="https://kc.example.com/realms/test")
        account_resp = _mock_response(
            200,
            {
                "attributes": {"apg.langfuse_pk": ["pk-abc"]},
            },
        )
        writer._client = AsyncMock()
        writer._client.get = AsyncMock(return_value=account_resp)

        result = await writer.read(principal, "user-token")
        assert result["apg.langfuse_pk"] == "pk-abc"

    @pytest.mark.asyncio
    async def test_write_via_account_api_fallback(self, principal):
        """When no admin credentials, falls back to Account API."""
        writer = KeycloakCredentialWriter(issuer="https://kc.example.com/realms/test")
        account_resp = _mock_response(200, {"attributes": {}})
        post_resp = _mock_response(204)

        writer._client = AsyncMock()
        writer._client.get = AsyncMock(return_value=account_resp)
        writer._client.post = AsyncMock(return_value=post_resp)

        updates = CredentialUpdateRequest(attributes={"apg.langfuse_pk": "new-pk"})
        await writer.write(principal, "user-token", updates)


class TestKeycloakWriterClose:
    @pytest.mark.asyncio
    async def test_close(self):
        writer = KeycloakCredentialWriter(issuer="https://kc.example.com/realms/test")
        await writer.close()  # Should not raise
