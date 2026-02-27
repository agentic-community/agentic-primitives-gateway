"""Integration tests for the AgentCore identity primitive.

Full stack with real AWS calls:
AgenticPlatformClient → ASGI → middleware → route → registry →
AgentCoreIdentityProvider → real IdentityClient SDK.

Self-provisions workload identities and credential providers,
tearing them down after each test.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from agentic_primitives_gateway_client import AgenticPlatformClient

pytestmark = pytest.mark.integration


# ── Helpers ───────────────────────────────────────────────────────────


def _unique(prefix: str = "integ") -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


# ── Workload identity lifecycle ──────────────────────────────────────


class TestWorkloadIdentityLifecycle:
    async def test_workload_identity_lifecycle(self, client: AgenticPlatformClient) -> None:
        """Create, get, list, update, delete a workload identity."""
        name = _unique("wi")

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


# ── Credential provider lifecycle ────────────────────────────────────


class TestCredentialProviderLifecycleOAuth2:
    async def test_credential_provider_lifecycle_oauth2(self, client: AgenticPlatformClient) -> None:
        """Create oauth2 credential provider, get, update, delete.

        Note: This creates a real credential provider. The OAuth config
        won't actually work for token exchange, but the CRUD lifecycle
        is the focus.
        """
        name = _unique("cp-oauth2")

        # Create — config must match boto3 create_oauth2_credential_provider kwargs
        created = await client.create_credential_provider(
            name,
            "oauth2",
            {
                "credentialProviderVendor": "CustomOauth2",
                "oauth2ProviderConfigInput": {
                    "customOauth2ProviderConfig": {
                        "oauthDiscovery": {
                            "authorizationServerMetadata": {
                                "issuer": "https://auth.example.com",
                                "authorizationEndpoint": "https://auth.example.com/authorize",
                                "tokenEndpoint": "https://auth.example.com/token",
                            },
                        },
                        "clientId": "test-client-id",
                        "clientSecret": "test-client-secret",
                    },
                },
            },
        )
        assert created["name"] == name
        assert created["provider_type"] == "oauth2"

        try:
            # Get
            fetched = await client.get_credential_provider(name)
            assert fetched["name"] == name
        finally:
            # Delete
            await client.delete_credential_provider(name)


class TestListCredentialProviders:
    async def test_list_credential_providers(self, client: AgenticPlatformClient) -> None:
        result = await client.list_credential_providers()

        assert "credential_providers" in result
        # Result is a list (may be empty in a fresh account)
        assert isinstance(result["credential_providers"], list)


# ── Workload token ───────────────────────────────────────────────────


class TestGetWorkloadToken:
    async def test_get_workload_token(self, client: AgenticPlatformClient) -> None:
        """Create a workload identity and get a token for it."""
        name = _unique("wi-token")

        # Create a workload identity first
        await client.create_workload_identity(name)

        try:
            result = await client.get_workload_token(workload_name=name)

            assert "workload_token" in result
            assert result["workload_name"] == name
            assert result["workload_token"]  # Should be non-empty
        finally:
            await client.delete_workload_identity(name)
