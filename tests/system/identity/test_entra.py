"""System tests for the Entra identity primitive.

Full stack: AgenticPlatformClient -> ASGI -> middleware -> route -> registry ->
EntraIdentityProvider -> (mocked) msal.ConfidentialClientApplication + requests.
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
    """Initialise registry with Entra identity provider (noop for everything else)."""
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
                "backend": ("agentic_primitives_gateway.primitives.identity.entra.EntraIdentityProvider"),
                "config": {
                    "tenant_id": "test-tenant",
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

_MSAL_PATCH = "agentic_primitives_gateway.primitives.identity.entra.msal.ConfidentialClientApplication"
_REQUESTS_PATCH = "agentic_primitives_gateway.primitives.identity.entra.requests"


def _mock_graph_headers(mock_app: MagicMock) -> None:
    """Set up mock_app so _graph_headers() returns a valid Authorization header."""
    mock_app.acquire_token_for_client.return_value = {"access_token": "graph-token-123"}


def _mock_requests_response(data: dict | list | None = None, status_code: int = 200) -> MagicMock:
    """Build a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data if data is not None else {}
    resp.raise_for_status.return_value = None
    return resp


# ── Data plane -- token operations ───────────────────────────────────


class TestGetToken:
    async def test_get_token_m2m(self, client: AgenticPlatformClient) -> None:
        mock_app = MagicMock()
        mock_app.acquire_token_on_behalf_of.return_value = {"access_token": "at-1"}

        with patch(_MSAL_PATCH, return_value=mock_app):
            result = await client.get_token(
                credential_provider="github",
                workload_token="wt-abc",
            )

        assert result["access_token"] == "at-1"
        assert result["token_type"] == "Bearer"

    async def test_get_token_user_federation(self, client: AgenticPlatformClient) -> None:
        mock_app = MagicMock()
        mock_app.get_authorization_request_url.return_value = "https://login.microsoft.com/authorize?..."

        with patch(_MSAL_PATCH, return_value=mock_app):
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
        mock_app = MagicMock()
        _mock_graph_headers(mock_app)

        apps_resp = _mock_requests_response({"value": [{"id": "app-123", "displayName": "openai"}]})
        app_detail_resp = _mock_requests_response({"passwordCredentials": [{"secretText": "sk-test-123"}]})

        with (
            patch(_MSAL_PATCH, return_value=mock_app),
            patch(_REQUESTS_PATCH) as mock_requests,
        ):
            mock_requests.get.side_effect = [apps_resp, app_detail_resp]

            result = await client.get_api_key(
                credential_provider="openai",
                workload_token="wt-abc",
            )

        assert result["api_key"] == "sk-test-123"
        assert result["credential_provider"] == "openai"


class TestGetWorkloadToken:
    async def test_get_workload_token(self, client: AgenticPlatformClient) -> None:
        mock_app = MagicMock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "wt-1"}

        with patch(_MSAL_PATCH, return_value=mock_app):
            result = await client.get_workload_token(workload_name="my-agent")

        assert result["workload_token"] == "wt-1"
        assert result["workload_name"] == "my-agent"


# ── Control plane -- credential providers ────────────────────────────


class TestListCredentialProviders:
    async def test_list_credential_providers(self, client: AgenticPlatformClient) -> None:
        mock_app = MagicMock()
        _mock_graph_headers(mock_app)

        sps_resp = _mock_requests_response(
            {
                "value": [
                    {
                        "displayName": "github",
                        "appId": "app-1",
                        "servicePrincipalType": "Application",
                    }
                ]
            }
        )

        with (
            patch(_MSAL_PATCH, return_value=mock_app),
            patch(_REQUESTS_PATCH) as mock_requests,
        ):
            mock_requests.get.return_value = sps_resp
            result = await client.list_credential_providers()

        assert "credential_providers" in result
        names = [p["name"] for p in result["credential_providers"]]
        assert "github" in names


class TestCreateCredentialProvider:
    async def test_create_credential_provider_oauth2(self, client: AgenticPlatformClient) -> None:
        mock_app = MagicMock()
        _mock_graph_headers(mock_app)

        sp_resp = _mock_requests_response({"id": "sp-123", "displayName": "github"})

        with (
            patch(_MSAL_PATCH, return_value=mock_app),
            patch(_REQUESTS_PATCH) as mock_requests,
        ):
            mock_requests.post.return_value = sp_resp
            result = await client.create_credential_provider("github", "oauth2", {"app_id": "abc"})

        assert result["name"] == "github"
        assert result["provider_type"] == "oauth2"

    async def test_create_credential_provider_api_key(self, client: AgenticPlatformClient) -> None:
        mock_app = MagicMock()
        _mock_graph_headers(mock_app)

        app_resp = _mock_requests_response({"id": "app-456", "displayName": "openai"})
        password_resp = _mock_requests_response({})

        with (
            patch(_MSAL_PATCH, return_value=mock_app),
            patch(_REQUESTS_PATCH) as mock_requests,
        ):
            mock_requests.post.side_effect = [app_resp, password_resp]
            result = await client.create_credential_provider("openai", "api_key", {"api_key": "sk-123"})

        assert result["name"] == "openai"
        assert result["provider_type"] == "api_key"


