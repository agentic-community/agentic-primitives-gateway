"""Tests for JWT authentication backend."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, patch

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from agentic_primitives_gateway.auth.jwt import JwtAuthBackend

# ── Test fixtures: RSA key pair + JWKS ──────────────────────────────


def _generate_rsa_key_pair():
    """Generate an RSA key pair for test token signing."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    return private_key, public_key


_PRIVATE_KEY, _PUBLIC_KEY = _generate_rsa_key_pair()

_PRIVATE_PEM = _PRIVATE_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)


def _make_token(
    sub: str = "user-123",
    issuer: str = "https://issuer.example.com",
    audience: str = "my-app",
    groups: list[str] | None = None,
    scopes: str | None = None,
    expires_in: int = 3600,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Create a signed JWT for testing."""
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": sub,
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + expires_in,
    }
    if groups is not None:
        payload["groups"] = groups
    if scopes is not None:
        payload["scope"] = scopes
    if extra_claims:
        payload.update(extra_claims)

    return pyjwt.encode(payload, _PRIVATE_PEM, algorithm="RS256")


def _make_request(token: str | None = None, headers: dict[str, str] | None = None) -> MagicMock:
    """Create a mock request with optional Bearer token."""
    h: dict[str, str] = {}
    if headers:
        h.update(headers)
    if token:
        h["authorization"] = f"Bearer {token}"
    request = MagicMock()
    request.headers = h
    return request


def _make_backend(
    issuer: str = "https://issuer.example.com",
    audience: str | None = "my-app",
    claims_mapping: dict[str, str] | None = None,
) -> JwtAuthBackend:
    """Create a JwtAuthBackend with mocked JWKS client."""
    backend = JwtAuthBackend.__new__(JwtAuthBackend)
    backend._issuer = issuer
    backend._audience = audience
    backend._algorithms = ["RS256"]
    backend._claims_mapping = claims_mapping or {}
    backend._jwks_url = "https://issuer.example.com/.well-known/jwks.json"

    # Mock the JWKS client to return our test public key
    mock_jwk_client = MagicMock()
    mock_signing_key = MagicMock()
    mock_signing_key.key = _PUBLIC_KEY
    mock_jwk_client.get_signing_key_from_jwt.return_value = mock_signing_key
    backend._jwk_client = mock_jwk_client

    return backend


# ── Tests ───────────────────────────────────────────────────────────


class TestJwtAuthentication:
    @pytest.mark.asyncio
    async def test_valid_token(self):
        backend = _make_backend()
        token = _make_token(sub="alice", groups=["engineering"], scopes="read write")
        request = _make_request(token)

        principal = await backend.authenticate(request)

        assert principal is not None
        assert principal.id == "alice"
        assert principal.type == "user"
        assert principal.groups == frozenset({"engineering"})
        assert principal.scopes == frozenset({"read", "write"})

    @pytest.mark.asyncio
    async def test_no_token_returns_none(self):
        backend = _make_backend()
        request = _make_request()

        principal = await backend.authenticate(request)
        assert principal is None

    @pytest.mark.asyncio
    async def test_expired_token_returns_none(self):
        backend = _make_backend()
        token = _make_token(expires_in=-100)
        request = _make_request(token)

        principal = await backend.authenticate(request)
        assert principal is None

    @pytest.mark.asyncio
    async def test_wrong_issuer_returns_none(self):
        backend = _make_backend(issuer="https://other-issuer.com")
        token = _make_token(issuer="https://issuer.example.com")
        request = _make_request(token)

        principal = await backend.authenticate(request)
        assert principal is None

    @pytest.mark.asyncio
    async def test_wrong_audience_returns_none(self):
        backend = _make_backend(audience="wrong-app")
        token = _make_token(audience="my-app")
        request = _make_request(token)

        principal = await backend.authenticate(request)
        assert principal is None

    @pytest.mark.asyncio
    async def test_no_audience_validation(self):
        """When audience is None, audience claim is not checked."""
        backend = _make_backend(audience=None)
        token = _make_token(audience="any-app")
        request = _make_request(token)

        principal = await backend.authenticate(request)
        assert principal is not None
        assert principal.id == "user-123"

    @pytest.mark.asyncio
    async def test_non_bearer_auth_ignored(self):
        backend = _make_backend()
        request = _make_request(headers={"authorization": "Basic dXNlcjpwYXNz"})

        principal = await backend.authenticate(request)
        assert principal is None


class TestClaimsMapping:
    @pytest.mark.asyncio
    async def test_custom_groups_claim(self):
        backend = _make_backend(claims_mapping={"groups": "cognito:groups"})
        token = _make_token(extra_claims={"cognito:groups": ["admin", "dev"]})
        request = _make_request(token)

        principal = await backend.authenticate(request)
        assert principal is not None
        assert principal.groups == frozenset({"admin", "dev"})

    @pytest.mark.asyncio
    async def test_custom_scopes_claim(self):
        backend = _make_backend(claims_mapping={"scopes": "permissions"})
        token = _make_token(extra_claims={"permissions": ["agents:read", "agents:write"]})
        request = _make_request(token)

        principal = await backend.authenticate(request)
        assert principal is not None
        assert principal.scopes == frozenset({"agents:read", "agents:write"})

    @pytest.mark.asyncio
    async def test_groups_as_space_separated_string(self):
        """Some IdPs return groups as a space-separated string."""
        backend = _make_backend()
        token = _make_token(extra_claims={"groups": "admin engineering"})
        request = _make_request(token)

        principal = await backend.authenticate(request)
        assert principal is not None
        assert principal.groups == frozenset({"admin", "engineering"})

    @pytest.mark.asyncio
    async def test_scope_as_space_separated_string(self):
        """Standard OAuth scope claim is space-separated."""
        backend = _make_backend()
        token = _make_token(scopes="openid profile admin")
        request = _make_request(token)

        principal = await backend.authenticate(request)
        assert principal is not None
        assert principal.scopes == frozenset({"openid", "profile", "admin"})

    @pytest.mark.asyncio
    async def test_missing_groups_defaults_empty(self):
        backend = _make_backend()
        token = _make_token()  # no groups claim
        request = _make_request(token)

        principal = await backend.authenticate(request)
        assert principal is not None
        assert principal.groups == frozenset()

    @pytest.mark.asyncio
    async def test_missing_scopes_defaults_empty(self):
        backend = _make_backend()
        token = _make_token()  # no scope claim
        request = _make_request(token)

        principal = await backend.authenticate(request)
        assert principal is not None
        assert principal.scopes == frozenset()


class TestJwksDiscovery:
    def test_discover_from_openid_configuration(self):
        """JWKS URL is discovered from .well-known/openid-configuration."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"jwks_uri": "https://issuer.example.com/keys"}
        mock_response.raise_for_status = MagicMock()

        with patch("agentic_primitives_gateway.auth.jwt.httpx.get", return_value=mock_response):
            url = JwtAuthBackend._discover_jwks_url("https://issuer.example.com")

        assert url == "https://issuer.example.com/keys"

    def test_fallback_on_discovery_failure(self):
        """Falls back to /.well-known/jwks.json when discovery fails."""
        with patch("agentic_primitives_gateway.auth.jwt.httpx.get", side_effect=Exception("network error")):
            url = JwtAuthBackend._discover_jwks_url("https://issuer.example.com")

        assert url == "https://issuer.example.com/.well-known/jwks.json"


class TestJwtClose:
    @pytest.mark.asyncio
    async def test_close_is_noop(self):
        backend = _make_backend()
        await backend.close()  # should not raise
