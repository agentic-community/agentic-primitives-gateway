from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentic_primitives_gateway.primitives.identity.keycloak import KeycloakIdentityProvider

_PATCH_PREFIX = "agentic_primitives_gateway.primitives.identity.keycloak"


@patch(f"{_PATCH_PREFIX}.get_service_credentials_or_defaults")
@patch(f"{_PATCH_PREFIX}.KeycloakAdmin")
@patch(f"{_PATCH_PREFIX}.KeycloakOpenID")
class TestKeycloakIdentityProvider:
    """Tests for the Keycloak identity provider."""

    # ── get_token ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_token_m2m(self, mock_openid_cls, mock_admin_cls, mock_creds):
        mock_creds.return_value = {"server_url": "http://kc", "realm": "r", "client_id": "c", "client_secret": "s"}
        mock_openid = MagicMock()
        mock_openid.exchange_token.return_value = {"access_token": "exchanged-tok"}
        mock_openid_cls.return_value = mock_openid

        provider = KeycloakIdentityProvider()
        result = await provider.get_token(
            credential_provider="github",
            workload_token="wt-123",
            auth_flow="M2M",
            scopes=["repo"],
        )

        assert result["access_token"] == "exchanged-tok"
        assert result["token_type"] == "Bearer"
        mock_openid.exchange_token.assert_called_once_with(
            token="wt-123",
            audience="github",
            scope="repo",
        )

    @pytest.mark.asyncio
    async def test_get_token_user_federation_returns_auth_url(self, mock_openid_cls, mock_admin_cls, mock_creds):
        mock_creds.return_value = {"server_url": "http://kc", "realm": "r", "client_id": "c", "client_secret": "s"}
        mock_openid = MagicMock()
        mock_openid.auth_url.return_value = "https://kc/auth?state=abc"
        mock_openid_cls.return_value = mock_openid

        provider = KeycloakIdentityProvider()
        result = await provider.get_token(
            credential_provider="github",
            workload_token="wt",
            auth_flow="USER_FEDERATION",
            callback_url="https://example.com/cb",
            custom_state="abc",
        )

        assert result["authorization_url"] == "https://kc/auth?state=abc"
        assert result["session_uri"] == "abc"

    @pytest.mark.asyncio
    async def test_get_token_with_session_uri_exchanges_code(self, mock_openid_cls, mock_admin_cls, mock_creds):
        mock_creds.return_value = {"server_url": "http://kc", "realm": "r", "client_id": "c", "client_secret": "s"}
        mock_openid = MagicMock()
        mock_openid.token.return_value = {"access_token": "code-exchanged"}
        mock_openid_cls.return_value = mock_openid

        provider = KeycloakIdentityProvider()
        result = await provider.get_token(
            credential_provider="github",
            workload_token="wt",
            session_uri="auth-code-xyz",
            callback_url="https://example.com/cb",
        )

        assert result["access_token"] == "code-exchanged"
        mock_openid.token.assert_called_once_with(
            grant_type="authorization_code",
            code="auth-code-xyz",
            redirect_uri="https://example.com/cb",
        )

    # ── get_api_key ───────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_api_key(self, mock_openid_cls, mock_admin_cls, mock_creds):
        mock_creds.return_value = {"server_url": "http://kc", "realm": "r", "client_id": "c", "client_secret": "s"}
        mock_admin = MagicMock()
        mock_admin.get_client_id.return_value = "uuid-123"
        mock_admin.get_client_secrets.return_value = {"value": "sk-secret"}
        mock_admin_cls.return_value = mock_admin

        provider = KeycloakIdentityProvider()
        result = await provider.get_api_key(
            credential_provider="openai",
            workload_token="wt",
        )

        assert result == {"api_key": "sk-secret", "credential_provider": "openai"}
        mock_admin.get_client_id.assert_called_once_with("openai")

    # ── get_workload_token ────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_workload_token_plain(self, mock_openid_cls, mock_admin_cls, mock_creds):
        mock_creds.return_value = {"server_url": "http://kc", "realm": "r", "client_id": "c", "client_secret": "s"}
        mock_openid = MagicMock()
        mock_openid.token.return_value = {"access_token": "cc-token"}
        mock_openid_cls.return_value = mock_openid

        provider = KeycloakIdentityProvider()
        result = await provider.get_workload_token(workload_name="my-agent")

        assert result == {"workload_token": "cc-token", "workload_name": "my-agent"}
        mock_openid.token.assert_called_once_with(
            grant_type="client_credentials",
            scope="openid",
        )

    @pytest.mark.asyncio
    async def test_get_workload_token_with_user_token(self, mock_openid_cls, mock_admin_cls, mock_creds):
        mock_creds.return_value = {"server_url": "http://kc", "realm": "r", "client_id": "c", "client_secret": "s"}
        mock_openid = MagicMock()
        mock_openid.exchange_token.return_value = {"access_token": "user-scoped"}
        mock_openid_cls.return_value = mock_openid

        provider = KeycloakIdentityProvider()
        result = await provider.get_workload_token(
            workload_name="my-agent",
            user_token="user-jwt",
        )

        assert result["workload_token"] == "user-scoped"
        mock_openid.exchange_token.assert_called_once_with(
            token="user-jwt",
            audience="my-agent",
        )

    # ── list_credential_providers ─────────────────────────────

    @pytest.mark.asyncio
    async def test_list_credential_providers(self, mock_openid_cls, mock_admin_cls, mock_creds):
        mock_creds.return_value = {"server_url": "http://kc", "realm": "r", "client_id": "c", "client_secret": "s"}
        mock_admin = MagicMock()
        mock_admin.get_idps.return_value = [
            {"alias": "github", "providerId": "github"},
        ]
        mock_admin.get_clients.return_value = [
            {"clientId": "openai-keys", "publicClient": False, "serviceAccountsEnabled": True},
            {"clientId": "public-app", "publicClient": True, "serviceAccountsEnabled": False},
        ]
        mock_admin_cls.return_value = mock_admin

        provider = KeycloakIdentityProvider()
        result = await provider.list_credential_providers()

        assert len(result) == 2
        assert result[0]["name"] == "github"
        assert result[0]["provider_type"] == "oauth2"
        assert result[1]["name"] == "openai-keys"
        assert result[1]["provider_type"] == "api_key"

    # ── complete_auth ─────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_complete_auth_is_noop(self, mock_openid_cls, mock_admin_cls, mock_creds):
        mock_creds.return_value = {"server_url": "http://kc", "realm": "r", "client_id": "c", "client_secret": "s"}
        provider = KeycloakIdentityProvider()
        # Should not raise
        await provider.complete_auth(session_uri="state-abc", user_token="jwt")

    # ── create_credential_provider ────────────────────────────

    @pytest.mark.asyncio
    async def test_create_oauth2_credential_provider(self, mock_openid_cls, mock_admin_cls, mock_creds):
        mock_creds.return_value = {"server_url": "http://kc", "realm": "r", "client_id": "c", "client_secret": "s"}
        mock_admin = MagicMock()
        mock_admin_cls.return_value = mock_admin

        provider = KeycloakIdentityProvider()
        result = await provider.create_credential_provider(
            name="github",
            provider_type="oauth2",
            config={"provider_id": "github", "clientId": "gh-id", "clientSecret": "gh-secret"},
        )

        assert result["name"] == "github"
        assert result["provider_type"] == "oauth2"
        mock_admin.create_idp.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_api_key_credential_provider(self, mock_openid_cls, mock_admin_cls, mock_creds):
        mock_creds.return_value = {"server_url": "http://kc", "realm": "r", "client_id": "c", "client_secret": "s"}
        mock_admin = MagicMock()
        mock_admin.create_client.return_value = "uuid-new"
        mock_admin_cls.return_value = mock_admin

        provider = KeycloakIdentityProvider()
        result = await provider.create_credential_provider(
            name="openai",
            provider_type="api_key",
            config={"api_key": "sk-123"},
        )

        assert result["name"] == "openai"
        assert result["provider_type"] == "api_key"
        assert result["arn"] == "uuid-new"

    # ── workload identity CRUD ────────────────────────────────

    @pytest.mark.asyncio
    async def test_create_workload_identity(self, mock_openid_cls, mock_admin_cls, mock_creds):
        mock_creds.return_value = {"server_url": "http://kc", "realm": "r", "client_id": "c", "client_secret": "s"}
        mock_admin = MagicMock()
        mock_admin.create_client.return_value = "uuid-agent"
        mock_admin_cls.return_value = mock_admin

        provider = KeycloakIdentityProvider()
        result = await provider.create_workload_identity(
            name="my-agent",
            allowed_return_urls=["https://example.com/cb"],
        )

        assert result["name"] == "my-agent"
        assert result["arn"] == "uuid-agent"
        assert result["allowed_return_urls"] == ["https://example.com/cb"]

    @pytest.mark.asyncio
    async def test_get_workload_identity(self, mock_openid_cls, mock_admin_cls, mock_creds):
        mock_creds.return_value = {"server_url": "http://kc", "realm": "r", "client_id": "c", "client_secret": "s"}
        mock_admin = MagicMock()
        mock_admin.get_client_id.return_value = "uuid-agent"
        mock_admin.get_client.return_value = {
            "clientId": "my-agent",
            "redirectUris": ["https://example.com/cb"],
        }
        mock_admin_cls.return_value = mock_admin

        provider = KeycloakIdentityProvider()
        result = await provider.get_workload_identity(name="my-agent")

        assert result["name"] == "my-agent"
        assert result["allowed_return_urls"] == ["https://example.com/cb"]

    @pytest.mark.asyncio
    async def test_delete_workload_identity(self, mock_openid_cls, mock_admin_cls, mock_creds):
        mock_creds.return_value = {"server_url": "http://kc", "realm": "r", "client_id": "c", "client_secret": "s"}
        mock_admin = MagicMock()
        mock_admin.get_client_id.return_value = "uuid-agent"
        mock_admin_cls.return_value = mock_admin

        provider = KeycloakIdentityProvider()
        await provider.delete_workload_identity(name="my-agent")

        mock_admin.delete_client.assert_called_once_with("uuid-agent")

    @pytest.mark.asyncio
    async def test_list_workload_identities(self, mock_openid_cls, mock_admin_cls, mock_creds):
        mock_creds.return_value = {"server_url": "http://kc", "realm": "r", "client_id": "c", "client_secret": "s"}
        mock_admin = MagicMock()
        mock_admin.get_clients.return_value = [
            {"clientId": "agent-1", "id": "u1", "serviceAccountsEnabled": True, "redirectUris": []},
            {"clientId": "public-app", "id": "u2", "serviceAccountsEnabled": False},
        ]
        mock_admin_cls.return_value = mock_admin

        provider = KeycloakIdentityProvider()
        result = await provider.list_workload_identities()

        assert len(result) == 1
        assert result[0]["name"] == "agent-1"

    # ── healthcheck ───────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_healthcheck_success(self, mock_openid_cls, mock_admin_cls, mock_creds):
        mock_creds.return_value = {"server_url": "http://kc", "realm": "r", "client_id": "c", "client_secret": "s"}
        mock_openid = MagicMock()
        mock_openid.well_known.return_value = {"issuer": "http://kc/realms/r"}
        mock_openid_cls.return_value = mock_openid

        provider = KeycloakIdentityProvider()
        assert await provider.healthcheck() is True

    @pytest.mark.asyncio
    async def test_healthcheck_failure(self, mock_openid_cls, mock_admin_cls, mock_creds):
        mock_creds.return_value = {"server_url": "http://kc", "realm": "r", "client_id": "c", "client_secret": "s"}
        mock_openid = MagicMock()
        mock_openid.well_known.side_effect = ConnectionError("unreachable")
        mock_openid_cls.return_value = mock_openid

        provider = KeycloakIdentityProvider()
        assert await provider.healthcheck() is False

    # ── credential resolution ─────────────────────────────────

    @pytest.mark.asyncio
    async def test_uses_resolved_credentials(self, mock_openid_cls, mock_admin_cls, mock_creds):
        mock_creds.return_value = {
            "server_url": "https://override.kc",
            "realm": "override-realm",
            "client_id": "override-client",
            "client_secret": "override-secret",
        }
        mock_openid = MagicMock()
        mock_openid.token.return_value = {"access_token": "tok"}
        mock_openid_cls.return_value = mock_openid

        provider = KeycloakIdentityProvider(
            server_url="http://default.kc",
            realm="default-realm",
        )
        await provider.get_workload_token(workload_name="test")

        mock_openid_cls.assert_called_with(
            server_url="https://override.kc",
            realm_name="override-realm",
            client_id="override-client",
            client_secret_key="override-secret",
        )
