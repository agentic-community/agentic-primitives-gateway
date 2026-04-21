"""System tests for the Okta identity primitive.

Full stack: AgenticPlatformClient -> ASGI -> middleware -> route -> registry ->
OktaIdentityProvider -> (mocked) requests.
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
    """Initialise registry with Okta identity provider (noop for everything else)."""
    test_settings = Settings(
        allow_server_credentials="always",
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
                "backend": ("agentic_primitives_gateway.primitives.identity.okta.OktaIdentityProvider"),
                "config": {
                    "domain": "dev-test.okta.com",
                    "client_id": "test-client",
                    "client_secret": "test-secret",
                    "api_token": "test-ssws-token",
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

_REQUESTS_PATCH = "agentic_primitives_gateway.primitives.identity.okta.requests"


def _mock_response(data: dict | list | None = None, status_code: int = 200) -> MagicMock:
    """Build a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data if data is not None else {}
    resp.raise_for_status.return_value = None
    return resp


# ── Data plane -- token operations ───────────────────────────────────


class TestGetToken:
    async def test_get_token_m2m(self, client: AgenticPlatformClient) -> None:
        token_resp = _mock_response({"access_token": "at-1", "token_type": "Bearer"})

        with patch(_REQUESTS_PATCH) as mock_requests:
            mock_requests.post.return_value = token_resp
            result = await client.get_token(
                credential_provider="github",
                workload_token="wt-abc",
            )

        assert result["access_token"] == "at-1"
        assert result["token_type"] == "Bearer"

    async def test_get_token_user_federation(self, client: AgenticPlatformClient) -> None:
        # No mock needed: Okta constructs the URL from config without calling the network
        result = await client.get_token(
            credential_provider="github",
            workload_token="wt-abc",
            auth_flow="USER_FEDERATION",
            callback_url="https://app.example.com/callback",
        )

        assert "authorization_url" in result
        assert "session_uri" in result
        assert "dev-test.okta.com" in result["authorization_url"]


class TestGetApiKey:
    async def test_get_api_key(self, client: AgenticPlatformClient) -> None:
        apps_resp = _mock_response(
            [
                {
                    "id": "app-1",
                    "label": "openai",
                    "credentials": {
                        "oauthClient": {"client_secret": "sk-test-123"},
                    },
                }
            ]
        )

        with patch(_REQUESTS_PATCH) as mock_requests:
            mock_requests.get.return_value = apps_resp
            result = await client.get_api_key(
                credential_provider="openai",
                workload_token="wt-abc",
            )

        assert result["api_key"] == "sk-test-123"
        assert result["credential_provider"] == "openai"


class TestGetWorkloadToken:
    async def test_get_workload_token(self, client: AgenticPlatformClient) -> None:
        token_resp = _mock_response({"access_token": "wt-1"})

        with patch(_REQUESTS_PATCH) as mock_requests:
            mock_requests.post.return_value = token_resp
            result = await client.get_workload_token(workload_name="my-agent")

        assert result["workload_token"] == "wt-1"
        assert result["workload_name"] == "my-agent"


# ── Control plane -- credential providers ────────────────────────────


class TestListCredentialProviders:
    async def test_list_credential_providers(self, client: AgenticPlatformClient) -> None:
        idps_resp = _mock_response(
            [
                {"name": "github-idp", "type": "OIDC", "id": "idp-1"},
            ]
        )
        apps_resp = _mock_response(
            [
                {
                    "id": "app-1",
                    "label": "openai-keys",
                    "signOnMode": "OPENID_CONNECT",
                },
            ]
        )

        with patch(_REQUESTS_PATCH) as mock_requests:
            mock_requests.get.side_effect = [idps_resp, apps_resp]
            result = await client.list_credential_providers()

        assert "credential_providers" in result
        names = [p["name"] for p in result["credential_providers"]]
        assert "github-idp" in names
        assert "openai-keys" in names


class TestCreateCredentialProvider:
    async def test_create_credential_provider_oauth2(self, client: AgenticPlatformClient) -> None:
        create_resp = _mock_response({"id": "idp-new", "name": "github"})

        with patch(_REQUESTS_PATCH) as mock_requests:
            mock_requests.post.return_value = create_resp
            result = await client.create_credential_provider("github", "oauth2", {"type": "OIDC"})

        assert result["name"] == "github"
        assert result["provider_type"] == "oauth2"

    async def test_create_credential_provider_api_key(self, client: AgenticPlatformClient) -> None:
        create_resp = _mock_response({"id": "app-new", "label": "openai"})

        with patch(_REQUESTS_PATCH) as mock_requests:
            mock_requests.post.return_value = create_resp
            result = await client.create_credential_provider(
                "openai", "api_key", {"client_id": "abc", "api_key": "sk-123"}
            )

        assert result["name"] == "openai"
        assert result["provider_type"] == "api_key"


