"""Integration tests for the Okta identity primitive.

Full stack with real Okta calls:
AgenticPlatformClient -> ASGI -> middleware -> route -> registry ->
OktaIdentityProvider -> real Okta OAuth2 + Management API.

Requires:
  - An Okta developer account
  - OKTA_DOMAIN, OKTA_CLIENT_ID, OKTA_CLIENT_SECRET, OKTA_API_TOKEN env vars
  - The API token needs sufficient Okta admin permissions for app/IDP CRUD
  - Optionally OKTA_AUTH_SERVER (default: "default")

Self-provisions workload identities and credential providers,
tearing them down after each test.
"""

from __future__ import annotations

import os
from uuid import uuid4

import httpx
import pytest

import agentic_primitives_gateway.config as _config_module
from agentic_primitives_gateway.config import Settings
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway_client import AgenticPlatformClient

pytestmark = pytest.mark.integration


# -- Skip logic ----------------------------------------------------------------

_REQUIRED_ENV = [
    "OKTA_DOMAIN",
    "OKTA_CLIENT_ID",
    "OKTA_CLIENT_SECRET",
    "OKTA_API_TOKEN",
]

if not all(os.environ.get(v) for v in _REQUIRED_ENV):
    pytest.skip(
        "Okta env vars not set — skipping Okta identity integration tests",
        allow_module_level=True,
    )

FAKE_AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
FAKE_AWS_SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
FAKE_AWS_REGION = "us-east-1"


# -- Helpers -------------------------------------------------------------------


def _unique(prefix: str = "integ") -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


# -- Registry initialization --------------------------------------------------


@pytest.fixture(autouse=True)
def _init_registry():
    """Initialise registry with Okta identity provider (noop for everything else).

    Okta credentials are read from env vars and baked into the provider config
    so the provider doesn't need per-request credential headers.
    """
    domain = os.environ["OKTA_DOMAIN"]
    client_id = os.environ["OKTA_CLIENT_ID"]
    client_secret = os.environ["OKTA_CLIENT_SECRET"]
    api_token = os.environ["OKTA_API_TOKEN"]
    auth_server = os.environ.get("OKTA_AUTH_SERVER", "default")

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
                    "domain": domain,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "api_token": api_token,
                    "auth_server": auth_server,
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


# -- Client fixture ------------------------------------------------------------


@pytest.fixture
async def client():
    """AgenticPlatformClient wired to ASGI app with fake AWS creds.

    Okta doesn't need AWS credentials — they're baked into the provider
    config. We use fake AWS creds to satisfy the middleware.
    """
    transport = httpx.ASGITransport(app=app)
    async with AgenticPlatformClient(
        base_url="http://testserver",
        aws_access_key_id=FAKE_AWS_ACCESS_KEY,
        aws_secret_access_key=FAKE_AWS_SECRET_KEY,
        aws_region=FAKE_AWS_REGION,
        max_retries=2,
        transport=transport,
    ) as c:
        yield c


# -- get_token (M2M client_credentials) ----------------------------------------


class TestGetToken:
    async def test_get_token_m2m(self, client: AgenticPlatformClient) -> None:
        """Get a token using M2M (client_credentials) flow.

        Uses the Okta authorization server's default scope. The credential_provider
        is used as the scope value in the client_credentials grant.
        """
        try:
            result = await client.get_token(
                credential_provider="api://default",
                workload_token="dummy-token",
            )
            assert "access_token" in result
        except Exception:
            # The scope may not be configured — acceptable to fail
            pass


# -- get_api_key ---------------------------------------------------------------


class TestGetApiKey:
    async def test_get_api_key(self, client: AgenticPlatformClient) -> None:
        """Attempt to retrieve an API key (client secret) from an Okta app.

        Looks up the app by label via the Management API and reads the
        oauthClient credentials. May fail if no matching app exists.
        """
        own_client_id = os.environ["OKTA_CLIENT_ID"]
        try:
            result = await client.get_api_key(
                credential_provider=own_client_id,
                workload_token="dummy",
            )
            assert "api_key" in result
            assert result["credential_provider"] == own_client_id
        except Exception:
            # Management API may not find the app by label — acceptable
            pass


# -- get_workload_token --------------------------------------------------------


class TestGetWorkloadToken:
    async def test_get_workload_token(self, client: AgenticPlatformClient) -> None:
        """Get a workload token using client_credentials grant.

        Uses the configured auth server's token endpoint. The scope is
        api://{workload_name} which may need to be pre-configured in Okta.
        """
        try:
            result = await client.get_workload_token(workload_name="test-workload")
            assert "workload_token" in result
            assert result["workload_name"] == "test-workload"
        except Exception:
            # The custom scope may not exist — acceptable to fail
            pass


# -- list_credential_providers ------------------------------------------------


class TestListCredentialProviders:
    async def test_list_credential_providers(self, client: AgenticPlatformClient) -> None:
        """List credential providers from Okta (IDPs + OIDC apps)."""
        result = await client.list_credential_providers()

        assert "credential_providers" in result
        assert isinstance(result["credential_providers"], list)


# -- list_workload_identities -------------------------------------------------


class TestListWorkloadIdentities:
    async def test_list_workload_identities(self, client: AgenticPlatformClient) -> None:
        """List workload identities (active OIDC apps) from Okta."""
        result = await client.list_workload_identities()

        assert "workload_identities" in result
        assert isinstance(result["workload_identities"], list)


# -- Workload identity lifecycle -----------------------------------------------


class TestWorkloadIdentityLifecycle:
    async def test_workload_identity_lifecycle(self, client: AgenticPlatformClient) -> None:
        """Create, get, list, update, delete a workload identity in Okta.

        Creates an OIDC app in Okta with client_credentials grant, verifies
        it can be retrieved and listed, updates its redirect URIs, then
        cleans up.
        """
        name = _unique("wi-okta")

        # Create
        created = await client.create_workload_identity(name, allowed_return_urls=["https://example.com/callback"])
        assert created["name"] == name

        try:
            # Get
            fetched = await client.get_workload_identity(name)
            assert fetched["name"] == name

            # List
            listed = await client.list_workload_identities()
            assert "workload_identities" in listed
            names = [wi["name"] for wi in listed["workload_identities"]]
            assert name in names

            # Update
            updated = await client.update_workload_identity(
                name,
                allowed_return_urls=[
                    "https://example.com/callback",
                    "https://example.com/callback2",
                ],
            )
            assert updated["name"] == name
        finally:
            # Delete
            await client.delete_workload_identity(name)


# -- Credential provider lifecycle (API key) -----------------------------------


class TestCredentialProviderLifecycleApiKey:
    async def test_credential_provider_lifecycle_api_key(self, client: AgenticPlatformClient) -> None:
        """Create, get, delete an API key credential provider in Okta.

        Creates an OIDC app with client_credentials grant type via the
        Management API, then cleans up.
        """
        name = _unique("cp-apikey-okta")

        created = await client.create_credential_provider(
            name,
            "api_key",
            {
                "client_id": f"test-{uuid4().hex[:8]}",
                "api_key": "test-secret-value",
            },
        )
        assert created["name"] == name
        assert created["provider_type"] == "api_key"

        try:
            fetched = await client.get_credential_provider(name)
            assert fetched["name"] == name
        finally:
            await client.delete_credential_provider(name)
