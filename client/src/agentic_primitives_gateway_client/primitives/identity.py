from __future__ import annotations

import asyncio
from typing import Any

from agentic_primitives_gateway_client.client import AgenticPlatformClient


class Identity:
    """Helper for the identity primitive — token and API key exchange."""

    def __init__(self, client: AgenticPlatformClient) -> None:
        self._client = client
        self._loop: asyncio.AbstractEventLoop | None = None

    async def get_token(
        self,
        provider_name: str,
        scopes: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Exchange credentials for an access token."""
        try:
            result = await self._client.get_token(provider_name, scopes=scopes, context=context)
            token_preview = result["access_token"][:20] + "..." if result["access_token"] else "(empty)"
            return f"Got {result.get('token_type', 'Bearer')} token for {provider_name}: {token_preview}"
        except Exception as e:
            return f"Failed to get token for {provider_name}: {e}"

    async def get_api_key(
        self,
        provider_name: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Retrieve an API key for a service."""
        try:
            result = await self._client.get_api_key(provider_name, context=context)
            key_preview = result["api_key"][:10] + "..." if result["api_key"] else "(empty)"
            return f"Got API key for {provider_name}: {key_preview}"
        except Exception as e:
            return f"Failed to get API key for {provider_name}: {e}"

    # ── Sync wrappers ───────────────────────────────────────────────

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _sync(self, coro: Any) -> Any:
        return self._get_loop().run_until_complete(coro)

    def get_token_sync(
        self,
        provider_name: str,
        scopes: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> str:
        return str(self._sync(self.get_token(provider_name, scopes, context)))

    def get_api_key_sync(
        self,
        provider_name: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        return str(self._sync(self.get_api_key(provider_name, context)))
