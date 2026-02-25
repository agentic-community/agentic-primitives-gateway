from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentic_primitives_gateway.primitives.identity.entra import EntraIdentityProvider

_PATCH_PREFIX = "agentic_primitives_gateway.primitives.identity.entra"


@patch(f"{_PATCH_PREFIX}.get_service_credentials_or_defaults")
@patch(f"{_PATCH_PREFIX}.requests")
@patch(f"{_PATCH_PREFIX}.msal")
class TestEntraIdentityProvider:
    """Tests for the Entra identity provider."""

    def _setup_msal(self, mock_msal, mock_creds):
        mock_creds.return_value = {
            "tenant_id": "test-tenant",
            "client_id": "test-client",
            "client_secret": "test-secret",
        }
        mock_app = MagicMock()
        mock_msal.ConfidentialClientApplication.return_value = mock_app
        return mock_app

    # ── get_token ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_token_m2m_obo(self, mock_msal, mock_requests, mock_creds):
        mock_app = self._setup_msal(mock_msal, mock_creds)
        mock_app.acquire_token_on_behalf_of.return_value = {"access_token": "obo-token"}

        provider = EntraIdentityProvider(tenant_id="t", client_id="c", client_secret="s")
        result = await provider.get_token(
            credential_provider="my-api",
            workload_token="wt-123",
            auth_flow="M2M",
            scopes=["api://my-api/.default"],
        )

        assert result["access_token"] == "obo-token"
        assert result["token_type"] == "Bearer"
        mock_app.acquire_token_on_behalf_of.assert_called_once_with(
            user_assertion="wt-123",
            scopes=["api://my-api/.default"],
        )

    @pytest.mark.asyncio
    async def test_get_token_m2m_default_scopes(self, mock_msal, mock_requests, mock_creds):
        mock_app = self._setup_msal(mock_msal, mock_creds)
        mock_app.acquire_token_on_behalf_of.return_value = {"access_token": "tok"}

        provider = EntraIdentityProvider(tenant_id="t", client_id="c", client_secret="s")
        await provider.get_token(
            credential_provider="my-api",
            workload_token="wt",
        )

        call_kwargs = mock_app.acquire_token_on_behalf_of.call_args
        assert call_kwargs.kwargs["scopes"] == ["api://my-api/.default"]

    @pytest.mark.asyncio
    async def test_get_token_user_federation(self, mock_msal, mock_requests, mock_creds):
        mock_app = self._setup_msal(mock_msal, mock_creds)
        mock_app.get_authorization_request_url.return_value = "https://login.microsoftonline.com/authorize?..."

        provider = EntraIdentityProvider(tenant_id="t", client_id="c", client_secret="s")
        result = await provider.get_token(
            credential_provider="my-api",
            workload_token="wt",
            auth_flow="USER_FEDERATION",
            callback_url="https://example.com/cb",
            custom_state="state-abc",
        )

        assert result["authorization_url"] == "https://login.microsoftonline.com/authorize?..."
        assert result["session_uri"] == "state-abc"

    @pytest.mark.asyncio
    async def test_get_token_code_exchange(self, mock_msal, mock_requests, mock_creds):
        mock_app = self._setup_msal(mock_msal, mock_creds)
        mock_app.acquire_token_by_authorization_code.return_value = {"access_token": "code-tok"}

        provider = EntraIdentityProvider(tenant_id="t", client_id="c", client_secret="s")
        result = await provider.get_token(
            credential_provider="my-api",
            workload_token="wt",
            session_uri="auth-code-xyz",
            callback_url="https://example.com/cb",
        )

        assert result["access_token"] == "code-tok"

    @pytest.mark.asyncio
    async def test_get_token_obo_failure_raises(self, mock_msal, mock_requests, mock_creds):
        mock_app = self._setup_msal(mock_msal, mock_creds)
        mock_app.acquire_token_on_behalf_of.return_value = {"error": "invalid_grant", "error_description": "bad token"}

        provider = EntraIdentityProvider(tenant_id="t", client_id="c", client_secret="s")
        with pytest.raises(RuntimeError, match="OBO token exchange failed"):
            await provider.get_token(
                credential_provider="my-api",
                workload_token="bad-wt",
            )

    # ── get_api_key ───────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_api_key(self, mock_msal, mock_requests, mock_creds):
        mock_app = self._setup_msal(mock_msal, mock_creds)
        mock_app.acquire_token_for_client.return_value = {"access_token": "graph-tok"}

        # Mock Graph API responses
        mock_list_resp = MagicMock()
        mock_list_resp.raise_for_status = MagicMock()
        mock_list_resp.json.return_value = {"value": [{"id": "app-id-1", "displayName": "openai"}]}

        mock_detail_resp = MagicMock()
        mock_detail_resp.raise_for_status = MagicMock()
        mock_detail_resp.json.return_value = {"passwordCredentials": [{"secretText": "sk-secret"}]}

        mock_requests.get.side_effect = [mock_list_resp, mock_detail_resp]

        provider = EntraIdentityProvider(tenant_id="t", client_id="c", client_secret="s")
        result = await provider.get_api_key(
            credential_provider="openai",
            workload_token="wt",
        )

        assert result == {"api_key": "sk-secret", "credential_provider": "openai"}

    # ── get_workload_token ────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_workload_token_client_credentials(self, mock_msal, mock_requests, mock_creds):
        mock_app = self._setup_msal(mock_msal, mock_creds)
        mock_app.acquire_token_for_client.return_value = {"access_token": "cc-token"}

        provider = EntraIdentityProvider(tenant_id="t", client_id="c", client_secret="s")
        result = await provider.get_workload_token(workload_name="my-agent")

        assert result == {"workload_token": "cc-token", "workload_name": "my-agent"}
        mock_app.acquire_token_for_client.assert_called_once_with(
            scopes=["api://my-agent/.default"],
        )

    @pytest.mark.asyncio
    async def test_get_workload_token_with_user_token(self, mock_msal, mock_requests, mock_creds):
        mock_app = self._setup_msal(mock_msal, mock_creds)
        mock_app.acquire_token_on_behalf_of.return_value = {"access_token": "obo-user"}

        provider = EntraIdentityProvider(tenant_id="t", client_id="c", client_secret="s")
        result = await provider.get_workload_token(
            workload_name="my-agent",
            user_token="user-jwt",
        )

        assert result["workload_token"] == "obo-user"

    # ── list_credential_providers ─────────────────────────────

    @pytest.mark.asyncio
    async def test_list_credential_providers(self, mock_msal, mock_requests, mock_creds):
        mock_app = self._setup_msal(mock_msal, mock_creds)
        mock_app.acquire_token_for_client.return_value = {"access_token": "graph-tok"}

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "value": [
                {"displayName": "github-app", "appId": "app-1", "servicePrincipalType": "Application"},
            ]
        }
        mock_requests.get.return_value = mock_resp

        provider = EntraIdentityProvider(tenant_id="t", client_id="c", client_secret="s")
        result = await provider.list_credential_providers()

        assert len(result) == 1
        assert result[0]["name"] == "github-app"
        assert result[0]["provider_type"] == "oauth2"

    # ── complete_auth ─────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_complete_auth_is_noop(self, mock_msal, mock_requests, mock_creds):
        mock_creds.return_value = {"tenant_id": "t", "client_id": "c", "client_secret": "s"}
        provider = EntraIdentityProvider(tenant_id="t", client_id="c", client_secret="s")
        await provider.complete_auth(session_uri="state-abc", user_token="jwt")

    # ── workload identity CRUD ────────────────────────────────

    @pytest.mark.asyncio
    async def test_create_workload_identity(self, mock_msal, mock_requests, mock_creds):
        mock_app = self._setup_msal(mock_msal, mock_creds)
        mock_app.acquire_token_for_client.return_value = {"access_token": "graph-tok"}

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "id": "app-uuid",
            "displayName": "my-agent",
            "web": {"redirectUris": ["https://example.com/cb"]},
        }
        mock_requests.post.return_value = mock_resp

        provider = EntraIdentityProvider(tenant_id="t", client_id="c", client_secret="s")
        result = await provider.create_workload_identity(
            name="my-agent",
            allowed_return_urls=["https://example.com/cb"],
        )

        assert result["name"] == "my-agent"
        assert result["arn"] == "app-uuid"

    @pytest.mark.asyncio
    async def test_delete_workload_identity(self, mock_msal, mock_requests, mock_creds):
        mock_app = self._setup_msal(mock_msal, mock_creds)
        mock_app.acquire_token_for_client.return_value = {"access_token": "graph-tok"}

        mock_get_resp = MagicMock()
        mock_get_resp.raise_for_status = MagicMock()
        mock_get_resp.json.return_value = {"value": [{"id": "app-uuid", "displayName": "my-agent"}]}
        mock_requests.get.return_value = mock_get_resp

        mock_del_resp = MagicMock()
        mock_del_resp.raise_for_status = MagicMock()
        mock_requests.delete.return_value = mock_del_resp

        provider = EntraIdentityProvider(tenant_id="t", client_id="c", client_secret="s")
        await provider.delete_workload_identity(name="my-agent")

        mock_requests.delete.assert_called_once()

    # ── healthcheck ───────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_healthcheck_success(self, mock_msal, mock_requests, mock_creds):
        mock_app = self._setup_msal(mock_msal, mock_creds)
        mock_app.acquire_token_for_client.return_value = {"access_token": "graph-tok"}

        provider = EntraIdentityProvider(tenant_id="t", client_id="c", client_secret="s")
        assert await provider.healthcheck() is True

    @pytest.mark.asyncio
    async def test_healthcheck_failure(self, mock_msal, mock_requests, mock_creds):
        mock_app = self._setup_msal(mock_msal, mock_creds)
        mock_app.acquire_token_for_client.side_effect = Exception("unreachable")

        provider = EntraIdentityProvider(tenant_id="t", client_id="c", client_secret="s")
        assert await provider.healthcheck() is False

    # ── credential resolution ─────────────────────────────────

    @pytest.mark.asyncio
    async def test_uses_resolved_credentials(self, mock_msal, mock_requests, mock_creds):
        mock_creds.return_value = {
            "tenant_id": "override-tenant",
            "client_id": "override-client",
            "client_secret": "override-secret",
        }
        mock_app = MagicMock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "tok"}
        mock_msal.ConfidentialClientApplication.return_value = mock_app

        provider = EntraIdentityProvider(tenant_id="default-tenant", client_id="default-client")
        await provider.get_workload_token(workload_name="test")

        mock_msal.ConfidentialClientApplication.assert_called_with(
            client_id="override-client",
            client_credential="override-secret",
            authority="https://login.microsoftonline.com/override-tenant",
        )
