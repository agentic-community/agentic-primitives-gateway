from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentic_primitives_gateway.primitives.identity.agentcore import AgentCoreIdentityProvider


@patch("agentic_primitives_gateway.primitives.identity.agentcore.get_boto3_session")
@patch("agentic_primitives_gateway.primitives.identity.agentcore.IdentityClient")
class TestAgentCoreIdentityProvider:
    """Tests for the AgentCore identity provider."""

    @pytest.mark.asyncio
    async def test_get_token(self, mock_identity_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.get_token.return_value = "access-token-123"
        mock_identity_client_cls.return_value = mock_client

        provider = AgentCoreIdentityProvider(region="us-east-1")
        result = await provider.get_token(
            provider_name="github",
            scopes=["repo"],
            context={"agent_identity_token": "agent-token"},
        )

        assert result == {"access_token": "access-token-123", "token_type": "Bearer"}
        mock_client.get_token.assert_called_once_with(
            provider_name="github",
            agent_identity_token="agent-token",
            scopes=["repo"],
        )

    @pytest.mark.asyncio
    async def test_get_token_no_context(self, mock_identity_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.get_token.return_value = "token-456"
        mock_identity_client_cls.return_value = mock_client

        provider = AgentCoreIdentityProvider()
        result = await provider.get_token(provider_name="slack")

        assert result["access_token"] == "token-456"
        mock_client.get_token.assert_called_once_with(
            provider_name="slack",
            agent_identity_token="",
            scopes=None,
        )

    @pytest.mark.asyncio
    async def test_get_api_key(self, mock_identity_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-west-2")
        mock_client = MagicMock()
        mock_client.get_api_key.return_value = "api-key-789"
        mock_identity_client_cls.return_value = mock_client

        provider = AgentCoreIdentityProvider(region="us-west-2")
        result = await provider.get_api_key(
            provider_name="openai",
            context={"agent_identity_token": "my-token"},
        )

        assert result == {"api_key": "api-key-789", "provider_name": "openai"}
        mock_identity_client_cls.assert_called_with(region="us-west-2")

    @pytest.mark.asyncio
    async def test_get_api_key_no_context(self, mock_identity_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.get_api_key.return_value = "key"
        mock_identity_client_cls.return_value = mock_client

        provider = AgentCoreIdentityProvider()
        result = await provider.get_api_key(provider_name="anthropic")

        assert result["api_key"] == "key"
        mock_client.get_api_key.assert_called_once_with(
            provider_name="anthropic",
            agent_identity_token="",
        )

    @pytest.mark.asyncio
    async def test_list_providers_returns_empty(self, mock_identity_client_cls, mock_get_session):
        provider = AgentCoreIdentityProvider()
        result = await provider.list_providers()
        assert result == []

    @pytest.mark.asyncio
    async def test_healthcheck_returns_true(self, mock_identity_client_cls, mock_get_session):
        provider = AgentCoreIdentityProvider()
        assert await provider.healthcheck() is True

    @pytest.mark.asyncio
    async def test_uses_session_region(self, mock_identity_client_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="eu-west-1")
        mock_client = MagicMock()
        mock_client.get_token.return_value = "tok"
        mock_identity_client_cls.return_value = mock_client

        provider = AgentCoreIdentityProvider(region="us-east-1")
        await provider.get_token(provider_name="test")

        # Should use session's region, not provider config region
        mock_identity_client_cls.assert_called_with(region="eu-west-1")
