from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentic_primitives_gateway.primitives.identity.agentcore import AgentCoreIdentityProvider


@patch("agentic_primitives_gateway.primitives.identity.agentcore.get_boto3_session")
@patch("agentic_primitives_gateway.primitives.identity.agentcore.IdentityClient")
class TestAgentCoreIdentityProvider:
    """Tests for the AgentCore identity provider."""

    # ── get_token ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_token_m2m_returns_access_token(self, mock_identity_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.dp_client.get_resource_oauth2_token.return_value = {
            "accessToken": "access-token-123",
        }
        mock_identity_client_cls.return_value = mock_client

        provider = AgentCoreIdentityProvider(region="us-east-1")
        result = await provider.get_token(
            credential_provider="github",
            workload_token="wt-123",
            auth_flow="M2M",
            scopes=["repo"],
        )

        assert result == {"access_token": "access-token-123", "token_type": "Bearer"}
        mock_client.dp_client.get_resource_oauth2_token.assert_called_once_with(
            resourceCredentialProviderName="github",
            workloadIdentityToken="wt-123",
            oauth2Flow="M2M",
            scopes=["repo"],
        )

    @pytest.mark.asyncio
    async def test_get_token_user_federation_returns_auth_url(self, mock_identity_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.dp_client.get_resource_oauth2_token.return_value = {
            "authorizationUrl": "https://github.com/login/oauth/authorize?...",
            "sessionUri": "session-abc",
        }
        mock_identity_client_cls.return_value = mock_client

        provider = AgentCoreIdentityProvider()
        result = await provider.get_token(
            credential_provider="github",
            workload_token="wt-123",
            auth_flow="USER_FEDERATION",
            callback_url="https://example.com/callback",
        )

        assert result == {
            "authorization_url": "https://github.com/login/oauth/authorize?...",
            "session_uri": "session-abc",
        }

    @pytest.mark.asyncio
    async def test_get_token_with_session_uri(self, mock_identity_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.dp_client.get_resource_oauth2_token.return_value = {
            "accessToken": "polled-token",
        }
        mock_identity_client_cls.return_value = mock_client

        provider = AgentCoreIdentityProvider()
        result = await provider.get_token(
            credential_provider="github",
            workload_token="wt-123",
            session_uri="session-abc",
        )

        assert result["access_token"] == "polled-token"
        call_kwargs = mock_client.dp_client.get_resource_oauth2_token.call_args
        assert call_kwargs.kwargs["sessionUri"] == "session-abc"

    @pytest.mark.asyncio
    async def test_get_token_with_optional_params(self, mock_identity_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.dp_client.get_resource_oauth2_token.return_value = {
            "accessToken": "tok",
        }
        mock_identity_client_cls.return_value = mock_client

        provider = AgentCoreIdentityProvider()
        await provider.get_token(
            credential_provider="test",
            workload_token="wt",
            force_auth=True,
            custom_state="state-xyz",
            custom_parameters={"prompt": "consent"},
        )

        call_kwargs = mock_client.dp_client.get_resource_oauth2_token.call_args.kwargs
        assert call_kwargs["forceAuthentication"] is True
        assert call_kwargs["customState"] == "state-xyz"
        assert call_kwargs["customParameters"] == {"prompt": "consent"}

    @pytest.mark.asyncio
    async def test_get_token_raises_on_unexpected_response(self, mock_identity_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.dp_client.get_resource_oauth2_token.return_value = {}
        mock_identity_client_cls.return_value = mock_client

        provider = AgentCoreIdentityProvider()
        with pytest.raises(RuntimeError, match="did not return a token"):
            await provider.get_token(
                credential_provider="test",
                workload_token="wt",
            )

    # ── get_api_key ───────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_api_key(self, mock_identity_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-west-2")
        mock_client = MagicMock()
        mock_client.dp_client.get_resource_api_key.return_value = {"apiKey": "sk-123"}
        mock_identity_client_cls.return_value = mock_client

        provider = AgentCoreIdentityProvider(region="us-west-2")
        result = await provider.get_api_key(
            credential_provider="openai",
            workload_token="wt-456",
        )

        assert result == {"api_key": "sk-123", "credential_provider": "openai"}

    # ── get_workload_token ────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_workload_token_plain(self, mock_identity_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.get_workload_access_token.return_value = {
            "workloadAccessToken": "wat-789",
        }
        mock_identity_client_cls.return_value = mock_client

        provider = AgentCoreIdentityProvider()
        result = await provider.get_workload_token(workload_name="my-agent")

        assert result == {"workload_token": "wat-789", "workload_name": "my-agent"}
        mock_client.get_workload_access_token.assert_called_once_with(
            workload_name="my-agent",
            user_token=None,
            user_id=None,
        )

    @pytest.mark.asyncio
    async def test_get_workload_token_with_user_token(self, mock_identity_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.get_workload_access_token.return_value = {
            "workloadAccessToken": "wat-user",
        }
        mock_identity_client_cls.return_value = mock_client

        provider = AgentCoreIdentityProvider()
        result = await provider.get_workload_token(
            workload_name="my-agent",
            user_token="user-jwt",
        )

        assert result["workload_token"] == "wat-user"
        mock_client.get_workload_access_token.assert_called_once_with(
            workload_name="my-agent",
            user_token="user-jwt",
            user_id=None,
        )

    @pytest.mark.asyncio
    async def test_get_workload_token_with_user_id(self, mock_identity_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.get_workload_access_token.return_value = {
            "workloadAccessToken": "wat-uid",
        }
        mock_identity_client_cls.return_value = mock_client

        provider = AgentCoreIdentityProvider()
        result = await provider.get_workload_token(
            workload_name="my-agent",
            user_id="user-123",
        )

        assert result["workload_token"] == "wat-uid"

    # ── complete_auth ─────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_complete_auth_with_user_token(self, mock_identity_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_identity_client_cls.return_value = mock_client

        provider = AgentCoreIdentityProvider()
        await provider.complete_auth(
            session_uri="session-abc",
            user_token="user-jwt",
        )

        mock_client.complete_resource_token_auth.assert_called_once()

    @pytest.mark.asyncio
    async def test_complete_auth_with_user_id(self, mock_identity_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_identity_client_cls.return_value = mock_client

        provider = AgentCoreIdentityProvider()
        await provider.complete_auth(
            session_uri="session-abc",
            user_id="user-123",
        )

        mock_client.complete_resource_token_auth.assert_called_once()

    @pytest.mark.asyncio
    async def test_complete_auth_requires_identifier(self, mock_identity_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_identity_client_cls.return_value = MagicMock()

        provider = AgentCoreIdentityProvider()
        with pytest.raises(ValueError, match="Either user_token or user_id"):
            await provider.complete_auth(session_uri="session-abc")

    # ── list_credential_providers ─────────────────────────────

    @pytest.mark.asyncio
    async def test_list_credential_providers(self, mock_identity_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.cp_client.list_oauth2_credential_providers.return_value = {
            "credentialProviders": [{"name": "github", "credentialProviderArn": "arn:github"}],
        }
        mock_client.cp_client.list_api_key_credential_providers.return_value = {
            "credentialProviders": [{"name": "openai", "credentialProviderArn": "arn:openai"}],
        }
        mock_identity_client_cls.return_value = mock_client

        provider = AgentCoreIdentityProvider()
        result = await provider.list_credential_providers()

        assert len(result) == 2
        assert result[0]["name"] == "github"
        assert result[0]["provider_type"] == "oauth2"
        assert result[1]["name"] == "openai"
        assert result[1]["provider_type"] == "api_key"

    # ── create_credential_provider ────────────────────────────

    @pytest.mark.asyncio
    async def test_create_oauth2_credential_provider(self, mock_identity_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.create_oauth2_credential_provider.return_value = {
            "name": "github",
            "credentialProviderArn": "arn:github",
        }
        mock_identity_client_cls.return_value = mock_client

        provider = AgentCoreIdentityProvider()
        result = await provider.create_credential_provider(
            name="github",
            provider_type="oauth2",
            config={"credentialProviderVendor": "GithubOauth2"},
        )

        assert result["name"] == "github"
        assert result["provider_type"] == "oauth2"

    @pytest.mark.asyncio
    async def test_create_api_key_credential_provider(self, mock_identity_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.create_api_key_credential_provider.return_value = {
            "name": "openai",
            "credentialProviderArn": "arn:openai",
        }
        mock_identity_client_cls.return_value = mock_client

        provider = AgentCoreIdentityProvider()
        result = await provider.create_credential_provider(
            name="openai",
            provider_type="api_key",
            config={"apiKey": "sk-123"},
        )

        assert result["name"] == "openai"
        assert result["provider_type"] == "api_key"

    # ── workload identity CRUD ────────────────────────────────

    @pytest.mark.asyncio
    async def test_create_workload_identity(self, mock_identity_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.create_workload_identity.return_value = {
            "name": "my-agent",
            "workloadIdentityArn": "arn:my-agent",
            "allowedResourceOauth2ReturnUrls": ["https://example.com/callback"],
        }
        mock_identity_client_cls.return_value = mock_client

        provider = AgentCoreIdentityProvider()
        result = await provider.create_workload_identity(
            name="my-agent",
            allowed_return_urls=["https://example.com/callback"],
        )

        assert result["name"] == "my-agent"
        assert result["arn"] == "arn:my-agent"
        assert result["allowed_return_urls"] == ["https://example.com/callback"]

    @pytest.mark.asyncio
    async def test_get_workload_identity(self, mock_identity_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.get_workload_identity.return_value = {
            "name": "my-agent",
            "workloadIdentityArn": "arn:my-agent",
            "allowedResourceOauth2ReturnUrls": [],
        }
        mock_identity_client_cls.return_value = mock_client

        provider = AgentCoreIdentityProvider()
        result = await provider.get_workload_identity(name="my-agent")

        assert result["name"] == "my-agent"

    @pytest.mark.asyncio
    async def test_delete_workload_identity(self, mock_identity_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_identity_client_cls.return_value = mock_client

        provider = AgentCoreIdentityProvider()
        await provider.delete_workload_identity(name="my-agent")

        mock_client.cp_client.delete_workload_identity.assert_called_once_with(name="my-agent")

    @pytest.mark.asyncio
    async def test_list_workload_identities(self, mock_identity_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.cp_client.list_workload_identities.return_value = {
            "workloadIdentities": [
                {"name": "agent-1", "workloadIdentityArn": "arn:1"},
                {"name": "agent-2", "workloadIdentityArn": "arn:2"},
            ],
        }
        mock_identity_client_cls.return_value = mock_client

        provider = AgentCoreIdentityProvider()
        result = await provider.list_workload_identities()

        assert len(result) == 2
        assert result[0]["name"] == "agent-1"

    # ── healthcheck / region ──────────────────────────────────

    @pytest.mark.asyncio
    async def test_healthcheck_returns_true(self, mock_identity_client_cls, mock_get_session):
        provider = AgentCoreIdentityProvider()
        assert await provider.healthcheck() is True

    @pytest.mark.asyncio
    async def test_uses_session_region(self, mock_identity_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="eu-west-1")
        mock_client = MagicMock()
        mock_client.dp_client.get_resource_oauth2_token.return_value = {"accessToken": "tok"}
        mock_identity_client_cls.return_value = mock_client

        provider = AgentCoreIdentityProvider(region="us-east-1")
        await provider.get_token(credential_provider="test", workload_token="wt")

        # Should use session's region, not provider config region
        mock_identity_client_cls.assert_called_with(region="eu-west-1")
