"""Keycloak Account API credential writer."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.credentials.models import APG_PREFIX, CredentialUpdateRequest
from agentic_primitives_gateway.credentials.writer.base import CredentialWriter

logger = logging.getLogger(__name__)


def _filter_apg_attrs(raw_attrs: dict[str, Any]) -> dict[str, Any]:
    """Return only apg.* attributes, flattening Keycloak's list values."""
    return {k: (v[0] if isinstance(v, list) and v else v) for k, v in raw_attrs.items() if k.startswith(APG_PREFIX)}


class KeycloakCredentialWriter(CredentialWriter):
    """Write credentials to Keycloak via the Admin REST API.

    Uses a service account token to update user attributes in Keycloak.
    The Account REST API does not reliably support custom attribute updates
    across Keycloak versions, so this writer uses the Admin API instead.

    Two auth modes:
    - **admin_token** (default): Uses a service account with ``manage-users``
      role. The gateway fetches a token via client credentials grant at startup.
    - **user_token** (fallback): Uses the caller's own access token. Only works
      if the user has ``manage-account`` and the Account API is v2+. Not reliable
      for custom attributes.

    Configuration::

        credentials:
          writer:
            backend: keycloak
            config:
              base_url: "https://keycloak.example.com"
              realm: "my-realm"
              # Service account for Admin API (recommended):
              admin_client_id: "agentic-gateway-admin"
              admin_client_secret: "${KC_ADMIN_SECRET}"
              # OR derive from JWT issuer (uses Admin API with service account):
              # issuer: "https://keycloak.example.com/realms/my-realm"
    """

    def __init__(
        self,
        base_url: str | None = None,
        realm: str | None = None,
        issuer: str | None = None,
        admin_client_id: str | None = None,
        admin_client_secret: str | None = None,
    ) -> None:
        if base_url and realm:
            self._base_url = base_url.rstrip("/")
            self._realm = realm
        elif issuer:
            # Derive from issuer: https://keycloak.example.com/realms/my-realm
            issuer = issuer.rstrip("/")
            # Parse base_url and realm from issuer
            if "/realms/" in issuer:
                parts = issuer.rsplit("/realms/", 1)
                self._base_url = parts[0]
                self._realm = parts[1]
            else:
                raise ValueError(f"Cannot derive realm from issuer: {issuer}")
        else:
            raise ValueError("KeycloakCredentialWriter requires (base_url + realm) or issuer")

        self._admin_url = f"{self._base_url}/admin/realms/{self._realm}"
        self._account_url = f"{self._base_url}/realms/{self._realm}/account"
        self._token_url = f"{self._base_url}/realms/{self._realm}/protocol/openid-connect/token"
        self._admin_client_id = admin_client_id
        self._admin_client_secret = admin_client_secret
        self._admin_token: str | None = None
        self._declared_attrs: set[str] = set()  # Cache of already-declared attribute names
        self._client = httpx.AsyncClient(timeout=10)

        logger.info(
            "KeycloakCredentialWriter initialized (base=%s, realm=%s, admin_api=%s)",
            self._base_url,
            self._realm,
            bool(admin_client_id),
        )

    async def _get_admin_token(self) -> str | None:
        """Fetch a service account token via client credentials grant."""
        if not self._admin_client_id or not self._admin_client_secret:
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
            logger.warning("Failed to fetch admin token", exc_info=True)
            return None

    async def _ensure_admin_token(self) -> str | None:
        """Get or refresh the admin token."""
        if self._admin_token is None:
            self._admin_token = await self._get_admin_token()
        return self._admin_token

    async def write(
        self,
        principal: AuthenticatedPrincipal,
        access_token: str,
        updates: CredentialUpdateRequest,
    ) -> None:
        """Update user attributes in Keycloak."""
        admin_token = await self._ensure_admin_token()
        if admin_token:
            try:
                await self._write_via_admin_api(principal, admin_token, updates)
                return
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 403):
                    logger.warning(
                        "Admin API returned %d — refreshing token and falling back to Account API.",
                        e.response.status_code,
                    )
                    self._admin_token = None  # Force token refresh on next attempt
                else:
                    raise
        await self._write_via_account_api(access_token, updates)

    async def _ensure_attributes_declared(self, admin_token: str, attr_names: list[str]) -> None:
        """Ensure apg.* attributes are declared in Keycloak's User Profile.

        Modern Keycloak silently drops undeclared attributes on user PUT.
        This method reads the User Profile config, adds any missing apg.*
        attributes, and writes it back.
        """
        token = self._admin_token or admin_token
        profile_url = f"{self._admin_url}/users/profile"

        resp = await self._client.get(
            profile_url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        if resp.status_code != 200:
            logger.warning("Could not read User Profile config (status=%d)", resp.status_code)
            return

        profile: dict[str, Any] = resp.json()
        existing_names = {a["name"] for a in profile.get("attributes", [])}

        self._declared_attrs.update(existing_names)
        missing = [n for n in attr_names if n not in self._declared_attrs]
        if not missing:
            return

        for name in missing:
            profile.setdefault("attributes", []).append(
                {
                    "name": name,
                    "displayName": name,
                    "permissions": {"view": ["admin", "user"], "edit": ["admin", "user"]},
                    "validations": {},
                }
            )

        resp = await self._client.put(
            profile_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=profile,
        )
        if resp.status_code < 300:
            self._declared_attrs.update(missing)
            logger.info("Declared %d new attribute(s) in User Profile: %s", len(missing), missing)
        else:
            logger.warning(
                "Failed to declare attributes in User Profile (status=%d): %s",
                resp.status_code,
                resp.text[:200],
            )

    async def _write_via_admin_api(
        self,
        principal: AuthenticatedPrincipal,
        admin_token: str,
        updates: CredentialUpdateRequest,
    ) -> None:
        """Update user attributes via the Admin REST API (reliable for custom attrs)."""
        user_id = principal.id

        # Ensure all attribute names are declared in User Profile first
        safe_keys = [key if key.startswith(APG_PREFIX) else f"{APG_PREFIX}{key}" for key in updates.attributes]
        await self._ensure_attributes_declared(admin_token, safe_keys)

        # GET current user
        resp = await self._client.get(
            f"{self._admin_url}/users/{user_id}",
            headers={"Authorization": f"Bearer {admin_token}", "Accept": "application/json"},
        )
        if resp.status_code == 401:
            # Token expired, refresh and retry
            self._admin_token = await self._get_admin_token()
            if not self._admin_token:
                raise ConnectionError("Failed to refresh admin token")
            resp = await self._client.get(
                f"{self._admin_url}/users/{user_id}",
                headers={"Authorization": f"Bearer {self._admin_token}", "Accept": "application/json"},
            )
        resp.raise_for_status()
        user_data: dict[str, Any] = resp.json()

        # Merge attributes — only touch apg.* keys
        attrs = user_data.get("attributes", {})
        for key, value in updates.attributes.items():
            safe_key = key if key.startswith(APG_PREFIX) else f"{APG_PREFIX}{key}"
            attrs[safe_key] = [value]  # Keycloak stores attributes as lists

        user_data["attributes"] = attrs

        # PUT updated user
        token = self._admin_token or admin_token
        resp = await self._client.put(
            f"{self._admin_url}/users/{user_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=user_data,
        )
        resp.raise_for_status()
        logger.info("Updated %d attributes for user %s via Admin API", len(updates.attributes), user_id)

    async def _write_via_account_api(self, access_token: str, updates: CredentialUpdateRequest) -> None:
        """Fallback: update via the Account REST API using the user's own token.

        This works for standard profile fields but may not work for custom
        attributes depending on Keycloak version and User Profile configuration.
        """
        # Read current account
        resp = await self._client.get(
            self._account_url,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
        resp.raise_for_status()
        current: dict[str, Any] = resp.json()

        # Merge attributes — only touch apg.* keys
        attrs = current.get("attributes", {})
        for key, value in updates.attributes.items():
            safe_key = key if key.startswith(APG_PREFIX) else f"{APG_PREFIX}{key}"
            attrs[safe_key] = [value]

        current["attributes"] = attrs

        # POST updated account
        resp = await self._client.post(
            self._account_url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=current,
        )
        if resp.status_code >= 400:
            logger.warning(
                "Account API update failed (status=%d). Custom attributes may require "
                "Admin API access. Configure admin_client_id/admin_client_secret.",
                resp.status_code,
            )
        resp.raise_for_status()
        logger.info("Updated %d attributes via Account API", len(updates.attributes))

    async def read(
        self,
        principal: AuthenticatedPrincipal,
        access_token: str,
    ) -> dict[str, Any]:
        """Read the user's current attributes from Keycloak."""
        admin_token = await self._ensure_admin_token()
        if admin_token:
            try:
                return await self._read_via_admin_api(principal, admin_token)
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 403):
                    logger.warning(
                        "Admin API returned %d — refreshing token and falling back to Account API.",
                        e.response.status_code,
                    )
                    self._admin_token = None  # Force token refresh on next attempt
                else:
                    raise
        return await self._read_via_account_api(access_token)

    async def _read_via_admin_api(self, principal: AuthenticatedPrincipal, admin_token: str) -> dict[str, Any]:
        """Read user attributes via Admin API."""
        resp = await self._client.get(
            f"{self._admin_url}/users/{principal.id}",
            headers={"Authorization": f"Bearer {admin_token}", "Accept": "application/json"},
        )
        if resp.status_code == 401:
            self._admin_token = await self._get_admin_token()
            if not self._admin_token:
                return {}
            resp = await self._client.get(
                f"{self._admin_url}/users/{principal.id}",
                headers={"Authorization": f"Bearer {self._admin_token}", "Accept": "application/json"},
            )
        resp.raise_for_status()
        user_data: dict[str, Any] = resp.json()
        raw_attrs = user_data.get("attributes", {})
        return _filter_apg_attrs(raw_attrs)

    async def _read_via_account_api(self, access_token: str) -> dict[str, Any]:
        """Fallback: read via Account API."""
        resp = await self._client.get(
            self._account_url,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
        resp.raise_for_status()
        account: dict[str, Any] = resp.json()
        raw_attrs = account.get("attributes", {})
        return _filter_apg_attrs(raw_attrs)

    async def delete(
        self,
        principal: AuthenticatedPrincipal,
        access_token: str,
        key: str,
    ) -> None:
        """Delete a single apg.* attribute from the user."""
        safe_key = key if key.startswith(APG_PREFIX) else f"{APG_PREFIX}{key}"
        admin_token = await self._ensure_admin_token()
        if not admin_token:
            raise NotImplementedError("Delete requires Admin API access (no admin credentials configured)")

        token = self._admin_token or admin_token
        resp = await self._client.get(
            f"{self._admin_url}/users/{principal.id}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        resp.raise_for_status()
        user_data: dict[str, Any] = resp.json()

        attrs = user_data.get("attributes", {})
        if safe_key not in attrs:
            return  # Nothing to delete

        del attrs[safe_key]
        user_data["attributes"] = attrs

        resp = await self._client.put(
            f"{self._admin_url}/users/{principal.id}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=user_data,
        )
        resp.raise_for_status()
        logger.info("Deleted attribute %s for user %s", safe_key, principal.id)

    async def close(self) -> None:
        await self._client.aclose()
