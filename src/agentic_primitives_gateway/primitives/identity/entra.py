from __future__ import annotations

import logging
import uuid
from typing import Any

import msal
import requests

from agentic_primitives_gateway.context import get_service_credentials_or_defaults
from agentic_primitives_gateway.models.enums import (
    AuthFlow,
    CredentialProviderType,
    TokenType,
)
from agentic_primitives_gateway.primitives._sync import SyncRunnerMixin
from agentic_primitives_gateway.primitives.identity.base import IdentityProvider

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class EntraIdentityProvider(SyncRunnerMixin, IdentityProvider):
    """Identity provider backed by Microsoft Entra ID (Azure AD).

    Uses ``msal`` for OAuth2 token operations and the Microsoft Graph
    REST API for credential provider and workload identity management.

    Prerequisites::

        pip install agentic-primitives-gateway[entra]

    Provider config example::

        backend: agentic_primitives_gateway.primitives.identity.entra.EntraIdentityProvider
        config:
          tenant_id: "${AZURE_TENANT_ID}"
          client_id: "${AZURE_CLIENT_ID}"
          client_secret: "${AZURE_CLIENT_SECRET}"

    Per-request credential overrides via headers::

        X-Cred-Entra-Tenant-Id: <tenant>
        X-Cred-Entra-Client-Id: <client>
        X-Cred-Entra-Client-Secret: <secret>
    """

    def __init__(
        self,
        tenant_id: str = "",
        client_id: str = "",
        client_secret: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        logger.info(
            "Entra identity provider initialized (tenant=%s, client=%s)",
            tenant_id,
            client_id,
        )

    def _resolve_config(self) -> dict[str, str | None]:
        """Resolve Entra config from request context with server-side defaults."""
        return get_service_credentials_or_defaults(
            "entra",
            {
                "tenant_id": self._tenant_id,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )

    def _get_app(self) -> msal.ConfidentialClientApplication:
        """Create an MSAL ConfidentialClientApplication from resolved config."""
        cfg = self._resolve_config()
        tenant_id = cfg.get("tenant_id") or self._tenant_id
        authority = f"https://login.microsoftonline.com/{tenant_id}"
        return msal.ConfidentialClientApplication(
            client_id=cfg.get("client_id") or self._client_id,
            client_credential=cfg.get("client_secret") or self._client_secret,
            authority=authority,
        )

    def _graph_headers(self) -> dict[str, str]:
        """Get an access token for Microsoft Graph and return auth headers."""
        app = self._get_app()
        result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        if "access_token" not in result:
            raise RuntimeError(f"Failed to get Graph API token: {result.get('error_description', result)}")
        return {"Authorization": f"Bearer {result['access_token']}"}

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
        app = self._get_app()
        resolved_scopes = scopes or [f"api://{credential_provider}/.default"]

        # Authorization code exchange
        if session_uri:
            result: dict[str, Any] = await self._run_sync(
                app.acquire_token_by_authorization_code,
                code=session_uri,
                scopes=resolved_scopes,
                redirect_uri=callback_url or "",
            )
            if "access_token" not in result:
                raise RuntimeError(f"Token exchange failed: {result.get('error_description', result)}")
            return {"access_token": result["access_token"], "token_type": TokenType.BEARER}

        if auth_flow == AuthFlow.USER_FEDERATION:
            state = custom_state or uuid.uuid4().hex
            auth_url: str = await self._run_sync(
                app.get_authorization_request_url,
                scopes=resolved_scopes,
                redirect_uri=callback_url or "",
                state=state,
            )
            return {"authorization_url": auth_url, "session_uri": state}

        # M2M: on-behalf-of flow (exchange workload token for service token)
        result = await self._run_sync(
            app.acquire_token_on_behalf_of,
            user_assertion=workload_token,
            scopes=resolved_scopes,
        )
        if "access_token" not in result:
            raise RuntimeError(f"OBO token exchange failed: {result.get('error_description', result)}")
        return {"access_token": result["access_token"], "token_type": TokenType.BEARER}

    async def get_api_key(
        self,
        credential_provider: str,
        workload_token: str,
    ) -> dict[str, Any]:
        def _fetch() -> dict[str, Any]:
            headers = self._graph_headers()
            # Look up the application by displayName
            resp = requests.get(
                f"{_GRAPH_BASE}/applications",
                headers=headers,
                params={"$filter": f"displayName eq '{credential_provider}'", "$top": "1"},
                timeout=30,
            )
            resp.raise_for_status()
            apps = resp.json().get("value", [])
            if not apps:
                raise ValueError(f"Application '{credential_provider}' not found in Entra")
            app_id = apps[0]["id"]

            # Get password credentials
            resp = requests.get(
                f"{_GRAPH_BASE}/applications/{app_id}",
                headers=headers,
                params={"$select": "passwordCredentials"},
                timeout=30,
            )
            resp.raise_for_status()
            creds = resp.json().get("passwordCredentials", [])
            secret = creds[0]["secretText"] if creds else ""
            return {"api_key": secret, "credential_provider": credential_provider}

        result: Any = await self._run_sync(_fetch)
        return result  # type: ignore[no-any-return]

    async def get_workload_token(
        self,
        workload_name: str,
        *,
        user_token: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        app = self._get_app()

        if user_token:
            result: dict[str, Any] = await self._run_sync(
                app.acquire_token_on_behalf_of,
                user_assertion=user_token,
                scopes=[f"api://{workload_name}/.default"],
            )
        else:
            result = await self._run_sync(
                app.acquire_token_for_client,
                scopes=[f"api://{workload_name}/.default"],
            )

        if "access_token" not in result:
            raise RuntimeError(f"Failed to get workload token: {result.get('error_description', result)}")
        return {
            "workload_token": result["access_token"],
            "workload_name": workload_name,
        }

    async def list_credential_providers(self) -> list[dict[str, Any]]:
        def _fetch() -> list[dict[str, Any]]:
            headers = self._graph_headers()
            results: list[dict[str, Any]] = []

            # List service principals (represent external OAuth2 providers)
            resp = requests.get(
                f"{_GRAPH_BASE}/servicePrincipals",
                headers=headers,
                params={"$select": "displayName,appId,servicePrincipalType", "$top": "100"},
                timeout=30,
            )
            resp.raise_for_status()
            for sp in resp.json().get("value", []):
                results.append(
                    {
                        "name": sp.get("displayName", ""),
                        "provider_type": CredentialProviderType.OAUTH2,
                        "metadata": {
                            "app_id": sp.get("appId", ""),
                            "type": sp.get("servicePrincipalType", ""),
                        },
                    }
                )

            return results

        result: Any = await self._run_sync(_fetch)
        return result  # type: ignore[no-any-return]

    # ── Data plane — 3LO completion ──────────────────────────────

    async def complete_auth(
        self,
        session_uri: str,
        *,
        user_token: str | None = None,
        user_id: str | None = None,
    ) -> None:
        # In the Entra flow, completion happens via authorization code exchange
        # in get_token(session_uri=code). This method is a no-op confirmation.
        pass

    # ── Control plane — credential provider management ───────────

    async def create_credential_provider(
        self,
        name: str,
        provider_type: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        def _create() -> dict[str, Any]:
            headers = self._graph_headers()

            if provider_type == CredentialProviderType.API_KEY:
                # Create an application registration with a client secret
                app_payload = {"displayName": name, **{k: v for k, v in config.items() if k != "api_key"}}
                resp = requests.post(
                    f"{_GRAPH_BASE}/applications",
                    headers=headers,
                    json=app_payload,
                    timeout=30,
                )
                resp.raise_for_status()
                app_data = resp.json()
                app_id = app_data["id"]

                # Add a password credential if api_key was provided
                if config.get("api_key"):
                    requests.post(
                        f"{_GRAPH_BASE}/applications/{app_id}/addPassword",
                        headers=headers,
                        json={"passwordCredential": {"displayName": "api-key"}},
                        timeout=30,
                    ).raise_for_status()

                return {
                    "name": name,
                    "provider_type": CredentialProviderType.API_KEY,
                    "arn": app_id,
                }

            if provider_type == CredentialProviderType.OAUTH2:
                # Create a service principal for an external app
                sp_payload = {"displayName": name, "appId": config.get("app_id", ""), **config}
                resp = requests.post(
                    f"{_GRAPH_BASE}/servicePrincipals",
                    headers=headers,
                    json=sp_payload,
                    timeout=30,
                )
                resp.raise_for_status()
                sp_data = resp.json()
                return {
                    "name": name,
                    "provider_type": CredentialProviderType.OAUTH2,
                    "arn": sp_data.get("id", ""),
                }

            raise ValueError(f"Unknown provider_type: {provider_type}")

        result: Any = await self._run_sync(_create)
        return result  # type: ignore[no-any-return]

    async def get_credential_provider(self, name: str) -> dict[str, Any]:
        def _fetch() -> dict[str, Any]:
            headers = self._graph_headers()

            # Try application first
            resp = requests.get(
                f"{_GRAPH_BASE}/applications",
                headers=headers,
                params={"$filter": f"displayName eq '{name}'", "$top": "1"},
                timeout=30,
            )
            resp.raise_for_status()
            apps = resp.json().get("value", [])
            if apps:
                return {
                    "name": apps[0].get("displayName", name),
                    "provider_type": CredentialProviderType.API_KEY,
                    "arn": apps[0].get("id", ""),
                }

            # Fall back to service principal
            resp = requests.get(
                f"{_GRAPH_BASE}/servicePrincipals",
                headers=headers,
                params={"$filter": f"displayName eq '{name}'", "$top": "1"},
                timeout=30,
            )
            resp.raise_for_status()
            sps = resp.json().get("value", [])
            if sps:
                return {
                    "name": sps[0].get("displayName", name),
                    "provider_type": CredentialProviderType.OAUTH2,
                    "arn": sps[0].get("id", ""),
                }

            raise ValueError(f"Credential provider '{name}' not found in Entra")

        result: Any = await self._run_sync(_fetch)
        return result  # type: ignore[no-any-return]

    async def update_credential_provider(
        self,
        name: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        def _update() -> dict[str, Any]:
            headers = self._graph_headers()

            # Try application first
            resp = requests.get(
                f"{_GRAPH_BASE}/applications",
                headers=headers,
                params={"$filter": f"displayName eq '{name}'", "$top": "1"},
                timeout=30,
            )
            resp.raise_for_status()
            apps = resp.json().get("value", [])
            if apps:
                app_id = apps[0]["id"]
                requests.patch(
                    f"{_GRAPH_BASE}/applications/{app_id}",
                    headers=headers,
                    json=config,
                    timeout=30,
                ).raise_for_status()
                return {"name": name, "provider_type": CredentialProviderType.API_KEY}

            # Fall back to service principal
            resp = requests.get(
                f"{_GRAPH_BASE}/servicePrincipals",
                headers=headers,
                params={"$filter": f"displayName eq '{name}'", "$top": "1"},
                timeout=30,
            )
            resp.raise_for_status()
            sps = resp.json().get("value", [])
            if sps:
                sp_id = sps[0]["id"]
                requests.patch(
                    f"{_GRAPH_BASE}/servicePrincipals/{sp_id}",
                    headers=headers,
                    json=config,
                    timeout=30,
                ).raise_for_status()
                return {"name": name, "provider_type": CredentialProviderType.OAUTH2}

            raise ValueError(f"Credential provider '{name}' not found in Entra")

        result: Any = await self._run_sync(_update)
        return result  # type: ignore[no-any-return]

    async def delete_credential_provider(self, name: str) -> None:
        def _delete() -> None:
            headers = self._graph_headers()

            # Try application first
            resp = requests.get(
                f"{_GRAPH_BASE}/applications",
                headers=headers,
                params={"$filter": f"displayName eq '{name}'", "$top": "1"},
                timeout=30,
            )
            resp.raise_for_status()
            apps = resp.json().get("value", [])
            if apps:
                requests.delete(
                    f"{_GRAPH_BASE}/applications/{apps[0]['id']}",
                    headers=headers,
                    timeout=30,
                ).raise_for_status()
                return

            # Fall back to service principal
            resp = requests.get(
                f"{_GRAPH_BASE}/servicePrincipals",
                headers=headers,
                params={"$filter": f"displayName eq '{name}'", "$top": "1"},
                timeout=30,
            )
            resp.raise_for_status()
            sps = resp.json().get("value", [])
            if sps:
                requests.delete(
                    f"{_GRAPH_BASE}/servicePrincipals/{sps[0]['id']}",
                    headers=headers,
                    timeout=30,
                ).raise_for_status()
                return

            raise ValueError(f"Credential provider '{name}' not found in Entra")

        await self._run_sync(_delete)  # type: ignore[no-any-return]

    # ── Control plane — workload identity management ─────────────

    async def create_workload_identity(
        self,
        name: str,
        *,
        allowed_return_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        def _create() -> dict[str, Any]:
            headers = self._graph_headers()
            payload: dict[str, Any] = {
                "displayName": name,
                "web": {"redirectUris": allowed_return_urls or []},
            }
            resp = requests.post(
                f"{_GRAPH_BASE}/applications",
                headers=headers,
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            app = resp.json()
            return {
                "name": app.get("displayName", name),
                "arn": app.get("id", ""),
                "allowed_return_urls": (app.get("web") or {}).get("redirectUris", []),
            }

        result: Any = await self._run_sync(_create)
        return result  # type: ignore[no-any-return]

    async def get_workload_identity(self, name: str) -> dict[str, Any]:
        def _fetch() -> dict[str, Any]:
            headers = self._graph_headers()
            resp = requests.get(
                f"{_GRAPH_BASE}/applications",
                headers=headers,
                params={"$filter": f"displayName eq '{name}'", "$top": "1"},
                timeout=30,
            )
            resp.raise_for_status()
            apps = resp.json().get("value", [])
            if not apps:
                raise ValueError(f"Workload identity '{name}' not found in Entra")
            app = apps[0]
            return {
                "name": app.get("displayName", name),
                "arn": app.get("id", ""),
                "allowed_return_urls": (app.get("web") or {}).get("redirectUris", []),
            }

        result: Any = await self._run_sync(_fetch)
        return result  # type: ignore[no-any-return]

    async def update_workload_identity(
        self,
        name: str,
        *,
        allowed_return_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        def _update() -> dict[str, Any]:
            headers = self._graph_headers()
            resp = requests.get(
                f"{_GRAPH_BASE}/applications",
                headers=headers,
                params={"$filter": f"displayName eq '{name}'", "$top": "1"},
                timeout=30,
            )
            resp.raise_for_status()
            apps = resp.json().get("value", [])
            if not apps:
                raise ValueError(f"Workload identity '{name}' not found in Entra")
            app_id = apps[0]["id"]

            requests.patch(
                f"{_GRAPH_BASE}/applications/{app_id}",
                headers=headers,
                json={"web": {"redirectUris": allowed_return_urls or []}},
                timeout=30,
            ).raise_for_status()
            return {
                "name": name,
                "arn": app_id,
                "allowed_return_urls": allowed_return_urls or [],
            }

        result: Any = await self._run_sync(_update)
        return result  # type: ignore[no-any-return]

    async def delete_workload_identity(self, name: str) -> None:
        def _delete() -> None:
            headers = self._graph_headers()
            resp = requests.get(
                f"{_GRAPH_BASE}/applications",
                headers=headers,
                params={"$filter": f"displayName eq '{name}'", "$top": "1"},
                timeout=30,
            )
            resp.raise_for_status()
            apps = resp.json().get("value", [])
            if not apps:
                raise ValueError(f"Workload identity '{name}' not found in Entra")
            requests.delete(
                f"{_GRAPH_BASE}/applications/{apps[0]['id']}",
                headers=headers,
                timeout=30,
            ).raise_for_status()

        await self._run_sync(_delete)  # type: ignore[no-any-return]

    async def list_workload_identities(self) -> list[dict[str, Any]]:
        def _fetch() -> list[dict[str, Any]]:
            headers = self._graph_headers()
            resp = requests.get(
                f"{_GRAPH_BASE}/applications",
                headers=headers,
                params={"$select": "id,displayName,web", "$top": "100"},
                timeout=30,
            )
            resp.raise_for_status()
            return [
                {
                    "name": app.get("displayName", ""),
                    "arn": app.get("id", ""),
                    "allowed_return_urls": (app.get("web") or {}).get("redirectUris", []),
                }
                for app in resp.json().get("value", [])
            ]

        result: Any = await self._run_sync(_fetch)
        return result  # type: ignore[no-any-return]

    # ── Health ───────────────────────────────────────────────────

    async def healthcheck(self) -> bool:
        try:
            app = self._get_app()
            result = await self._run_sync(
                app.acquire_token_for_client,
                scopes=["https://graph.microsoft.com/.default"],
            )
            return "access_token" in result
        except Exception:
            return False
