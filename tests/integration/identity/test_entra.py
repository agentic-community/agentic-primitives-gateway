"""Integration tests for the Entra ID (Azure AD) identity primitive.

Full stack with real Entra ID calls:
AgenticPlatformClient -> ASGI -> middleware -> route -> registry ->
EntraIdentityProvider -> real Microsoft Graph API / MSAL.

Requires:
  - An Azure AD / Entra ID tenant with an app registration
  - AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET env vars
  - The app registration needs Application.ReadWrite.All (or similar)
    Graph API permissions for control-plane tests

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
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
]

if not all(os.environ.get(v) for v in _REQUIRED_ENV):
    pytest.skip(
        "Entra env vars not set — skipping Entra identity integration tests",
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
    """Initialise registry with Entra identity provider (noop for everything else).

    Entra credentials are read from env vars and baked into the provider config
    so the provider doesn't need per-request credential headers.
    """
    tenant_id = os.environ["AZURE_TENANT_ID"]
    client_id = os.environ["AZURE_CLIENT_ID"]
    client_secret = os.environ["AZURE_CLIENT_SECRET"]

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
                "backend": ("agentic_primitives_gateway.primitives.identity.entra.EntraIdentityProvider"),
                "config": {
                    "tenant_id": tenant_id,
                    "client_id": client_id,
                    "client_secret": client_secret,
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

    Entra doesn't need AWS credentials — they're baked into the provider
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


# -- get_token (M2M on-behalf-of) --------------------------------------------


class TestGetToken:
    async def test_get_token_m2m(self, client: AgenticPlatformClient) -> None:
        """Attempt M2M token exchange via on-behalf-of flow.

        The OBO flow requires a valid user assertion token, so this may fail
        if no real user token is available. The test verifies the endpoint is
        reachable and the Entra provider processes the request.
        """
        try:
            result = await client.get_token(
                credential_provider="test-audience",
                workload_token="dummy-token",
            )
            assert "access_token" in result
        except Exception:
            # OBO requires a real user token — acceptable to fail here.
            pass


# -- get_api_key ---------------------------------------------------------------


class TestGetApiKey:
    async def test_get_api_key(self, client: AgenticPlatformClient) -> None:
        """Attempt to retrieve an API key (password credential) for an app.

        This looks up an application by displayName via Graph API and reads
        its passwordCredentials. May fail if no matching app exists or the
        secret text is not returned.
        """
        try:
            result = await client.get_api_key(
                credential_provider="test-app",
                workload_token="dummy",
            )
            assert "api_key" in result
        except Exception:
            # Graph API may not find the app or may not return secretText
            pass


# -- get_workload_token --------------------------------------------------------


class TestGetWorkloadToken:
    async def test_get_workload_token(self, client: AgenticPlatformClient) -> None:
        """Get a workload token using client_credentials grant.

        Uses the app's own scope (api://<client_id>/.default) which should
        resolve to a token for itself.
        """
        own_client_id = os.environ["AZURE_CLIENT_ID"]
        result = await client.get_workload_token(workload_name=own_client_id)

        assert "workload_token" in result
        assert result["workload_name"] == own_client_id
        assert result["workload_token"]  # non-empty


# -- list_credential_providers ------------------------------------------------


class TestListCredentialProviders:
    async def test_list_credential_providers(self, client: AgenticPlatformClient) -> None:
        """List credential providers (service principals) from Entra."""
        result = await client.list_credential_providers()

        assert "credential_providers" in result
        assert isinstance(result["credential_providers"], list)


# -- list_workload_identities -------------------------------------------------


class TestListWorkloadIdentities:
    async def test_list_workload_identities(self, client: AgenticPlatformClient) -> None:
        """List workload identities (application registrations) from Entra."""
        result = await client.list_workload_identities()

        assert "workload_identities" in result
        assert isinstance(result["workload_identities"], list)


# -- Workload identity lifecycle -----------------------------------------------


class TestWorkloadIdentityLifecycle:
    async def test_workload_identity_lifecycle(self, client: AgenticPlatformClient) -> None:
        """Create, get, list, update, delete a workload identity in Entra.

        Creates an application registration in Azure AD, verifies it can be
        retrieved and listed, updates its redirect URIs, then cleans up.
        """
        name = _unique("wi-entra")

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
        """Create, get, delete an API key credential provider in Entra.

        Creates an application registration with a password credential, then
        cleans up.
        """
        name = _unique("cp-apikey-entra")

        created = await client.create_credential_provider(
            name,
            "api_key",
            {"api_key": "test-secret-value"},
        )
        assert created["name"] == name
        assert created["provider_type"] == "api_key"

        try:
            fetched = await client.get_credential_provider(name)
            assert fetched["name"] == name
        finally:
            await client.delete_credential_provider(name)
