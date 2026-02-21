from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class IdentityProvider(ABC):
    """Abstract base class for identity providers.

    Handles token exchange, API key retrieval, and credential management
    for agent-to-service authentication.
    """

    @abstractmethod
    async def get_token(
        self,
        provider_name: str,
        scopes: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def get_api_key(
        self,
        provider_name: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def list_providers(self) -> list[dict[str, Any]]: ...

    async def healthcheck(self) -> bool:
        return True
