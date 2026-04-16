"""System tests for the Keycloak identity primitive.

Full stack: AgenticPlatformClient -> ASGI -> middleware -> route -> registry ->
KeycloakIdentityProvider -> (mocked) KeycloakAdmin + KeycloakOpenID.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import agentic_primitives_gateway.config as _config_module
from agentic_primitives_gateway.config import Settings
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway_client import AgenticPlatformClient

# ── Registry override ────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _init_registry():
    """Initialise registry with Keycloak identity provider (noop for everything else)."""
    with (
        patch("agentic_primitives_gateway.primitives.identity.keycloak.KeycloakAdmin"),
        patch("agentic_primitives_gateway.primitives.identity.keycloak.KeycloakOpenID"),
    ):
        test_settings = Settings(
            allow_server_credentials=True,
            providers={
                "memory": {
                    "backend": "agentic_primitives_gateway.primitives.memory.noop.NoopMemoryProvider",
                    "config": {},
                },
                "observability": {
                    "backend": "agentic_primitives_gateway.primitives.observability.noop.NoopObservabilityProvider",
                    "config": {},
                },
                "llm": {
                    "backend": "agentic_primitives_gateway.primitives.llm.noop.NoopLLMProvider",
                    "config": {},
                },
                "tools": {
                    "backend": "agentic_primitives_gateway.primitives.tools.noop.NoopToolsProvider",
                    "config": {},
                },
                "identity": {
                    "backend": ("agentic_primitives_gateway.primitives.identity.keycloak.KeycloakIdentityProvider"),
                    "config": {
                        "server_url": "http://localhost:8080",
                        "realm": "test",
                        "client_id": "test-client",
                        "client_secret": "test-secret",
                    },
                },
                "code_interpreter": {
                    "backend": "agentic_primitives_gateway.primitives.code_interpreter.noop.NoopCodeInterpreterProvider",
                    "config": {},
                },
                "browser": {
                    "backend": "agentic_primitives_gateway.primitives.browser.noop.NoopBrowserProvider",
                    "config": {},
                },
            },
        )
        orig_settings = _config_module.settings
        _config_module.settings = test_settings
        registry.initialize(test_settings)

    yield

    _config_module.settings = orig_settings


# ── Helpers ──────────────────────────────────────────────────────────

_KC_ADMIN_PATCH = "agentic_primitives_gateway.primitives.identity.keycloak.KeycloakAdmin"
_KC_OPENID_PATCH = "agentic_primitives_gateway.primitives.identity.keycloak.KeycloakOpenID"


# ── Data plane -- token operations ───────────────────────────────────


class TestGetToken:
    async def test_get_token_m2m(self, client: AgenticPlatformClient) -> None:
        mock_openid = MagicMock()
        mock_openid.exchange_token.return_value = {"access_token": "at-1"}

        with patch(_KC_OPENID_PATCH, return_value=mock_openid):
            result = await client.get_token(
                credential_provider="github",
                workload_token="wt-abc",
            )

        assert result["access_token"] == "at-1"
        assert result["token_type"] == "Bearer"

    async def test_get_token_user_federation(self, client: AgenticPlatformClient) -> None:
        mock_openid = MagicMock()
        mock_openid.auth_url.return_value = "http://localhost:8080/auth/realms/test/protocol/openid-connect/auth?..."

        with patch(_KC_OPENID_PATCH, return_value=mock_openid):
            result = await client.get_token(
                credential_provider="github",
                workload_token="wt-abc",
                auth_flow="USER_FEDERATION",
                callback_url="https://app.example.com/callback",
            )

        assert "authorization_url" in result
        assert "session_uri" in result


class TestGetApiKey:
    async def test_get_api_key(self, client: AgenticPlatformClient) -> None:
        mock_admin = MagicMock()
        mock_admin.get_client_id.return_value = "client-uuid-123"
        mock_admin.get_client_secrets.return_value = {"value": "sk-1"}

        with patch(_KC_ADMIN_PATCH, return_value=mock_admin):
            result = await client.get_api_key(
                credential_provider="openai",
                workload_token="wt-abc",
            )

        assert result["api_key"] == "sk-1"
        assert result["credential_provider"] == "openai"


class TestGetWorkloadToken:
    async def test_get_workload_token(self, client: AgenticPlatformClient) -> None:
        mock_openid = MagicMock()
        mock_openid.token.return_value = {"access_token": "wt-1"}

        with patch(_KC_OPENID_PATCH, return_value=mock_openid):
            result = await client.get_workload_token(workload_name="my-agent")

        assert result["workload_token"] == "wt-1"
        assert result["workload_name"] == "my-agent"


# ── Control plane -- credential providers ────────────────────────────


class TestListCredentialProviders:
    async def test_list_credential_providers(self, client: AgenticPlatformClient) -> None:
        mock_admin = MagicMock()
        mock_admin.get_idps.return_value = [
            {"alias": "github-idp", "providerId": "oidc"},
        ]
        mock_admin.get_clients.return_value = [
            {
                "clientId": "openai-keys",
                "publicClient": False,
                "serviceAccountsEnabled": True,
            },
        ]

        with patch(_KC_ADMIN_PATCH, return_value=mock_admin):
            result = await client.list_credential_providers()

        assert "credential_providers" in result
        names = [p["name"] for p in result["credential_providers"]]
        assert "github-idp" in names
        assert "openai-keys" in names


class TestCreateCredentialProvider:
    async def test_create_credential_provider_oauth2(self, client: AgenticPlatformClient) -> None:
        mock_admin = MagicMock()
        mock_admin.create_idp.return_value = "idp-created"

        with patch(_KC_ADMIN_PATCH, return_value=mock_admin):
            result = await client.create_credential_provider(
                "github", "oauth2", {"provider_id": "oidc", "client_id": "abc"}
            )

        assert result["name"] == "github"
        assert result["provider_type"] == "oauth2"

    async def test_create_credential_provider_api_key(self, client: AgenticPlatformClient) -> None:
        mock_admin = MagicMock()
        mock_admin.create_client.return_value = "client-uuid-new"

        with patch(_KC_ADMIN_PATCH, return_value=mock_admin):
            result = await client.create_credential_provider("openai", "api_key", {"api_key": "sk-123"})

        assert result["name"] == "openai"
        assert result["provider_type"] == "api_key"


class TestGetCredentialProvider:
    async def test_get_credential_provider(self, client: AgenticPlatformClient) -> None:
        mock_admin = MagicMock()
        mock_admin.get_idp.return_value = {"alias": "github", "providerId": "oidc"}

        with patch(_KC_ADMIN_PATCH, return_value=mock_admin):
            result = await client.get_credential_provider("github")

        assert result["name"] == "github"
        assert result["provider_type"] == "oauth2"


class TestUpdateCredentialProvider:
    async def test_update_credential_provider(self, client: AgenticPlatformClient) -> None:
        mock_admin = MagicMock()
        mock_admin.update_idp.return_value = None

        with patch(_KC_ADMIN_PATCH, return_value=mock_admin):
            result = await client.update_credential_provider("github", {"client_secret": "new-secret"})

        assert result["name"] == "github"
        assert result["provider_type"] == "oauth2"


class TestDeleteCredentialProvider:
    async def test_delete_credential_provider(self, client: AgenticPlatformClient) -> None:
        mock_admin = MagicMock()
        mock_admin.delete_idp.return_value = None

        with patch(_KC_ADMIN_PATCH, return_value=mock_admin):
            await client.delete_credential_provider("github")

        mock_admin.delete_idp.assert_called_once_with("github")


# ── Control plane -- workload identities ─────────────────────────────


class TestCreateWorkloadIdentity:
    async def test_create_workload_identity(self, client: AgenticPlatformClient) -> None:
        mock_admin = MagicMock()
        mock_admin.create_client.return_value = "client-uuid-new"

        with patch(_KC_ADMIN_PATCH, return_value=mock_admin):
            result = await client.create_workload_identity(
                "my-agent",
                allowed_return_urls=["https://app.example.com/callback"],
            )

        assert result["name"] == "my-agent"
        assert result["arn"] == "client-uuid-new"


class TestGetWorkloadIdentity:
    async def test_get_workload_identity(self, client: AgenticPlatformClient) -> None:
        mock_admin = MagicMock()
        mock_admin.get_client_id.return_value = "client-uuid-1"
        mock_admin.get_client.return_value = {
            "clientId": "my-agent",
            "redirectUris": [],
        }

        with patch(_KC_ADMIN_PATCH, return_value=mock_admin):
            result = await client.get_workload_identity("my-agent")

        assert result["name"] == "my-agent"
        assert result["arn"] == "client-uuid-1"


class TestDeleteWorkloadIdentity:
    async def test_delete_workload_identity(self, client: AgenticPlatformClient) -> None:
        mock_admin = MagicMock()
        mock_admin.get_client_id.return_value = "client-uuid-1"
        mock_admin.delete_client.return_value = None

        with patch(_KC_ADMIN_PATCH, return_value=mock_admin):
            await client.delete_workload_identity("my-agent")

        mock_admin.delete_client.assert_called_once_with("client-uuid-1")


class TestListWorkloadIdentities:
    async def test_list_workload_identities(self, client: AgenticPlatformClient) -> None:
        mock_admin = MagicMock()
        mock_admin.get_clients.return_value = [
            {
                "id": "uuid-1",
                "clientId": "agent-1",
                "serviceAccountsEnabled": True,
                "redirectUris": [],
            },
            {
                "id": "uuid-2",
                "clientId": "agent-2",
                "serviceAccountsEnabled": True,
                "redirectUris": ["https://example.com/callback"],
            },
            {
                "id": "uuid-3",
                "clientId": "public-app",
                "serviceAccountsEnabled": False,
                "redirectUris": [],
            },
        ]

        with patch(_KC_ADMIN_PATCH, return_value=mock_admin):
            result = await client.list_workload_identities()

        assert "workload_identities" in result
        # Only the two service-account-enabled clients
        assert len(result["workload_identities"]) == 2
        names = [wi["name"] for wi in result["workload_identities"]]
        assert "agent-1" in names
        assert "agent-2" in names
