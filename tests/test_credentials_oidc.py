"""Tests for OIDC credential resolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from agentic_primitives_gateway.auth.models import ANONYMOUS_PRINCIPAL, AuthenticatedPrincipal
from agentic_primitives_gateway.credentials.oidc import OidcCredentialResolver


@pytest.fixture
def principal():
    return AuthenticatedPrincipal(id="user-123", type="user")


@pytest.fixture
def resolver():
    """Resolver with userinfo only (no admin API)."""
    return OidcCredentialResolver(userinfo_url="https://idp.example.com/userinfo")


@pytest.fixture
def admin_resolver():
    """Resolver with admin API (preferred path)."""
    with patch("httpx.get", side_effect=Exception("skip discovery")):
        r = OidcCredentialResolver(
            issuer="https://kc.example.com/realms/test",
            admin_client_id="admin-client",
            admin_client_secret="secret",
        )
    return r


class TestOidcCredentialResolverInit:
    def test_requires_url_or_issuer(self):
        with pytest.raises(ValueError, match="requires either"):
            OidcCredentialResolver()

    def test_explicit_userinfo_url(self):
        r = OidcCredentialResolver(userinfo_url="https://test.com/userinfo")
        assert r._userinfo_url == "https://test.com/userinfo"

    def test_issuer_discovery_fallback(self):
        with patch("httpx.get", side_effect=Exception("fail")):
            r = OidcCredentialResolver(issuer="https://idp.example.com/realms/test")
            assert r._userinfo_url == "https://idp.example.com/realms/test/protocol/openid-connect/userinfo"

    def test_admin_url_derived_from_issuer(self):
        with patch("httpx.get", side_effect=Exception("skip")):
            r = OidcCredentialResolver(
                issuer="https://kc.example.com/realms/test",
                admin_client_id="admin",
                admin_client_secret="secret",
            )
        assert r._admin_url == "https://kc.example.com/admin/realms/test"

    def test_legacy_kwargs_ignored(self):
        r = OidcCredentialResolver(
            userinfo_url="https://test.com/userinfo",
            attributes={"old": "mapping"},
            service_mapping={"old": ["mapping"]},
        )
        assert r._userinfo_url == "https://test.com/userinfo"


class TestOidcResolverUserinfoMode:
    @pytest.mark.asyncio
    async def test_no_access_token_returns_none(self, resolver, principal):
        result = await resolver.resolve(principal, None)
        assert result is None

    @pytest.mark.asyncio
    async def test_anonymous_returns_none(self, resolver):
        result = await resolver.resolve(ANONYMOUS_PRINCIPAL, "token")
        assert result is None

    @pytest.mark.asyncio
    async def test_convention_based_resolution(self, resolver, principal):
        userinfo = {
            "sub": "user-123",
            "apg.langfuse.public_key": "pk-abc",
            "apg.langfuse.secret_key": "sk-xyz",
            "apg.mcp_registry.api_key": "mcp-key-123",
            "email": "user@test.com",
        }
        resolver._fetch_userinfo = AsyncMock(return_value=userinfo)

        result = await resolver.resolve(principal, "valid-token")
        assert result is not None
        assert result.service_credentials["langfuse"]["public_key"] == "pk-abc"
        assert result.service_credentials["langfuse"]["secret_key"] == "sk-xyz"
        assert result.service_credentials["mcp_registry"]["api_key"] == "mcp-key-123"

    @pytest.mark.asyncio
    async def test_no_apg_claims_returns_none(self, resolver, principal):
        resolver._fetch_userinfo = AsyncMock(return_value={"sub": "user-123", "email": "a@b.c"})
        result = await resolver.resolve(principal, "token")
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_hit(self, resolver, principal):
        resolver._fetch_userinfo = AsyncMock(return_value={"sub": "user-123", "apg.langfuse.public_key": "pk"})
        await resolver.resolve(principal, "token")
        assert resolver._fetch_userinfo.call_count == 1
        await resolver.resolve(principal, "token")
        assert resolver._fetch_userinfo.call_count == 1


class TestOidcResolverAdminApiMode:
    @pytest.mark.asyncio
    async def test_admin_api_preferred_over_userinfo(self, admin_resolver, principal):
        """When admin creds are available, Admin API is used instead of userinfo."""
        admin_resolver._fetch_via_admin_api = AsyncMock(return_value={"apg.langfuse.public_key": "pk-from-admin"})
        admin_resolver._fetch_userinfo = AsyncMock(return_value=None)

        result = await admin_resolver.resolve(principal, "token")
        assert result is not None
        assert result.service_credentials["langfuse"]["public_key"] == "pk-from-admin"
        admin_resolver._fetch_userinfo.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_userinfo_on_admin_failure(self, admin_resolver, principal):
        """If Admin API fails, falls back to userinfo."""
        admin_resolver._fetch_via_admin_api = AsyncMock(return_value=None)
        admin_resolver._fetch_userinfo = AsyncMock(return_value={"apg.langfuse.public_key": "pk-from-userinfo"})

        result = await admin_resolver.resolve(principal, "token")
        assert result is not None
        assert result.service_credentials["langfuse"]["public_key"] == "pk-from-userinfo"

    @pytest.mark.asyncio
    async def test_admin_api_flattens_list_attributes(self, admin_resolver, principal):
        """Keycloak stores attributes as lists — resolver flattens them."""
        admin_resolver._ensure_admin_token = AsyncMock(return_value="admin-token")

        request = httpx.Request("GET", "https://kc.example.com/admin/realms/test/users/user-123")
        user_resp = httpx.Response(
            200,
            json={
                "id": "user-123",
                "attributes": {
                    "apg.langfuse.public_key": ["pk-abc"],
                    "apg.langfuse.secret_key": ["sk-xyz"],
                    "other_attr": ["ignored"],
                },
            },
            request=request,
        )
        admin_resolver._client = AsyncMock()
        admin_resolver._client.get = AsyncMock(return_value=user_resp)

        result = await admin_resolver.resolve(principal, "token")
        assert result is not None
        assert result.service_credentials["langfuse"]["public_key"] == "pk-abc"
        assert result.service_credentials["langfuse"]["secret_key"] == "sk-xyz"


class TestOidcMapCredentials:
    def test_single_dot_goes_to_global(self):
        result = OidcCredentialResolver._map_credentials({"apg.some_key": "value"})
        assert result is not None
        assert result.service_credentials["_global"]["some_key"] == "value"

    def test_no_apg_returns_none(self):
        result = OidcCredentialResolver._map_credentials({"email": "a@b.c"})
        assert result is None

    def test_empty_remainder_skipped(self):
        result = OidcCredentialResolver._map_credentials({"apg.": "value"})
        assert result is None


class TestOidcFetchUserinfo:
    @pytest.mark.asyncio
    async def test_401_returns_none(self):
        r = OidcCredentialResolver(userinfo_url="https://test.com/userinfo")
        request = httpx.Request("GET", "https://test.com/userinfo")
        r._client = AsyncMock()
        r._client.get = AsyncMock(return_value=httpx.Response(401, request=request))
        assert await r._fetch_userinfo("bad-token") is None

    @pytest.mark.asyncio
    async def test_connection_error_returns_none(self):
        r = OidcCredentialResolver(userinfo_url="https://test.com/userinfo")
        r._client = AsyncMock()
        r._client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        assert await r._fetch_userinfo("token") is None


class TestOidcClose:
    @pytest.mark.asyncio
    async def test_close(self):
        r = OidcCredentialResolver(userinfo_url="https://test.com/userinfo")
        await r.close()