class TestGetCredentialProvider:
    async def test_get_credential_provider(self, client: AgenticPlatformClient) -> None:
        mock_app = MagicMock()
        _mock_graph_headers(mock_app)

        apps_resp = _mock_requests_response({"value": [{"id": "app-1", "displayName": "github"}]})

        with (
            patch(_MSAL_PATCH, return_value=mock_app),
            patch(_REQUESTS_PATCH) as mock_requests,
        ):
            mock_requests.get.return_value = apps_resp
            result = await client.get_credential_provider("github")

        assert result["name"] == "github"


class TestUpdateCredentialProvider:
    async def test_update_credential_provider(self, client: AgenticPlatformClient) -> None:
        mock_app = MagicMock()
        _mock_graph_headers(mock_app)

        apps_resp = _mock_requests_response({"value": [{"id": "app-1", "displayName": "github"}]})
        patch_resp = _mock_requests_response({})

        with (
            patch(_MSAL_PATCH, return_value=mock_app),
            patch(_REQUESTS_PATCH) as mock_requests,
        ):
            mock_requests.get.return_value = apps_resp
            mock_requests.patch.return_value = patch_resp
            result = await client.update_credential_provider("github", {"client_secret": "new-secret"})

        assert result["name"] == "github"


class TestDeleteCredentialProvider:
    async def test_delete_credential_provider(self, client: AgenticPlatformClient) -> None:
        mock_app = MagicMock()
        _mock_graph_headers(mock_app)

        apps_resp = _mock_requests_response({"value": [{"id": "app-1", "displayName": "github"}]})
        delete_resp = _mock_requests_response(status_code=204)

        with (
            patch(_MSAL_PATCH, return_value=mock_app),
            patch(_REQUESTS_PATCH) as mock_requests,
        ):
            mock_requests.get.return_value = apps_resp
            mock_requests.delete.return_value = delete_resp
            await client.delete_credential_provider("github")


# ── Control plane -- workload identities ─────────────────────────────


class TestCreateWorkloadIdentity:
    async def test_create_workload_identity(self, client: AgenticPlatformClient) -> None:
        mock_app = MagicMock()
        _mock_graph_headers(mock_app)

        app_resp = _mock_requests_response(
            {
                "id": "app-new",
                "displayName": "my-agent",
                "web": {"redirectUris": ["https://app.example.com/callback"]},
            }
        )

        with (
            patch(_MSAL_PATCH, return_value=mock_app),
            patch(_REQUESTS_PATCH) as mock_requests,
        ):
            mock_requests.post.return_value = app_resp
            result = await client.create_workload_identity(
                "my-agent",
                allowed_return_urls=["https://app.example.com/callback"],
            )

        assert result["name"] == "my-agent"
        assert result["arn"] == "app-new"


class TestGetWorkloadIdentity:
    async def test_get_workload_identity(self, client: AgenticPlatformClient) -> None:
        mock_app = MagicMock()
        _mock_graph_headers(mock_app)

        apps_resp = _mock_requests_response(
            {
                "value": [
                    {
                        "id": "app-1",
                        "displayName": "my-agent",
                        "web": {"redirectUris": []},
                    }
                ]
            }
        )

        with (
            patch(_MSAL_PATCH, return_value=mock_app),
            patch(_REQUESTS_PATCH) as mock_requests,
        ):
            mock_requests.get.return_value = apps_resp
            result = await client.get_workload_identity("my-agent")

        assert result["name"] == "my-agent"


class TestDeleteWorkloadIdentity:
    async def test_delete_workload_identity(self, client: AgenticPlatformClient) -> None:
        mock_app = MagicMock()
        _mock_graph_headers(mock_app)

        apps_resp = _mock_requests_response(
            {"value": [{"id": "app-1", "displayName": "my-agent", "web": {"redirectUris": []}}]}
        )
        delete_resp = _mock_requests_response(status_code=204)

        with (
            patch(_MSAL_PATCH, return_value=mock_app),
            patch(_REQUESTS_PATCH) as mock_requests,
        ):
            mock_requests.get.return_value = apps_resp
            mock_requests.delete.return_value = delete_resp
            await client.delete_workload_identity("my-agent")


class TestListWorkloadIdentities:
    async def test_list_workload_identities(self, client: AgenticPlatformClient) -> None:
        mock_app = MagicMock()
        _mock_graph_headers(mock_app)

        apps_resp = _mock_requests_response(
            {
                "value": [
                    {"id": "app-1", "displayName": "agent-1", "web": {"redirectUris": []}},
                    {"id": "app-2", "displayName": "agent-2", "web": {"redirectUris": []}},
                ]
            }
        )

        with (
            patch(_MSAL_PATCH, return_value=mock_app),
            patch(_REQUESTS_PATCH) as mock_requests,
        ):
            mock_requests.get.return_value = apps_resp
            result = await client.list_workload_identities()

        assert "workload_identities" in result
        assert len(result["workload_identities"]) == 2
