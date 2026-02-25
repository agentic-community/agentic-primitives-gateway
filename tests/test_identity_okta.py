from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentic_primitives_gateway.primitives.identity.okta import OktaIdentityProvider

_PATCH_PREFIX = "agentic_primitives_gateway.primitives.identity.okta"


@patch(f"{_PATCH_PREFIX}.get_service_credentials_or_defaults")
@patch(f"{_PATCH_PREFIX}.requests")
class TestOktaIdentityProvider:
    """Tests for the Okta identity provider."""

    def _setup(self, mock_creds):
        mock_creds.return_value = {
            "domain": "dev-test.okta.com",
            "client_id": "test-client",
            "client_secret": "test-secret",
            "api_token": "test-api-token",
        }

    def _mock_post(self, mock_requests, json_response):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = json_response
        mock_requests.post.return_value = mock_resp
        return mock_resp

    # ── get_token ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_token_m2m(self, mock_requests, mock_creds):
        self._setup(mock_creds)
        self._mock_post(mock_requests, {"access_token": "cc-token", "token_type": "Bearer"})

        provider = OktaIdentityProvider(domain="dev-test.okta.com", client_id="c", client_secret="s")
        result = await provider.get_token(
            credential_provider="my-api",
            workload_token="wt-123",
            auth_flow="M2M",
            scopes=["my-api"],
        )

        assert result["access_token"] == "cc-token"
        assert result["token_type"] == "Bearer"

    @pytest.mark.asyncio
    async def test_get_token_user_federation(self, mock_requests, mock_creds):
        self._setup(mock_creds)

        provider = OktaIdentityProvider(domain="dev-test.okta.com", client_id="c", client_secret="s")
        result = await provider.get_token(
            credential_provider="my-api",
            workload_token="wt",
            auth_flow="USER_FEDERATION",
            callback_url="https://example.com/cb",
            custom_state="state-abc",
        )

        assert "authorization_url" in result
        assert "dev-test.okta.com" in result["authorization_url"]
        assert result["session_uri"] == "state-abc"

    @pytest.mark.asyncio
    async def test_get_token_code_exchange(self, mock_requests, mock_creds):
        self._setup(mock_creds)
        self._mock_post(mock_requests, {"access_token": "code-token"})

        provider = OktaIdentityProvider(domain="dev-test.okta.com", client_id="c", client_secret="s")
        result = await provider.get_token(
            credential_provider="my-api",
            workload_token="wt",
            session_uri="auth-code-xyz",
            callback_url="https://example.com/cb",
        )

        assert result["access_token"] == "code-token"

    # ── get_api_key ───────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_api_key(self, mock_requests, mock_creds):
        self._setup(mock_creds)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [
            {"id": "app-1", "label": "openai", "credentials": {"oauthClient": {"client_secret": "sk-123"}}}
        ]
        mock_requests.get.return_value = mock_resp

        provider = OktaIdentityProvider(domain="dev-test.okta.com", client_id="c", api_token="tok")
        result = await provider.get_api_key(credential_provider="openai", workload_token="wt")

        assert result == {"api_key": "sk-123", "credential_provider": "openai"}

    # ── get_workload_token ────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_workload_token(self, mock_requests, mock_creds):
        self._setup(mock_creds)
        self._mock_post(mock_requests, {"access_token": "wt-token"})

        provider = OktaIdentityProvider(domain="dev-test.okta.com", client_id="c", client_secret="s")
        result = await provider.get_workload_token(workload_name="my-agent")

        assert result == {"workload_token": "wt-token", "workload_name": "my-agent"}

    # ── list_credential_providers ─────────────────────────────

    @pytest.mark.asyncio
    async def test_list_credential_providers(self, mock_requests, mock_creds):
        self._setup(mock_creds)

        mock_idp_resp = MagicMock()
        mock_idp_resp.raise_for_status = MagicMock()
        mock_idp_resp.json.return_value = [{"name": "google", "type": "GOOGLE", "id": "idp-1"}]

        mock_app_resp = MagicMock()
        mock_app_resp.raise_for_status = MagicMock()
        mock_app_resp.json.return_value = [
            {"label": "my-api", "id": "app-1", "signOnMode": "OPENID_CONNECT"},
            {"label": "saml-app", "id": "app-2", "signOnMode": "SAML_2_0"},
        ]

        mock_requests.get.side_effect = [mock_idp_resp, mock_app_resp]

        provider = OktaIdentityProvider(domain="dev-test.okta.com", api_token="tok")
        result = await provider.list_credential_providers()

        assert len(result) == 2
        assert result[0]["name"] == "google"
        assert result[0]["provider_type"] == "oauth2"
        assert result[1]["name"] == "my-api"
        assert result[1]["provider_type"] == "api_key"

    # ── complete_auth ─────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_complete_auth_is_noop(self, mock_requests, mock_creds):
        self._setup(mock_creds)
        provider = OktaIdentityProvider(domain="dev-test.okta.com")
        await provider.complete_auth(session_uri="state-abc", user_token="jwt")

    # ── workload identity CRUD ────────────────────────────────

    @pytest.mark.asyncio
    async def test_create_workload_identity(self, mock_requests, mock_creds):
        self._setup(mock_creds)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "id": "app-new",
            "label": "my-agent",
            "settings": {"oauthClient": {"redirect_uris": ["https://example.com/cb"]}},
        }
        mock_requests.post.return_value = mock_resp

        provider = OktaIdentityProvider(domain="dev-test.okta.com", api_token="tok")
        result = await provider.create_workload_identity(
            name="my-agent",
            allowed_return_urls=["https://example.com/cb"],
        )

        assert result["name"] == "my-agent"
        assert result["arn"] == "app-new"

    @pytest.mark.asyncio
    async def test_delete_workload_identity(self, mock_requests, mock_creds):
        self._setup(mock_creds)

        mock_get_resp = MagicMock()
        mock_get_resp.raise_for_status = MagicMock()
        mock_get_resp.json.return_value = [{"id": "app-1", "label": "my-agent"}]
        mock_requests.get.return_value = mock_get_resp

        mock_del_resp = MagicMock()
        mock_del_resp.raise_for_status = MagicMock()
        mock_requests.delete.return_value = mock_del_resp

        provider = OktaIdentityProvider(domain="dev-test.okta.com", api_token="tok")
        await provider.delete_workload_identity(name="my-agent")

        mock_requests.delete.assert_called_once()

    # ── healthcheck ───────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_healthcheck_success(self, mock_requests, mock_creds):
        self._setup(mock_creds)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_requests.get.return_value = mock_resp

        provider = OktaIdentityProvider(domain="dev-test.okta.com")
        assert await provider.healthcheck() is True

    @pytest.mark.asyncio
    async def test_healthcheck_failure(self, mock_requests, mock_creds):
        self._setup(mock_creds)
        mock_requests.get.side_effect = ConnectionError("unreachable")

        provider = OktaIdentityProvider(domain="dev-test.okta.com")
        assert await provider.healthcheck() is False
