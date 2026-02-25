from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Any

from bedrock_agentcore.services.identity import (
    IdentityClient,
    UserIdIdentifier,
    UserTokenIdentifier,
)

from agentic_primitives_gateway.context import get_boto3_session
from agentic_primitives_gateway.models.enums import (
    AuthFlow,
    CredentialProviderType,
    TokenType,
)
from agentic_primitives_gateway.primitives.identity.base import IdentityProvider

logger = logging.getLogger(__name__)


class AgentCoreIdentityProvider(IdentityProvider):
    """Identity provider backed by AWS Bedrock AgentCore Identity service.

    AWS credentials are read from request context on every call. The caller's
    boto3 session is used to authenticate to the AgentCore Identity service.

    Provider config example::

        backend: agentic_primitives_gateway.primitives.identity.agentcore.AgentCoreIdentityProvider
        config:
          region: "us-east-1"
    """

    def __init__(self, region: str = "us-east-1", **kwargs: Any) -> None:
        self._region = region
        logger.info("AgentCore identity provider initialized (region=%s)", region)

    def _get_client(self) -> IdentityClient:
        """Create an IdentityClient using the current request's boto3 session."""
        session = get_boto3_session(default_region=self._region)
        return IdentityClient(region=session.region_name)

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
        client = self._get_client()

        req: dict[str, Any] = {
            "resourceCredentialProviderName": credential_provider,
            "workloadIdentityToken": workload_token,
            "oauth2Flow": auth_flow,
            "scopes": scopes,
        }
        if callback_url:
            req["resourceOauth2ReturnUrl"] = callback_url
        if force_auth:
            req["forceAuthentication"] = force_auth
        if session_uri:
            req["sessionUri"] = session_uri
        if custom_state:
            req["customState"] = custom_state
        if custom_parameters:
            req["customParameters"] = custom_parameters

        response: dict[str, Any] = await self._run_sync(client.dp_client.get_resource_oauth2_token, **req)

        if "accessToken" in response:
            return {"access_token": response["accessToken"], "token_type": TokenType.BEARER}

        if "authorizationUrl" in response:
            result: dict[str, Any] = {"authorization_url": response["authorizationUrl"]}
            if "sessionUri" in response:
                result["session_uri"] = response["sessionUri"]
            return result

        raise RuntimeError("Identity service did not return a token or an authorization URL.")

    async def get_api_key(
        self,
        credential_provider: str,
        workload_token: str,
    ) -> dict[str, Any]:
        client = self._get_client()

        req = {
            "resourceCredentialProviderName": credential_provider,
            "workloadIdentityToken": workload_token,
        }
        response: dict[str, Any] = await self._run_sync(client.dp_client.get_resource_api_key, **req)
        return {"api_key": response["apiKey"], "credential_provider": credential_provider}

    async def get_workload_token(
        self,
        workload_name: str,
        *,
        user_token: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        client = self._get_client()

        response: dict[str, Any] = await self._run_sync(
            client.get_workload_access_token,
            workload_name=workload_name,
            user_token=user_token,
            user_id=user_id,
        )
        return {
            "workload_token": response.get("workloadAccessToken", ""),
            "workload_name": workload_name,
        }

    async def list_credential_providers(self) -> list[dict[str, Any]]:
        client = self._get_client()
        results: list[dict[str, Any]] = []

        try:
            oauth2_resp: dict[str, Any] = await self._run_sync(client.cp_client.list_oauth2_credential_providers)
            for item in oauth2_resp.get("credentialProviders", []):
                results.append(
                    {
                        "name": item.get("name", ""),
                        "provider_type": CredentialProviderType.OAUTH2,
                        "arn": item.get("credentialProviderArn", ""),
                    }
                )
        except Exception:
            logger.debug("Failed to list OAuth2 credential providers", exc_info=True)

        try:
            api_key_resp: dict[str, Any] = await self._run_sync(client.cp_client.list_api_key_credential_providers)
            for item in api_key_resp.get("credentialProviders", []):
                results.append(
                    {
                        "name": item.get("name", ""),
                        "provider_type": CredentialProviderType.API_KEY,
                        "arn": item.get("credentialProviderArn", ""),
                    }
                )
        except Exception:
            logger.debug("Failed to list API key credential providers", exc_info=True)

        return results

    # ── Data plane — 3LO completion ──────────────────────────────

    async def complete_auth(
        self,
        session_uri: str,
        *,
        user_token: str | None = None,
        user_id: str | None = None,
    ) -> None:
        client = self._get_client()

        identifier: UserTokenIdentifier | UserIdIdentifier
        if user_token:
            identifier = UserTokenIdentifier(user_token=user_token)
        elif user_id:
            identifier = UserIdIdentifier(user_id=user_id)
        else:
            raise ValueError("Either user_token or user_id is required for complete_auth")

        await self._run_sync(
            client.complete_resource_token_auth,
            session_uri=session_uri,
            user_identifier=identifier,
        )

    # ── Control plane — credential provider management ───────────

    async def create_credential_provider(
        self,
        name: str,
        provider_type: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        client = self._get_client()

        if provider_type == CredentialProviderType.OAUTH2:
            req = {"name": name, **config}
            response: dict[str, Any] = await self._run_sync(client.create_oauth2_credential_provider, req)
        elif provider_type == CredentialProviderType.API_KEY:
            req = {"name": name, **config}
            response = await self._run_sync(client.create_api_key_credential_provider, req)
        else:
            raise ValueError(f"Unknown provider_type: {provider_type}")

        return {
            "name": response.get("name", name),
            "provider_type": provider_type,
            "arn": response.get("credentialProviderArn", ""),
        }

    async def get_credential_provider(self, name: str) -> dict[str, Any]:
        client = self._get_client()

        # Try OAuth2 first, fall back to API key
        try:
            response: dict[str, Any] = await self._run_sync(client.cp_client.get_oauth2_credential_provider, name=name)
            return {
                "name": response.get("name", name),
                "provider_type": CredentialProviderType.OAUTH2,
                "arn": response.get("credentialProviderArn", ""),
            }
        except Exception:
            pass

        response = await self._run_sync(client.cp_client.get_api_key_credential_provider, name=name)
        return {
            "name": response.get("name", name),
            "provider_type": CredentialProviderType.API_KEY,
            "arn": response.get("credentialProviderArn", ""),
        }

    async def update_credential_provider(
        self,
        name: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        client = self._get_client()

        # Try OAuth2 first, fall back to API key
        try:
            req = {"name": name, **config}
            response: dict[str, Any] = await self._run_sync(client.cp_client.update_oauth2_credential_provider, **req)
            return {
                "name": response.get("name", name),
                "provider_type": CredentialProviderType.OAUTH2,
            }
        except Exception:
            pass

        req = {"name": name, **config}
        response = await self._run_sync(client.cp_client.update_api_key_credential_provider, **req)
        return {
            "name": response.get("name", name),
            "provider_type": CredentialProviderType.API_KEY,
        }

    async def delete_credential_provider(self, name: str) -> None:
        client = self._get_client()

        # Try OAuth2 first, fall back to API key
        try:
            await self._run_sync(client.cp_client.delete_oauth2_credential_provider, name=name)
            return
        except Exception:
            pass

        await self._run_sync(client.cp_client.delete_api_key_credential_provider, name=name)

    # ── Control plane — workload identity management ─────────────

    async def create_workload_identity(
        self,
        name: str,
        *,
        allowed_return_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        client = self._get_client()

        response: dict[str, Any] = await self._run_sync(
            client.create_workload_identity,
            name=name,
            allowed_resource_oauth_2_return_urls=allowed_return_urls,
        )
        return {
            "name": response.get("name", name),
            "arn": response.get("workloadIdentityArn", ""),
            "allowed_return_urls": response.get("allowedResourceOauth2ReturnUrls", []),
        }

    async def get_workload_identity(self, name: str) -> dict[str, Any]:
        client = self._get_client()

        response: dict[str, Any] = await self._run_sync(client.get_workload_identity, name=name)
        return {
            "name": response.get("name", name),
            "arn": response.get("workloadIdentityArn", ""),
            "allowed_return_urls": response.get("allowedResourceOauth2ReturnUrls", []),
        }

    async def update_workload_identity(
        self,
        name: str,
        *,
        allowed_return_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        client = self._get_client()

        response: dict[str, Any] = await self._run_sync(
            client.update_workload_identity,
            name=name,
            allowed_resource_oauth_2_return_urls=allowed_return_urls or [],
        )
        return {
            "name": response.get("name", name),
            "arn": response.get("workloadIdentityArn", ""),
            "allowed_return_urls": response.get("allowedResourceOauth2ReturnUrls", []),
        }

    async def delete_workload_identity(self, name: str) -> None:
        client = self._get_client()
        await self._run_sync(client.cp_client.delete_workload_identity, name=name)

    async def list_workload_identities(self) -> list[dict[str, Any]]:
        client = self._get_client()

        response: dict[str, Any] = await self._run_sync(client.cp_client.list_workload_identities)
        return [
            {
                "name": item.get("name", ""),
                "arn": item.get("workloadIdentityArn", ""),
            }
            for item in response.get("workloadIdentities", [])
        ]

    async def healthcheck(self) -> bool:
        return True
