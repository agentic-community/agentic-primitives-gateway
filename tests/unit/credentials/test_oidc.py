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

    @pytest.mark.asyncio
    async def test_endpoint_only_profile_is_negatively_cached(self, resolver, principal):
        """No IdP round-trip (or denial audit) per request for an unusable profile."""
        resolver._fetch_userinfo = AsyncMock(return_value={"apg.langfuse.base_url": "http://redirect.invalid"})
        with patch("agentic_primitives_gateway.credentials.security.emit_credential_key_denied") as emit:
            assert await resolver.resolve(principal, "token") is None
            assert await resolver.resolve(principal, "token") is None
        assert resolver._fetch_userinfo.call_count == 1
        assert emit.call_count == 1


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

    @pytest.mark.parametrize(
        "attr",
        [
            "apg.langfuse.base_url",
            "apg.mcp_registry.url",
            "apg.keycloak.server_url",
            "apg.selenium.hub_url",
            "apg.okta.domain",
            "apg.agentcore.gateway_url",
        ],
    )
    def test_endpoint_keys_dropped(self, attr: str):
        """User-editable attributes must not redirect providers (SSRF)."""
        service = attr.split(".")[1]
        result = OidcCredentialResolver._map_credentials({attr: "http://redirect:8081", f"apg.{service}.token": "t"})
        assert result is not None
        assert result.service_credentials == {service: {"token": "t"}}

    def test_only_endpoint_keys_returns_none(self):
        assert OidcCredentialResolver._map_credentials({"apg.langfuse.base_url": "http://redirect"}) is None


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


class TestOidcNegativeCacheAuthority:
    @pytest.mark.asyncio
    async def test_admin_api_failure_is_not_cached_as_empty(self, admin_resolver, principal):
        """A transient Admin API failure + mapper-less userinfo must retry next request."""
        admin_resolver._fetch_via_admin_api = AsyncMock(side_effect=[None, {"apg.langfuse.public_key": "user-pk"}])
        admin_resolver._fetch_userinfo = AsyncMock(return_value={"sub": "user-123"})

        assert await admin_resolver.resolve(principal, "token") is None
        second = await admin_resolver.resolve(principal, "token")

        assert second is not None
        assert second.service_credentials == {"langfuse": {"public_key": "user-pk"}}
        assert admin_resolver._fetch_via_admin_api.call_count == 2

    @pytest.mark.asyncio
    async def test_partial_userinfo_after_admin_failure_is_used_but_not_cached(self, admin_resolver, principal):
        """Userinfo only carries mapped attributes; caching it would hide the rest for the whole TTL."""
        admin_resolver._fetch_via_admin_api = AsyncMock(
            side_effect=[None, {"apg.langfuse.public_key": "pk", "apg.agentcore.gateway_token": "user-token"}]
        )
        admin_resolver._fetch_userinfo = AsyncMock(return_value={"apg.langfuse.public_key": "pk"})

        first = await admin_resolver.resolve(principal, "token")
        second = await admin_resolver.resolve(principal, "token")

        assert first is not None and first.service_credentials == {"langfuse": {"public_key": "pk"}}
        assert second is not None and second.service_credentials["agentcore"] == {"gateway_token": "user-token"}
        assert admin_resolver._fetch_via_admin_api.call_count == 2
