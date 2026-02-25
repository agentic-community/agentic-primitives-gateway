from __future__ import annotations

import asyncio
import logging
import uuid
from functools import partial
from typing import Any
from urllib.parse import urlencode

import requests

from agentic_primitives_gateway.context import get_service_credentials_or_defaults
from agentic_primitives_gateway.models.enums import (
    AuthFlow,
    CredentialProviderType,
    TokenType,
)
from agentic_primitives_gateway.primitives.identity.base import IdentityProvider

logger = logging.getLogger(__name__)


class OktaIdentityProvider(IdentityProvider):
    """Identity provider backed by Okta.

    Uses Okta's OAuth2 endpoints for token operations and the Okta
    Management API for credential provider and workload identity CRUD.

    Prerequisites::

        pip install agentic-primitives-gateway[okta]

    Provider config example::

        backend: agentic_primitives_gateway.primitives.identity.okta.OktaIdentityProvider
        config:
          domain: "dev-123456.okta.com"
          client_id: "${OKTA_CLIENT_ID}"
          client_secret: "${OKTA_CLIENT_SECRET}"
          api_token: "${OKTA_API_TOKEN}"
          auth_server: "default"

    Per-request credential overrides via headers::

        X-Cred-Okta-Domain: dev-123456.okta.com
        X-Cred-Okta-Client-Id: <client>
        X-Cred-Okta-Client-Secret: <secret>
        X-Cred-Okta-Api-Token: <SSWS token>
    """

    def __init__(
        self,
        domain: str = "",
        client_id: str = "",
        client_secret: str | None = None,
        api_token: str | None = None,
        auth_server: str = "default",
        **kwargs: Any,
    ) -> None:
        self._domain = domain
        self._client_id = client_id
        self._client_secret = client_secret
        self._api_token = api_token
        self._auth_server = auth_server
        logger.info(
            "Okta identity provider initialized (domain=%s, client=%s, auth_server=%s)",
            domain,
            client_id,
            auth_server,
        )

    def _resolve_config(self) -> dict[str, str | None]:
        return get_service_credentials_or_defaults(
            "okta",
            {
                "domain": self._domain,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "api_token": self._api_token,
            },
        )

    def _base_url(self) -> str:
        cfg = self._resolve_config()
        domain = cfg["domain"] or self._domain
        return f"https://{domain}"

    def _token_url(self) -> str:
        return f"{self._base_url()}/oauth2/{self._auth_server}/v1/token"

    def _authorize_url(self) -> str:
        return f"{self._base_url()}/oauth2/{self._auth_server}/v1/authorize"

    def _admin_headers(self) -> dict[str, str]:
        cfg = self._resolve_config()
        token = cfg["api_token"] or self._api_token
        if not token:
            raise ValueError(
                "Okta API token required for admin operations. "
                "Set api_token in provider config or pass via X-Cred-Okta-Api-Token header."
            )
        return {"Authorization": f"SSWS {token}", "Accept": "application/json", "Content-Type": "application/json"}

    def _client_auth(self) -> tuple[str, str]:
        cfg = self._resolve_config()
        return (cfg["client_id"] or self._client_id or "", cfg["client_secret"] or self._client_secret or "")

    async def _run_sync(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(func, *args, **kwargs))

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
        scope = " ".join(scopes) if scopes else credential_provider

        # Authorization code exchange
        if session_uri:

            def _exchange_code() -> dict[str, Any]:
                resp = requests.post(
                    self._token_url(),
                    data={
                        "grant_type": "authorization_code",
                        "code": session_uri,
                        "redirect_uri": callback_url or "",
                        "scope": scope,
                    },
                    auth=self._client_auth(),
                    timeout=30,
                )
                resp.raise_for_status()
                data: dict[str, Any] = resp.json()
                return data

            result: dict[str, Any] = await self._run_sync(_exchange_code)
            return {"access_token": result["access_token"], "token_type": TokenType.BEARER}

        if auth_flow == AuthFlow.USER_FEDERATION:
            state = custom_state or uuid.uuid4().hex
            cfg = self._resolve_config()
            params: dict[str, str] = {
                "client_id": cfg["client_id"] or self._client_id or "",
                "response_type": "code",
                "scope": scope or "openid",
                "redirect_uri": callback_url or "",
                "state": state,
            }
            if custom_parameters:
                params.update(custom_parameters)
            auth_url = f"{self._authorize_url()}?{urlencode(params)}"
            return {"authorization_url": auth_url, "session_uri": state}

        # M2M: client_credentials with target scope
        def _client_credentials() -> dict[str, Any]:
            resp = requests.post(
                self._token_url(),
                data={
                    "grant_type": "client_credentials",
                    "scope": scope,
                },
                auth=self._client_auth(),
                timeout=30,
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return data

        result = await self._run_sync(_client_credentials)
        return {"access_token": result["access_token"], "token_type": TokenType.BEARER}

    async def get_api_key(
        self,
        credential_provider: str,
        workload_token: str,
    ) -> dict[str, Any]:
        def _fetch() -> dict[str, Any]:
            headers = self._admin_headers()
            # Find the app by label
            resp = requests.get(
                f"{self._base_url()}/api/v1/apps",
                headers=headers,
                params={"q": credential_provider, "limit": "1"},
                timeout=30,
            )
            resp.raise_for_status()
            apps = resp.json()
            if not apps:
                raise ValueError(f"Okta application '{credential_provider}' not found")

            # Get client secret from app credentials
            credentials = apps[0].get("credentials", {}).get("oauthClient", {})
            return {
                "api_key": credentials.get("client_secret", ""),
                "credential_provider": credential_provider,
            }

        result: Any = await self._run_sync(_fetch)
        return result  # type: ignore[no-any-return]

    async def get_workload_token(
        self,
        workload_name: str,
        *,
        user_token: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        def _get_token() -> dict[str, Any]:
            resp = requests.post(
                self._token_url(),
                data={
                    "grant_type": "client_credentials",
                    "scope": f"api://{workload_name}",
                },
                auth=self._client_auth(),
                timeout=30,
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return data

        result: dict[str, Any] = await self._run_sync(_get_token)
        return {
            "workload_token": result.get("access_token", ""),
            "workload_name": workload_name,
        }

    async def list_credential_providers(self) -> list[dict[str, Any]]:
        def _fetch() -> list[dict[str, Any]]:
            headers = self._admin_headers()
            results: list[dict[str, Any]] = []

            # Identity providers
            try:
                resp = requests.get(
                    f"{self._base_url()}/api/v1/idps",
                    headers=headers,
                    params={"limit": "100"},
                    timeout=30,
                )
                resp.raise_for_status()
                for idp in resp.json():
                    results.append(
                        {
                            "name": idp.get("name", ""),
                            "provider_type": CredentialProviderType.OAUTH2,
                            "metadata": {"type": idp.get("type", ""), "id": idp.get("id", "")},
                        }
                    )
            except Exception:
                logger.debug("Failed to list Okta identity providers", exc_info=True)

            # OAuth apps (as API key sources)
            try:
                resp = requests.get(
                    f"{self._base_url()}/api/v1/apps",
                    headers=headers,
                    params={"limit": "100", "filter": 'status eq "ACTIVE"'},
                    timeout=30,
                )
                resp.raise_for_status()
                for app in resp.json():
                    if app.get("signOnMode") == "OPENID_CONNECT":
                        results.append(
                            {
                                "name": app.get("label", ""),
                                "provider_type": CredentialProviderType.API_KEY,
                                "metadata": {"id": app.get("id", "")},
                            }
                        )
            except Exception:
                logger.debug("Failed to list Okta apps", exc_info=True)

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
        # In the Okta flow, completion happens via authorization code exchange
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
            headers = self._admin_headers()

            if provider_type == CredentialProviderType.OAUTH2:
                payload = {
                    "type": config.get("type", "OIDC"),
                    "name": name,
                    "protocol": config.get("protocol", {}),
                    "policy": config.get("policy", {}),
                }
                resp = requests.post(
                    f"{self._base_url()}/api/v1/idps",
                    headers=headers,
                    json=payload,
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                return {"name": name, "provider_type": CredentialProviderType.OAUTH2, "arn": data.get("id", "")}

            if provider_type == CredentialProviderType.API_KEY:
                payload = {
                    "name": name,
                    "label": name,
                    "signOnMode": "OPENID_CONNECT",
                    "credentials": {
                        "oauthClient": {
                            "client_id": config.get("client_id", ""),
                            "client_secret": config.get("api_key", ""),
                        }
                    },
                    "settings": {"oauthClient": {"grant_types": ["client_credentials"], "response_types": ["token"]}},
                }
                resp = requests.post(
                    f"{self._base_url()}/api/v1/apps",
                    headers=headers,
                    json=payload,
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                return {"name": name, "provider_type": CredentialProviderType.API_KEY, "arn": data.get("id", "")}

            raise ValueError(f"Unknown provider_type: {provider_type}")

        result: Any = await self._run_sync(_create)
        return result  # type: ignore[no-any-return]

    async def get_credential_provider(self, name: str) -> dict[str, Any]:
        def _fetch() -> dict[str, Any]:
            headers = self._admin_headers()

            # Try identity providers first
            resp = requests.get(
                f"{self._base_url()}/api/v1/idps", headers=headers, params={"q": name, "limit": "1"}, timeout=30
            )
            resp.raise_for_status()
            idps = resp.json()
            if idps:
                return {
                    "name": idps[0].get("name", name),
                    "provider_type": CredentialProviderType.OAUTH2,
                    "arn": idps[0].get("id", ""),
                }

            # Fall back to apps
            resp = requests.get(
                f"{self._base_url()}/api/v1/apps", headers=headers, params={"q": name, "limit": "1"}, timeout=30
            )
            resp.raise_for_status()
            apps = resp.json()
            if apps:
                return {
                    "name": apps[0].get("label", name),
                    "provider_type": CredentialProviderType.API_KEY,
                    "arn": apps[0].get("id", ""),
                }

            raise ValueError(f"Credential provider '{name}' not found in Okta")

        result: Any = await self._run_sync(_fetch)
        return result  # type: ignore[no-any-return]

    async def update_credential_provider(
        self,
        name: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        def _update() -> dict[str, Any]:
            headers = self._admin_headers()

            # Try identity providers first
            resp = requests.get(
                f"{self._base_url()}/api/v1/idps", headers=headers, params={"q": name, "limit": "1"}, timeout=30
            )
            resp.raise_for_status()
            idps = resp.json()
            if idps:
                idp_id = idps[0]["id"]
                requests.put(
                    f"{self._base_url()}/api/v1/idps/{idp_id}", headers=headers, json=config, timeout=30
                ).raise_for_status()
                return {"name": name, "provider_type": CredentialProviderType.OAUTH2}

            # Fall back to apps
            resp = requests.get(
                f"{self._base_url()}/api/v1/apps", headers=headers, params={"q": name, "limit": "1"}, timeout=30
            )
            resp.raise_for_status()
            apps = resp.json()
            if apps:
                app_id = apps[0]["id"]
                requests.put(
                    f"{self._base_url()}/api/v1/apps/{app_id}", headers=headers, json=config, timeout=30
                ).raise_for_status()
                return {"name": name, "provider_type": CredentialProviderType.API_KEY}

            raise ValueError(f"Credential provider '{name}' not found in Okta")

        result: Any = await self._run_sync(_update)
        return result  # type: ignore[no-any-return]

    async def delete_credential_provider(self, name: str) -> None:
        def _delete() -> None:
            headers = self._admin_headers()

            resp = requests.get(
                f"{self._base_url()}/api/v1/idps", headers=headers, params={"q": name, "limit": "1"}, timeout=30
            )
            resp.raise_for_status()
            idps = resp.json()
            if idps:
                requests.delete(
                    f"{self._base_url()}/api/v1/idps/{idps[0]['id']}", headers=headers, timeout=30
                ).raise_for_status()
                return

            resp = requests.get(
                f"{self._base_url()}/api/v1/apps", headers=headers, params={"q": name, "limit": "1"}, timeout=30
            )
            resp.raise_for_status()
            apps = resp.json()
            if apps:
                requests.delete(
                    f"{self._base_url()}/api/v1/apps/{apps[0]['id']}", headers=headers, timeout=30
                ).raise_for_status()
                return

            raise ValueError(f"Credential provider '{name}' not found in Okta")

        await self._run_sync(_delete)

    # ── Control plane — workload identity management ─────────────

    async def create_workload_identity(
        self,
        name: str,
        *,
        allowed_return_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        def _create() -> dict[str, Any]:
            headers = self._admin_headers()
            payload: dict[str, Any] = {
                "name": name,
                "label": name,
                "signOnMode": "OPENID_CONNECT",
                "settings": {
                    "oauthClient": {
                        "grant_types": ["client_credentials"],
                        "response_types": ["token"],
                        "redirect_uris": allowed_return_urls or [],
                    }
                },
            }
            resp = requests.post(f"{self._base_url()}/api/v1/apps", headers=headers, json=payload, timeout=30)
            resp.raise_for_status()
            app = resp.json()
            return {
                "name": app.get("label", name),
                "arn": app.get("id", ""),
                "allowed_return_urls": (app.get("settings", {}).get("oauthClient", {}).get("redirect_uris", [])),
            }

        result: Any = await self._run_sync(_create)
        return result  # type: ignore[no-any-return]

    async def get_workload_identity(self, name: str) -> dict[str, Any]:
        def _fetch() -> dict[str, Any]:
            headers = self._admin_headers()
            resp = requests.get(
                f"{self._base_url()}/api/v1/apps", headers=headers, params={"q": name, "limit": "1"}, timeout=30
            )
            resp.raise_for_status()
            apps = resp.json()
            if not apps:
                raise ValueError(f"Workload identity '{name}' not found in Okta")
            app = apps[0]
            return {
                "name": app.get("label", name),
                "arn": app.get("id", ""),
                "allowed_return_urls": (app.get("settings", {}).get("oauthClient", {}).get("redirect_uris", [])),
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
            headers = self._admin_headers()
            resp = requests.get(
                f"{self._base_url()}/api/v1/apps", headers=headers, params={"q": name, "limit": "1"}, timeout=30
            )
            resp.raise_for_status()
            apps = resp.json()
            if not apps:
                raise ValueError(f"Workload identity '{name}' not found in Okta")
            app_id = apps[0]["id"]
            payload = {"settings": {"oauthClient": {"redirect_uris": allowed_return_urls or []}}}
            requests.put(
                f"{self._base_url()}/api/v1/apps/{app_id}", headers=headers, json=payload, timeout=30
            ).raise_for_status()
            return {"name": name, "arn": app_id, "allowed_return_urls": allowed_return_urls or []}

        result: Any = await self._run_sync(_update)
        return result  # type: ignore[no-any-return]

    async def delete_workload_identity(self, name: str) -> None:
        def _delete() -> None:
            headers = self._admin_headers()
            resp = requests.get(
                f"{self._base_url()}/api/v1/apps", headers=headers, params={"q": name, "limit": "1"}, timeout=30
            )
            resp.raise_for_status()
            apps = resp.json()
            if not apps:
                raise ValueError(f"Workload identity '{name}' not found in Okta")
            requests.delete(
                f"{self._base_url()}/api/v1/apps/{apps[0]['id']}", headers=headers, timeout=30
            ).raise_for_status()

        await self._run_sync(_delete)

    async def list_workload_identities(self) -> list[dict[str, Any]]:
        def _fetch() -> list[dict[str, Any]]:
            headers = self._admin_headers()
            resp = requests.get(
                f"{self._base_url()}/api/v1/apps",
                headers=headers,
                params={"limit": "100", "filter": 'status eq "ACTIVE"'},
                timeout=30,
            )
            resp.raise_for_status()
            return [
                {
                    "name": app.get("label", ""),
                    "arn": app.get("id", ""),
                    "allowed_return_urls": (app.get("settings", {}).get("oauthClient", {}).get("redirect_uris", [])),
                }
                for app in resp.json()
                if app.get("signOnMode") == "OPENID_CONNECT"
            ]

        result: Any = await self._run_sync(_fetch)
        return result  # type: ignore[no-any-return]

    # ── Health ───────────────────────────────────────────────────

    async def healthcheck(self) -> bool:
        try:
            resp: Any = await self._run_sync(
                requests.get,
                f"{self._base_url()}/.well-known/openid-configuration",
                timeout=10,
            )
            result: bool = resp.status_code == 200
            return result
        except Exception:
            return False
