from __future__ import annotations

import asyncio
from typing import Any

from agentic_primitives_gateway_client.client import AgenticPlatformClient


class Identity:
    """Helper for the identity primitive — token exchange, API keys, and workload tokens."""

    def __init__(self, client: AgenticPlatformClient) -> None:
        self._client = client
        self._loop: asyncio.AbstractEventLoop | None = None

    async def get_token(
        self,
        credential_provider: str,
        workload_token: str,
        *,
        auth_flow: str = "M2M",
        scopes: list[str] | None = None,
    ) -> str:
        """Exchange a workload token for an external service OAuth2 token."""
        try:
            result = await self._client.get_token(
                credential_provider,
                workload_token,
                auth_flow=auth_flow,
                scopes=scopes,
            )
            if result.get("authorization_url"):
                return f"Authorization required: {result['authorization_url']}"
            token_preview = result["access_token"][:20] + "..." if result.get("access_token") else "(empty)"
            return f"Got {result.get('token_type', 'Bearer')} token for {credential_provider}: {token_preview}"
        except Exception as e:
            return f"Failed to get token for {credential_provider}: {e}"

    async def get_api_key(
        self,
        credential_provider: str,
        workload_token: str,
    ) -> str:
        """Retrieve an API key for a credential provider."""
        try:
            result = await self._client.get_api_key(credential_provider, workload_token)
            key_preview = result["api_key"][:10] + "..." if result.get("api_key") else "(empty)"
            return f"Got API key for {credential_provider}: {key_preview}"
        except Exception as e:
            return f"Failed to get API key for {credential_provider}: {e}"

    async def get_workload_token(
        self,
        workload_name: str,
        *,
        user_token: str | None = None,
        user_id: str | None = None,
    ) -> str:
        """Obtain a workload identity token for the agent."""
        try:
            result = await self._client.get_workload_token(
                workload_name,
                user_token=user_token,
                user_id=user_id,
            )
            token_preview = result["workload_token"][:20] + "..." if result.get("workload_token") else "(empty)"
            return f"Got workload token for {workload_name}: {token_preview}"
        except Exception as e:
            return f"Failed to get workload token for {workload_name}: {e}"

    # ── Sync wrappers ───────────────────────────────────────────────

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _sync(self, coro: Any) -> Any:
        return self._get_loop().run_until_complete(coro)

    def get_token_sync(
        self,
        credential_provider: str,
        workload_token: str,
        *,
        auth_flow: str = "M2M",
        scopes: list[str] | None = None,
    ) -> str:
        return str(self._sync(self.get_token(credential_provider, workload_token, auth_flow=auth_flow, scopes=scopes)))

    def get_api_key_sync(
        self,
        credential_provider: str,
        workload_token: str,
    ) -> str:
        return str(self._sync(self.get_api_key(credential_provider, workload_token)))

    def get_workload_token_sync(
        self,
        workload_name: str,
        *,
        user_token: str | None = None,
        user_id: str | None = None,
    ) -> str:
        return str(self._sync(self.get_workload_token(workload_name, user_token=user_token, user_id=user_id)))
