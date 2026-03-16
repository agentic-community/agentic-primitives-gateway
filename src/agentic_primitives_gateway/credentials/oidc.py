"""OIDC-based credential resolver — reads per-user apg.* attributes."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.credentials.base import CredentialResolver
from agentic_primitives_gateway.credentials.cache import CredentialCache
from agentic_primitives_gateway.credentials.models import APG_PREFIX, ResolvedCredentials

logger = logging.getLogger(__name__)


class OidcCredentialResolver(CredentialResolver):
    """Resolve per-user credentials from OIDC user attributes.

    Convention-based: all ``apg.*`` attributes are automatically mapped
    to service credentials using dot-separated naming::

        apg.langfuse.public_key  → service_credentials["langfuse"]["public_key"]
        apg.langfuse.secret_key  → service_credentials["langfuse"]["secret_key"]
        apg.mcp_registry.api_key → service_credentials["mcp_registry"]["api_key"]

    Two resolution modes (tried in order):

    1. **Admin API** (preferred): If ``admin_client_id`` / ``admin_client_secret``
       are provided, reads the user's attributes directly from the Keycloak Admin
       REST API. This always returns all stored attributes — **no protocol mappers
       needed** in Keycloak.

    2. **Userinfo** (fallback): Fetches from the OIDC userinfo endpoint using the
       caller's access token. Only returns attributes that have protocol mappers
       configured in Keycloak.

    Configuration::

        credentials:
          resolver: oidc
          writer:
            backend: keycloak
            config:
              admin_client_id: "agentic-gateway-admin"
              admin_client_secret: "${KC_ADMIN_SECRET}"
          cache:
            ttl_seconds: 300
    """

    def __init__(
        self,
        userinfo_url: str | None = None,
        issuer: str | None = None,
        admin_client_id: str | None = None,
        admin_client_secret: str | None = None,
        cache: CredentialCache | None = None,
        # Legacy kwargs accepted but ignored
        **_kwargs: Any,
    ) -> None:
        self._cache = cache or CredentialCache()
        self._client = httpx.AsyncClient(timeout=10)
        self._admin_client_id = admin_client_id
        self._admin_client_secret = admin_client_secret
        self._admin_token: str | None = None

        # Derive admin URL and token URL from issuer
        self._admin_url: str | None = None
        self._token_url: str | None = None
        if issuer and admin_client_id:
            issuer_clean = issuer.rstrip("/")
            if "/realms/" in issuer_clean:
                base, realm = issuer_clean.rsplit("/realms/", 1)
                self._admin_url = f"{base}/admin/realms/{realm}"
                self._token_url = f"{issuer_clean}/protocol/openid-connect/token"

        # Resolve userinfo URL (fallback when admin API not available)
        if userinfo_url:
            self._userinfo_url: str | None = userinfo_url
        elif issuer:
            self._userinfo_url = self._discover_userinfo_url(issuer)
        else:
            self._userinfo_url = None

        if not self._admin_url and not self._userinfo_url:
            raise ValueError("OidcCredentialResolver requires either (issuer + admin creds) or userinfo_url")

        logger.info(
            "OidcCredentialResolver initialized (admin_api=%s, userinfo=%s)",
            bool(self._admin_url),
            self._userinfo_url,
        )

    @staticmethod
    def _discover_userinfo_url(issuer: str) -> str:
        """Derive userinfo URL from OIDC discovery document."""
        issuer = issuer.rstrip("/")
        discovery_url = f"{issuer}/.well-known/openid-configuration"
        try:
            resp = httpx.get(discovery_url, timeout=10)
            resp.raise_for_status()
            userinfo_endpoint = resp.json().get("userinfo_endpoint")
            if userinfo_endpoint:
                logger.info("Discovered userinfo endpoint: %s", userinfo_endpoint)
                return str(userinfo_endpoint)
        except Exception:
            logger.warning("OIDC discovery failed for %s, using fallback", discovery_url)

        return f"{issuer}/protocol/openid-connect/userinfo"

    async def _get_admin_token(self) -> str | None:
        """Fetch a service account token via client credentials grant."""
        if not self._admin_client_id or not self._admin_client_secret or not self._token_url:
            return None
        try:
            resp = await self._client.post(
                self._token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._admin_client_id,
                    "client_secret": self._admin_client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            token: str = resp.json()["access_token"]
            return token
        except Exception:
            logger.warning("Failed to fetch admin token for resolver", exc_info=True)
            return None

    async def _ensure_admin_token(self) -> str | None:
        if self._admin_token is None:
            self._admin_token = await self._get_admin_token()
        return self._admin_token

    async def resolve(
        self,
        principal: AuthenticatedPrincipal,
        access_token: str | None,
    ) -> ResolvedCredentials | None:
        if not access_token or principal.is_anonymous:
            return None

        # Check cache first
        cached = self._cache.get(principal.id)
        if cached is not None:
            return cached

        # Try Admin API first (returns all attributes, no protocol mappers needed)
        attrs = await self._fetch_via_admin_api(principal.id)

        # Fall back to userinfo (only returns claims with protocol mappers)
        if attrs is None and self._userinfo_url:
            attrs = await self._fetch_userinfo(access_token)

        if attrs is None:
            return None

        creds = self._map_credentials(attrs)
        if creds is not None:
            self._cache.put(principal.id, creds)
        return creds

    async def _fetch_via_admin_api(self, user_id: str) -> dict[str, Any] | None:
        """Read user attributes directly from the Keycloak Admin API."""
        if not self._admin_url:
            return None

        admin_token = await self._ensure_admin_token()
        if not admin_token:
            return None

        try:
            resp = await self._client.get(
                f"{self._admin_url}/users/{user_id}",
                headers={"Authorization": f"Bearer {admin_token}", "Accept": "application/json"},
            )
            if resp.status_code == 401:
                # Token expired, refresh once
                self._admin_token = await self._get_admin_token()
                if not self._admin_token:
                    return None
                resp = await self._client.get(
                    f"{self._admin_url}/users/{user_id}",
                    headers={"Authorization": f"Bearer {self._admin_token}", "Accept": "application/json"},
                )
            if resp.status_code in (401, 403):
                logger.warning("Admin API returned %d in resolver — falling back to userinfo", resp.status_code)
                self._admin_token = None
                return None
            resp.raise_for_status()
            user_data: dict[str, Any] = resp.json()
            # Flatten Keycloak's list-valued attributes to single values
            raw_attrs = user_data.get("attributes", {})
            return {k: (v[0] if isinstance(v, list) and v else v) for k, v in raw_attrs.items()}
        except Exception:
            logger.warning("Admin API resolver error", exc_info=True)
            return None

    async def _fetch_userinfo(self, access_token: str) -> dict[str, Any] | None:
        """Call the userinfo endpoint with the user's access token."""
        if not self._userinfo_url:
            return None
        try:
            resp = await self._client.get(
                self._userinfo_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if resp.status_code == 401:
                logger.warning("Userinfo returned 401 — token may be expired or invalid")
                return None
            resp.raise_for_status()
            result: dict[str, Any] = resp.json()
            return result
        except httpx.HTTPStatusError as e:
            logger.warning("Userinfo request failed: %s %s", e.response.status_code, e.response.text[:200])
            return None
        except Exception:
            logger.warning("Userinfo request error", exc_info=True)
            return None

    @staticmethod
    def _map_credentials(attrs: dict[str, Any]) -> ResolvedCredentials | None:
        """Map apg.* attributes to service credentials by convention.

        ``apg.{service}.{key}`` → ``service_credentials[service][key]``

        Attributes without two dots after the prefix (e.g. ``apg.something``)
        are stored as ``service_credentials["_global"]["something"]``.
        """
        service_credentials: dict[str, dict[str, str]] = {}

        for attr_name, value in attrs.items():
            if not attr_name.startswith(APG_PREFIX):
                continue
            remainder = attr_name[len(APG_PREFIX) :]
            if not remainder:
                continue

            parts = remainder.split(".", 1)
            if len(parts) == 2:
                service, key = parts
            else:
                service, key = "_global", parts[0]

            service_credentials.setdefault(service, {})[key] = str(value)

        if not service_credentials:
            return None

        return ResolvedCredentials(service_credentials=service_credentials)

    async def close(self) -> None:
        await self._client.aclose()
