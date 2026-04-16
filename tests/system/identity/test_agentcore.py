"""System tests for the AgentCore identity primitive.

Full stack: AgenticPlatformClient → ASGI → middleware → route → registry →
AgentCoreIdentityProvider → (mocked) IdentityClient (dp_client / cp_client).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from agentic_primitives_gateway_client import AgenticPlatformClient

# ── Data plane — token operations ─────────────────────────────────────


class TestGetToken:
    async def test_get_token_m2m(self, client: AgenticPlatformClient, mock_identity_client: MagicMock) -> None:
        mock_identity_client.dp_client.get_resource_oauth2_token.return_value = {
            "accessToken": "at-123",
        }

        result = await client.get_token(
            credential_provider="github",
            workload_token="wt-abc",
        )

        assert result["access_token"] == "at-123"
        assert result["token_type"] == "Bearer"

    async def test_get_token_user_federation(
        self, client: AgenticPlatformClient, mock_identity_client: MagicMock
    ) -> None:
        mock_identity_client.dp_client.get_resource_oauth2_token.return_value = {
            "authorizationUrl": "https://auth.example.com/login",
            "sessionUri": "urn:session:123",
        }

        result = await client.get_token(
            credential_provider="github",
            workload_token="wt-abc",
            auth_flow="USER_FEDERATION",
            callback_url="https://app.example.com/callback",
        )

        assert result["authorization_url"] == "https://auth.example.com/login"
        assert result["session_uri"] == "urn:session:123"


class TestGetApiKey:
    async def test_get_api_key(self, client: AgenticPlatformClient, mock_identity_client: MagicMock) -> None:
        mock_identity_client.dp_client.get_resource_api_key.return_value = {
            "apiKey": "sk-test-123",
        }

        result = await client.get_api_key(
            credential_provider="openai",
            workload_token="wt-abc",
        )

        assert result["api_key"] == "sk-test-123"
        assert result["credential_provider"] == "openai"


class TestGetWorkloadToken:
    async def test_get_workload_token(self, client: AgenticPlatformClient, mock_identity_client: MagicMock) -> None:
        mock_identity_client.get_workload_access_token.return_value = {
            "workloadAccessToken": "wat-xyz",
        }

        result = await client.get_workload_token(workload_name="my-agent")

        assert result["workload_token"] == "wat-xyz"
        assert result["workload_name"] == "my-agent"


class TestCompleteAuth:
    async def test_complete_auth(self, client: AgenticPlatformClient, mock_identity_client: MagicMock) -> None:
        mock_identity_client.complete_resource_token_auth.return_value = None

        await client.complete_auth(
            session_uri="urn:session:123",
            user_token="jwt-token",
        )


# ── Control plane — credential providers ──────────────────────────────


class TestListCredentialProviders:
    async def test_list_credential_providers(
        self, client: AgenticPlatformClient, mock_identity_client: MagicMock
    ) -> None:
        mock_identity_client.cp_client.list_oauth2_credential_providers.return_value = {
            "credentialProviders": [
                {"name": "github", "credentialProviderArn": "arn:aws:...github"},
            ],
        }
        mock_identity_client.cp_client.list_api_key_credential_providers.return_value = {
            "credentialProviders": [
                {"name": "openai", "credentialProviderArn": "arn:aws:...openai"},
            ],
        }

        result = await client.list_credential_providers()

        assert "credential_providers" in result
        names = [p["name"] for p in result["credential_providers"]]
        assert "github" in names
        assert "openai" in names


class TestCreateCredentialProvider:
    async def test_create_oauth2(self, client: AgenticPlatformClient, mock_identity_client: MagicMock) -> None:
        mock_identity_client.create_oauth2_credential_provider.return_value = {
            "name": "github",
            "credentialProviderArn": "arn:aws:...github",
        }

        result = await client.create_credential_provider(
            "github", "oauth2", {"client_id": "abc", "client_secret": "xyz"}
        )

        assert result["name"] == "github"
        assert result["provider_type"] == "oauth2"

    async def test_create_api_key(self, client: AgenticPlatformClient, mock_identity_client: MagicMock) -> None:
        mock_identity_client.create_api_key_credential_provider.return_value = {
            "name": "openai",
            "credentialProviderArn": "arn:aws:...openai",
        }

        result = await client.create_credential_provider("openai", "api_key", {"api_key": "sk-123"})

        assert result["name"] == "openai"
        assert result["provider_type"] == "api_key"


class TestGetCredentialProvider:
    async def test_get_credential_provider(
        self, client: AgenticPlatformClient, mock_identity_client: MagicMock
    ) -> None:
        mock_identity_client.cp_client.get_oauth2_credential_provider.return_value = {
            "name": "github",
            "credentialProviderArn": "arn:aws:...github",
        }

        result = await client.get_credential_provider("github")

        assert result["name"] == "github"


class TestUpdateCredentialProvider:
    async def test_update_credential_provider(
        self, client: AgenticPlatformClient, mock_identity_client: MagicMock
    ) -> None:
        mock_identity_client.cp_client.update_oauth2_credential_provider.return_value = {
            "name": "github",
        }

        result = await client.update_credential_provider("github", {"client_secret": "new-secret"})

        assert result["name"] == "github"


class TestDeleteCredentialProvider:
    async def test_delete_credential_provider(
        self, client: AgenticPlatformClient, mock_identity_client: MagicMock
    ) -> None:
        mock_identity_client.cp_client.delete_oauth2_credential_provider.return_value = None

        await client.delete_credential_provider("github")


# ── Control plane — workload identities ───────────────────────────────


class TestCreateWorkloadIdentity:
    async def test_create_workload_identity(
        self, client: AgenticPlatformClient, mock_identity_client: MagicMock
    ) -> None:
        mock_identity_client.create_workload_identity.return_value = {
            "name": "my-agent",
            "workloadIdentityArn": "arn:aws:...my-agent",
            "allowedResourceOauth2ReturnUrls": ["https://app.example.com/callback"],
        }

        result = await client.create_workload_identity(
            "my-agent",
            allowed_return_urls=["https://app.example.com/callback"],
        )

        assert result["name"] == "my-agent"
        assert result["arn"] == "arn:aws:...my-agent"


class TestGetWorkloadIdentity:
    async def test_get_workload_identity(self, client: AgenticPlatformClient, mock_identity_client: MagicMock) -> None:
        mock_identity_client.get_workload_identity.return_value = {
            "name": "my-agent",
            "workloadIdentityArn": "arn:aws:...my-agent",
            "allowedResourceOauth2ReturnUrls": [],
        }

        result = await client.get_workload_identity("my-agent")

        assert result["name"] == "my-agent"


class TestDeleteWorkloadIdentity:
    async def test_delete_workload_identity(
        self, client: AgenticPlatformClient, mock_identity_client: MagicMock
    ) -> None:
        mock_identity_client.cp_client.delete_workload_identity.return_value = None

        await client.delete_workload_identity("my-agent")


class TestListWorkloadIdentities:
    async def test_list_workload_identities(
        self, client: AgenticPlatformClient, mock_identity_client: MagicMock
    ) -> None:
        mock_identity_client.cp_client.list_workload_identities.return_value = {
            "workloadIdentities": [
                {"name": "agent-1", "workloadIdentityArn": "arn:aws:...1"},
                {"name": "agent-2", "workloadIdentityArn": "arn:aws:...2"},
            ],
        }

        result = await client.list_workload_identities()

        assert "workload_identities" in result
        assert len(result["workload_identities"]) == 2
