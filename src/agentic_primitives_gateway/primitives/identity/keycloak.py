from __future__ import annotations

import logging
import uuid
from typing import Any

from keycloak import KeycloakAdmin, KeycloakOpenID

from agentic_primitives_gateway.context import get_service_credentials_or_defaults
from agentic_primitives_gateway.models.enums import (
    AuthFlow,
    CredentialProviderType,
    TokenType,
)
from agentic_primitives_gateway.primitives._sync import SyncRunnerMixin
from agentic_primitives_gateway.primitives.identity.base import IdentityProvider

logger = logging.getLogger(__name__)


class KeycloakIdentityProvider(SyncRunnerMixin, IdentityProvider):
    """Identity provider backed by Keycloak.

    Uses ``python-keycloak`` for both OpenID Connect token operations and
    Keycloak Admin REST API for credential provider and workload identity
    management.

    Prerequisites::

        pip install agentic-primitives-gateway[keycloak]

    Provider config example::

        backend: agentic_primitives_gateway.primitives.identity.keycloak.KeycloakIdentityProvider
        config:
          server_url: "http://localhost:8080"
          realm: "agents"
          client_id: "agentic-gateway"
          client_secret: "${KEYCLOAK_CLIENT_SECRET}"

    Per-request credential overrides via headers::

        X-Cred-Keycloak-Server-Url: https://keycloak.example.com
        X-Cred-Keycloak-Realm: my-realm
        X-Cred-Keycloak-Client-Id: my-client
        X-Cred-Keycloak-Client-Secret: my-secret
    """

    def __init__(
        self,
        server_url: str = "http://localhost:8080",
        realm: str = "master",
        client_id: str = "agentic-gateway",
        client_secret: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._server_url = server_url
        self._realm = realm
        self._client_id = client_id
        self._client_secret = client_secret
        logger.info(
            "Keycloak identity provider initialized (server=%s, realm=%s, client=%s)",
            server_url,
            realm,
            client_id,
        )

    def _resolve_config(self) -> dict[str, str | None]:
        """Resolve Keycloak config from request context with server-side defaults."""
        return get_service_credentials_or_defaults(
            "keycloak",
            {
                "server_url": self._server_url,
                "realm": self._realm,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )

    def _get_openid(self) -> KeycloakOpenID:
        """Create a KeycloakOpenID client from resolved config."""
        cfg = self._resolve_config()
        return KeycloakOpenID(
            server_url=cfg["server_url"] or self._server_url,
            realm_name=cfg["realm"] or self._realm,
            client_id=cfg["client_id"] or self._client_id,
            client_secret_key=cfg["client_secret"] or self._client_secret,
        )

    def _get_admin(self) -> KeycloakAdmin:
        """Create a KeycloakAdmin client from resolved config."""
        cfg = self._resolve_config()
        return KeycloakAdmin(
            server_url=cfg["server_url"] or self._server_url,
            realm_name=cfg["realm"] or self._realm,
            client_id=cfg["client_id"] or self._client_id,
            client_secret_key=cfg["client_secret"] or self._client_secret,
        )

    # ── Data plane — runtime token operations ─────────────────────

    async def get_token(
        self,
        credential_provider: str,
        workload_token: str,
        *,
        auth_flow: str = AuthFlow.M2M,
        scopes: list[str] | None = None,
        callback_url: str | None = None,
        force_auth: bool = False,
        session_uri: str | None = None,
        custom_state: str | None = None,
        custom_parameters: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        openid = self._get_openid()
        scope = " ".join(scopes) if scopes else None

        # Authorization code exchange (caller passing back the code via session_uri)
        if session_uri:
            response: dict[str, Any] = await self._run_sync(
                openid.token,
                grant_type="authorization_code",
                code=session_uri,
                redirect_uri=callback_url or "",
            )
            return {
                "access_token": response.get("access_token", ""),
                "token_type": TokenType.BEARER,
            }

        if auth_flow == AuthFlow.USER_FEDERATION:
            # Return the authorization URL for the caller to redirect the user
            state = custom_state or uuid.uuid4().hex
            auth_url: str = await self._run_sync(
                openid.auth_url,
                redirect_uri=callback_url or "",
                scope=scope or "openid",
                state=state,
            )
            return {"authorization_url": auth_url, "session_uri": state}

        # M2M: token exchange (RFC 8693)
        response = await self._run_sync(
            openid.exchange_token,
            token=workload_token,
            audience=credential_provider,
            scope=scope,
        )
        return {
            "access_token": response.get("access_token", ""),
            "token_type": TokenType.BEARER,
        }

    async def get_api_key(
        self,
        credential_provider: str,
        workload_token: str,
    ) -> dict[str, Any]:
        admin = self._get_admin()

        # Look up the Keycloak client by clientId, then fetch its secret
        client_uuid: str = await self._run_sync(admin.get_client_id, credential_provider)
        secret_data: dict[str, Any] = await self._run_sync(admin.get_client_secrets, client_uuid)
        return {
            "api_key": secret_data.get("value", ""),
            "credential_provider": credential_provider,
        }

    async def get_workload_token(
        self,
        workload_name: str,
        *,
        user_token: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        openid = self._get_openid()

        if user_token:
            # Token exchange: workload token scoped to user
            response: dict[str, Any] = await self._run_sync(
                openid.exchange_token,
                token=user_token,
                audience=workload_name,
            )
        else:
            # Plain client_credentials grant
            response = await self._run_sync(
                openid.token,
                grant_type="client_credentials",
                scope="openid",
            )

        return {
            "workload_token": response.get("access_token", ""),
            "workload_name": workload_name,
        }

    async def list_credential_providers(self) -> list[dict[str, Any]]:
        admin = self._get_admin()
        results: list[dict[str, Any]] = []

        # OAuth2 identity providers
        try:
            idps: list[dict[str, Any]] = await self._run_sync(admin.get_idps)
            for idp in idps:
                results.append(
                    {
                        "name": idp.get("alias", ""),
                        "provider_type": CredentialProviderType.OAUTH2,
                        "metadata": {"provider_id": idp.get("providerId", "")},
                    }
                )
        except Exception:
            logger.debug("Failed to list Keycloak identity providers", exc_info=True)

        # Confidential clients as API key providers
        try:
            clients: list[dict[str, Any]] = await self._run_sync(admin.get_clients)
            for client in clients:
                if not client.get("publicClient", True) and client.get("serviceAccountsEnabled"):
                    results.append(
                        {
                            "name": client.get("clientId", ""),
                            "provider_type": CredentialProviderType.API_KEY,
                        }
                    )
        except Exception:
            logger.debug("Failed to list Keycloak clients", exc_info=True)

        return results

    # ── Data plane — 3LO completion ──────────────────────────────

    async def complete_auth(
        self,
        session_uri: str,
        *,
        user_token: str | None = None,
        user_id: str | None = None,
    ) -> None:
        # In the Keycloak flow, completion happens via authorization code exchange
        # in get_token(session_uri=code). This method is a no-op confirmation.
        pass

    # ── Control plane — credential provider management ───────────

    async def create_credential_provider(
        self,
        name: str,
        provider_type: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        admin = self._get_admin()

        if provider_type == CredentialProviderType.OAUTH2:
            idp_config = {
                "alias": name,
                "providerId": config.get("provider_id", "oidc"),
                "enabled": True,
                "config": {k: v for k, v in config.items() if k != "provider_id"},
            }
            await self._run_sync(admin.create_idp, idp_config)
            return {"name": name, "provider_type": CredentialProviderType.OAUTH2}

        if provider_type == CredentialProviderType.API_KEY:
            client_config: dict[str, Any] = {
                "clientId": name,
                "secret": config.get("api_key", ""),
                "publicClient": False,
                "serviceAccountsEnabled": True,
                "enabled": True,
            }
            client_uuid: str = await self._run_sync(admin.create_client, client_config)
            return {
                "name": name,
                "provider_type": CredentialProviderType.API_KEY,
                "arn": client_uuid,
            }

        raise ValueError(f"Unknown provider_type: {provider_type}")

    async def get_credential_provider(self, name: str) -> dict[str, Any]:
        admin = self._get_admin()

        # Try identity provider first
        try:
            idp: dict[str, Any] = await self._run_sync(admin.get_idp, name)
            return {
                "name": idp.get("alias", name),
                "provider_type": CredentialProviderType.OAUTH2,
                "metadata": {"provider_id": idp.get("providerId", "")},
            }
        except Exception:
            pass

        # Fall back to client
        client_uuid: str = await self._run_sync(admin.get_client_id, name)
        client: dict[str, Any] = await self._run_sync(admin.get_client, client_uuid)
        return {
            "name": client.get("clientId", name),
            "provider_type": CredentialProviderType.API_KEY,
            "arn": client_uuid,
        }

    async def update_credential_provider(
        self,
        name: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        admin = self._get_admin()

        # Try identity provider first
        try:
            await self._run_sync(admin.update_idp, name, config)
            return {"name": name, "provider_type": CredentialProviderType.OAUTH2}
        except Exception:
            pass

        # Fall back to client
        client_uuid: str = await self._run_sync(admin.get_client_id, name)
        await self._run_sync(admin.update_client, client_uuid, config)
        return {"name": name, "provider_type": CredentialProviderType.API_KEY}

    async def delete_credential_provider(self, name: str) -> None:
        admin = self._get_admin()

        # Try identity provider first
        try:
            await self._run_sync(admin.delete_idp, name)
            return
        except Exception:
            pass

        client_uuid: str = await self._run_sync(admin.get_client_id, name)
        await self._run_sync(admin.delete_client, client_uuid)

    # ── Control plane — workload identity management ─────────────

    async def create_workload_identity(
        self,
        name: str,
        *,
        allowed_return_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        admin = self._get_admin()

        client_config: dict[str, Any] = {
            "clientId": name,
            "serviceAccountsEnabled": True,
            "publicClient": False,
            "enabled": True,
            "redirectUris": allowed_return_urls or [],
        }
        client_uuid: str = await self._run_sync(admin.create_client, client_config)
        return {
            "name": name,
            "arn": client_uuid,
            "allowed_return_urls": allowed_return_urls or [],
        }

    async def get_workload_identity(self, name: str) -> dict[str, Any]:
        admin = self._get_admin()
        client_uuid: str = await self._run_sync(admin.get_client_id, name)
        client: dict[str, Any] = await self._run_sync(admin.get_client, client_uuid)
        return {
            "name": client.get("clientId", name),
            "arn": client_uuid,
            "allowed_return_urls": client.get("redirectUris", []),
        }

    async def update_workload_identity(
        self,
        name: str,
        *,
        allowed_return_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        admin = self._get_admin()
        client_uuid: str = await self._run_sync(admin.get_client_id, name)
        payload: dict[str, Any] = {"redirectUris": allowed_return_urls or []}
        await self._run_sync(admin.update_client, client_uuid, payload)
        return {
            "name": name,
            "arn": client_uuid,
            "allowed_return_urls": allowed_return_urls or [],
        }

    async def delete_workload_identity(self, name: str) -> None:
        admin = self._get_admin()
        client_uuid: str = await self._run_sync(admin.get_client_id, name)
        await self._run_sync(admin.delete_client, client_uuid)

    async def list_workload_identities(self) -> list[dict[str, Any]]:
        admin = self._get_admin()
        clients: list[dict[str, Any]] = await self._run_sync(admin.get_clients)
        return [
            {
                "name": c.get("clientId", ""),
                "arn": c.get("id", ""),
                "allowed_return_urls": c.get("redirectUris", []),
            }
            for c in clients
            if c.get("serviceAccountsEnabled")
        ]

    # ── Health ───────────────────────────────────────────────────

    async def healthcheck(self) -> bool:
        try:
            openid = self._get_openid()
            await self._run_sync(openid.well_known)
            return True
        except Exception:
            return False
