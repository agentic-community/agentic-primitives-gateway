from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any


class LLMProvider(ABC):
    """Abstract base class for LLM gateway providers."""

    @abstractmethod
    async def route_request(self, model_request: dict[str, Any]) -> dict[str, Any]: ...

    async def route_request_stream(self, model_request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Stream LLM response as incremental events.

        Yields dicts with a ``type`` key:
        - ``{"type": "content_delta", "delta": "..."}``
        - ``{"type": "tool_use_start", "id": "...", "name": "..."}``
        - ``{"type": "tool_use_delta", "id": "...", "delta": "..."}``
        - ``{"type": "message_stop", "stop_reason": "...", "usage": {...}}``

        Default implementation falls back to non-streaming ``route_request``.
        """
        response = await self.route_request(model_request)
        content = response.get("content", "")
        if content:
            yield {"type": "content_delta", "delta": content}
        tool_calls = response.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                yield {
                    "type": "tool_use_start",
                    "id": tc.get("id", ""),
                    "name": tc["name"],
                    "input": tc.get("input", {}),
                }
        yield {
            "type": "message_stop",
            "stop_reason": response.get("stop_reason", "end_turn"),
            "usage": response.get("usage", {}),
            "model": response.get("model", ""),
        }

    @abstractmethod
    async def list_models(self) -> list[dict[str, Any]]: ...

    async def healthcheck(self) -> bool | str:
        return True