class TestGetCredentialProvider:
    async def test_get_credential_provider(self, client: AgenticPlatformClient) -> None:
        idps_resp = _mock_response([{"id": "idp-1", "name": "github", "type": "OIDC"}])

        with patch(_REQUESTS_PATCH) as mock_requests:
            mock_requests.get.return_value = idps_resp
            result = await client.get_credential_provider("github")

        assert result["name"] == "github"
        assert result["provider_type"] == "oauth2"


class TestUpdateCredentialProvider:
    async def test_update_credential_provider(self, client: AgenticPlatformClient) -> None:
        idps_resp = _mock_response([{"id": "idp-1", "name": "github"}])
        put_resp = _mock_response({})

        with patch(_REQUESTS_PATCH) as mock_requests:
            mock_requests.get.return_value = idps_resp
            mock_requests.put.return_value = put_resp
            result = await client.update_credential_provider("github", {"client_secret": "new-secret"})

        assert result["name"] == "github"
        assert result["provider_type"] == "oauth2"


class TestDeleteCredentialProvider:
    async def test_delete_credential_provider(self, client: AgenticPlatformClient) -> None:
        idps_resp = _mock_response([{"id": "idp-1", "name": "github"}])
        delete_resp = _mock_response(status_code=204)

        with patch(_REQUESTS_PATCH) as mock_requests:
            mock_requests.get.return_value = idps_resp
            mock_requests.delete.return_value = delete_resp
            await client.delete_credential_provider("github")


# ── Control plane -- workload identities ─────────────────────────────


class TestCreateWorkloadIdentity:
    async def test_create_workload_identity(self, client: AgenticPlatformClient) -> None:
        create_resp = _mock_response(
            {
                "id": "app-new",
                "label": "my-agent",
                "settings": {
                    "oauthClient": {
                        "redirect_uris": ["https://app.example.com/callback"],
                    }
                },
            }
        )

        with patch(_REQUESTS_PATCH) as mock_requests:
            mock_requests.post.return_value = create_resp
            result = await client.create_workload_identity(
                "my-agent",
                allowed_return_urls=["https://app.example.com/callback"],
            )

        assert result["name"] == "my-agent"
        assert result["arn"] == "app-new"


class TestGetWorkloadIdentity:
    async def test_get_workload_identity(self, client: AgenticPlatformClient) -> None:
        apps_resp = _mock_response(
            [
                {
                    "id": "app-1",
                    "label": "my-agent",
                    "settings": {
                        "oauthClient": {"redirect_uris": []},
                    },
                }
            ]
        )

        with patch(_REQUESTS_PATCH) as mock_requests:
            mock_requests.get.return_value = apps_resp
            result = await client.get_workload_identity("my-agent")

        assert result["name"] == "my-agent"
        assert result["arn"] == "app-1"


class TestDeleteWorkloadIdentity:
    async def test_delete_workload_identity(self, client: AgenticPlatformClient) -> None:
        apps_resp = _mock_response([{"id": "app-1", "label": "my-agent"}])
        delete_resp = _mock_response(status_code=204)

        with patch(_REQUESTS_PATCH) as mock_requests:
            mock_requests.get.return_value = apps_resp
            mock_requests.delete.return_value = delete_resp
            await client.delete_workload_identity("my-agent")


class TestListWorkloadIdentities:
    async def test_list_workload_identities(self, client: AgenticPlatformClient) -> None:
        apps_resp = _mock_response(
            [
                {
                    "id": "app-1",
                    "label": "agent-1",
                    "signOnMode": "OPENID_CONNECT",
                    "settings": {"oauthClient": {"redirect_uris": []}},
                },
                {
                    "id": "app-2",
                    "label": "agent-2",
                    "signOnMode": "OPENID_CONNECT",
                    "settings": {
                        "oauthClient": {
                            "redirect_uris": ["https://example.com/callback"],
                        }
                    },
                },
                {
                    "id": "app-3",
                    "label": "saml-app",
                    "signOnMode": "SAML_2_0",
                    "settings": {},
                },
            ]
        )

        with patch(_REQUESTS_PATCH) as mock_requests:
            mock_requests.get.return_value = apps_resp
            result = await client.list_workload_identities()

        assert "workload_identities" in result
        # Only OPENID_CONNECT apps
        assert len(result["workload_identities"]) == 2
        names = [wi["name"] for wi in result["workload_identities"]]
        assert "agent-1" in names
        assert "agent-2" in names
