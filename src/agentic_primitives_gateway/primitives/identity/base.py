from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class IdentityProvider(ABC):
    """Abstract base class for identity providers.

    Handles workload identity tokens, OAuth2 token exchange, API key
    retrieval, and credential provider / workload identity management
    for agent-to-service authentication.

    **Data plane (runtime, abstract):**
        - ``get_token`` — exchange a workload token for an external service
          OAuth2 token (M2M or 3-legged).
        - ``get_api_key`` — retrieve a stored API key for a credential provider.
        - ``get_workload_token`` — obtain an identity token for the agent itself.
        - ``list_credential_providers`` — list registered credential providers.

    **Data plane (optional):**
        - ``complete_auth`` — confirm user authorization in a 3-legged OAuth flow.

    **Control plane (optional, default ``NotImplementedError``):**
        - CRUD for credential providers (``create_credential_provider``, etc.).
        - CRUD for workload identities (``create_workload_identity``, etc.).
    """

    # ── Data plane — runtime token operations ─────────────────────

    @abstractmethod
    async def get_token(
        self,
        credential_provider: str,
        workload_token: str,
        *,
        auth_flow: str = "M2M",
        scopes: list[str] | None = None,
        callback_url: str | None = None,
        force_auth: bool = False,
        session_uri: str | None = None,
        custom_state: str | None = None,
        custom_parameters: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Exchange a workload token for an external service OAuth2 token.

        Returns ``{"access_token": ..., "token_type": ...}`` when a token is
        immediately available (M2M or cached).

        For 3-legged flows that require user authorization, returns
        ``{"authorization_url": ..., "session_uri": ...}`` instead.  The
        caller should redirect the user and then either call
        ``complete_auth`` or re-call ``get_token`` with the returned
        ``session_uri`` to check for the token.
        """
        ...

    @abstractmethod
    async def get_api_key(
        self,
        credential_provider: str,
        workload_token: str,
    ) -> dict[str, Any]:
        """Retrieve a stored API key for a credential provider."""
        ...

    @abstractmethod
    async def get_workload_token(
        self,
        workload_name: str,
        *,
        user_token: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Obtain an identity token for the agent itself.

        Optionally scoped to a specific user via ``user_token`` (JWT from
        the user's IdP) or ``user_id``.
        """
        ...

    @abstractmethod
    async def list_credential_providers(self) -> list[dict[str, Any]]:
        """List registered credential providers."""
        ...

    # ── Data plane — 3LO completion ──────────────────────────────

    async def complete_auth(
        self,
        session_uri: str,
        *,
        user_token: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """Confirm user authorization for a 3-legged OAuth flow."""
        raise NotImplementedError("complete_auth not supported by this provider")

    # ── Control plane — credential provider management ───────────

    async def create_credential_provider(
        self,
        name: str,
        provider_type: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """Register a new OAuth2 or API key credential provider."""
        raise NotImplementedError("create_credential_provider not supported by this provider")

    async def get_credential_provider(self, name: str) -> dict[str, Any]:
        """Get credential provider details."""
        raise NotImplementedError("get_credential_provider not supported by this provider")

    async def update_credential_provider(
        self,
        name: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """Update a credential provider's configuration."""
        raise NotImplementedError("update_credential_provider not supported by this provider")

    async def delete_credential_provider(self, name: str) -> None:
        """Delete a credential provider."""
        raise NotImplementedError("delete_credential_provider not supported by this provider")

    # ── Control plane — workload identity management ─────────────

    async def create_workload_identity(
        self,
        name: str,
        *,
        allowed_return_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        """Register a new workload (agent) identity."""
        raise NotImplementedError("create_workload_identity not supported by this provider")

    async def get_workload_identity(self, name: str) -> dict[str, Any]:
        """Get workload identity details."""
        raise NotImplementedError("get_workload_identity not supported by this provider")

    async def update_workload_identity(
        self,
        name: str,
        *,
        allowed_return_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        """Update a workload identity."""
        raise NotImplementedError("update_workload_identity not supported by this provider")

    async def delete_workload_identity(self, name: str) -> None:
        """Delete a workload identity."""
        raise NotImplementedError("delete_workload_identity not supported by this provider")

    async def list_workload_identities(self) -> list[dict[str, Any]]:
        """List all workload identities."""
        raise NotImplementedError("list_workload_identities not supported by this provider")

    # ── Health ───────────────────────────────────────────────────

    async def healthcheck(self) -> bool:
        return True
