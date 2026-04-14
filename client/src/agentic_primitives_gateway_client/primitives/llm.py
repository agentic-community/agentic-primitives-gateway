"""LLM helper for the Agentic Primitives Gateway client.

Provides a convenience wrapper around the LLM routing endpoints.

Usage (async)::

    from agentic_primitives_gateway_client import AgenticPlatformClient, LLM

    client = AgenticPlatformClient("http://localhost:8000", ...)
    llm = LLM(client)

    result = await llm.completions(model="...", messages=[...])
    models = await llm.list_models()

Usage (sync)::

    result = llm.completions_sync(model="...", messages=[...])
"""

from __future__ import annotations

import asyncio
from typing import Any

from agentic_primitives_gateway_client.client import AgenticPlatformClient


class LLM:
    """Helper for the LLM primitive — model request routing."""

    def __init__(self, client: AgenticPlatformClient) -> None:
        self._client = client
        self._loop: asyncio.AbstractEventLoop | None = None

    async def completions(
        self,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 1.0,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Route a completion request to the configured LLM backend."""
        request: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            **kwargs,
        }
        if max_tokens is not None:
            request["max_tokens"] = max_tokens
        return await self._client.completions(request)

    async def list_models(self) -> list[dict[str, Any]]:
        """List available models from the LLM backend."""
        result = await self._client.list_models()
        models: list[dict[str, Any]] = result.get("models", [])
        return models

    # ── Sync wrappers ───────────────────────────────────────────────

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _sync(self, coro: Any) -> Any:
        return self._get_loop().run_until_complete(coro)

    def completions_sync(
        self,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 1.0,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        result: dict[str, Any] = self._sync(self.completions(model, messages, temperature, max_tokens, **kwargs))
        return result

    def list_models_sync(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._sync(self.list_models())
        return result
