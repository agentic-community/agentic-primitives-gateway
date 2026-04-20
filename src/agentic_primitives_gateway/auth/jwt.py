"""JWT authentication backend with JWKS-based signature verification."""

from __future__ import annotations

import logging
from typing import Any

import httpx
import jwt as pyjwt
from jwt import PyJWKClient
from starlette.requests import Request

from agentic_primitives_gateway.auth.base import AuthBackend
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal

logger = logging.getLogger(__name__)

# Default cache lifetime for JWKS keys (seconds)
_DEFAULT_JWKS_CACHE_SECONDS = 300


class JwtAuthBackend(AuthBackend):
    """Authenticate requests via JWT tokens validated against a JWKS endpoint.

    Supports any OIDC-compliant issuer (Cognito, Auth0, Okta, Keycloak, etc.).
    The OIDC login flow belongs in the client — this backend only validates
    the resulting access/ID token.

    Configuration::

        auth:
          backend: jwt
          jwt:
            issuer: "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_xxx"
            audience: "my-client-id"
            jwks_url: "https://..."           # optional, auto-derived from issuer
            algorithms: ["RS256"]             # optional, default RS256
            claims_mapping:
              groups: "cognito:groups"        # claim name for groups
              scopes: "scope"                # claim name for scopes
            jwks_cache_seconds: 300           # optional
    """

    def __init__(
        self,
        issuer: str,
        audience: str | None = None,
        client_id: str | None = None,
        jwks_url: str | None = None,
        algorithms: list[str] | None = None,
        claims_mapping: dict[str, str] | None = None,
        jwks_cache_seconds: int = _DEFAULT_JWKS_CACHE_SECONDS,
    ) -> None:
        if not issuer or not issuer.strip():
            raise ValueError("JwtAuthBackend: 'issuer' is required.")
        # Audience is mandatory in multi-tenant IdPs (Keycloak realms,
        # Cognito user pools with multiple clients, Auth0 tenants) —
        # without it, any token signed by the issuer is accepted
        # regardless of which client it was minted for.  Fail fast
        # instead of silently accepting tokens from other apps.
        if not audience or not str(audience).strip():
            raise ValueError(
                "JwtAuthBackend: 'audience' is required. Set auth.jwt.audience "
                "to your client_id (or an expected 'aud' claim). Without it, "
                "any token signed by the configured issuer would be accepted, "
                "even ones minted for other applications on the same IdP."
            )
        self._issuer = issuer.rstrip("/")
        self._client_id = client_id
        self._audience = audience
        self._algorithms = algorithms or ["RS256"]
        self._claims_mapping = claims_mapping or {}
        self._jwks_cache_seconds = jwks_cache_seconds

        # Resolve JWKS URL
        self._jwks_url = jwks_url or self._discover_jwks_url(self._issuer)
        self._jwk_client = PyJWKClient(
            self._jwks_url,
            cache_jwk_set=True,
            lifespan=jwks_cache_seconds,
        )

        logger.info(
            "JwtAuthBackend initialized (issuer=%s, audience=%s, jwks=%s)",
            self._issuer,
            self._audience,
            self._jwks_url,
        )

    @staticmethod
    def _discover_jwks_url(issuer: str) -> str:
        """Derive JWKS URL from the issuer's .well-known/openid-configuration.

        Falls back to ``{issuer}/.well-known/jwks.json`` if discovery fails.
        """
        discovery_url = f"{issuer}/.well-known/openid-configuration"
        try:
            resp = httpx.get(discovery_url, timeout=10)
            resp.raise_for_status()
            jwks_uri = resp.json().get("jwks_uri")
            if jwks_uri:
                logger.info("Discovered JWKS URI from %s: %s", discovery_url, jwks_uri)
                return str(jwks_uri)
        except Exception:
            logger.warning("OIDC discovery failed for %s, using fallback", discovery_url)

        return f"{issuer}/.well-known/jwks.json"

    async def authenticate(self, request: Request) -> AuthenticatedPrincipal | None:
        token = self._extract_token(request)
        if token is None:
            return None

        try:
            claims = self._decode_token(token)
        except pyjwt.ExpiredSignatureError:
            logger.warning("JWT expired")
            return None
        except pyjwt.InvalidTokenError as e:
            logger.warning("JWT validation failed: %s", e)
            return None

        principal = self._claims_to_principal(claims)
        if principal is None:
            # Empty or missing ``sub`` — reject so middleware returns 401.
            # An empty principal.id would flow into ownership checks
            # (``principal.id == resource_owner``) and Keycloak Admin
            # API URL construction, where it could match resources
            # erroneously persisted with owner_id="" or hit list
            # endpoints instead of per-user endpoints.
            logger.warning("JWT missing 'sub' claim")
            return None
        return principal

    @staticmethod
    def _extract_token(request: Request) -> str | None:
        """Extract Bearer token from the Authorization header."""
        auth_header = request.headers.get("authorization")
        if not auth_header:
            return None
        parts = auth_header.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None
        return parts[1].strip()

    def _decode_token(self, token: str) -> dict[str, Any]:
        """Decode and validate a JWT token.

        Audience is always validated — see ``__init__`` for why.
        """
        signing_key = self._jwk_client.get_signing_key_from_jwt(token)
        result: dict[str, Any] = pyjwt.decode(
            token,
            signing_key.key,
            algorithms=self._algorithms,
            issuer=self._issuer,
            audience=self._audience,
        )
        return result

    def _claims_to_principal(self, claims: dict[str, Any]) -> AuthenticatedPrincipal | None:
        """Map JWT claims to an AuthenticatedPrincipal.

        Returns ``None`` when the token has no usable ``sub`` — the
        caller treats that as authentication failure.
        """
        sub = str(claims.get("sub", "")).strip()
        if not sub:
            return None

        # Extract groups from configured claim
        groups_claim = self._claims_mapping.get("groups", "groups")
        raw_groups = claims.get(groups_claim, [])
        if isinstance(raw_groups, str):
            raw_groups = raw_groups.split()
        groups = frozenset(str(g) for g in raw_groups) if raw_groups else frozenset()

        # Extract scopes from configured claim
        scopes_claim = self._claims_mapping.get("scopes", "scope")
        raw_scopes = claims.get(scopes_claim, [])
        if isinstance(raw_scopes, str):
            raw_scopes = raw_scopes.split()
        scopes = frozenset(str(s) for s in raw_scopes) if raw_scopes else frozenset()

        return AuthenticatedPrincipal(
            id=sub,
            type="user",
            groups=groups,
            scopes=scopes,
        )

    async def close(self) -> None:
        """Cleanup — nothing to do for JWT backend."""
