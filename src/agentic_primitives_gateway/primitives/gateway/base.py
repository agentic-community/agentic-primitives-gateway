from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class GatewayProvider(ABC):
    """Abstract base class for LLM gateway providers."""

    @abstractmethod
    async def route_request(self, model_request: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    async def list_models(self) -> list[dict[str, Any]]: ...

    async def healthcheck(self) -> bool:
        return True
