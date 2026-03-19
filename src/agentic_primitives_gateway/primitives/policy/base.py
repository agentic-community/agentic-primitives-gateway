from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class PolicyProvider(ABC):
    """Abstract base class for policy providers.

    Manages policy engines, policies, and optional policy generation.
    """

    # ── Policy engines ────────────────────────────────────────────────

    @abstractmethod
    async def create_policy_engine(
        self,
        name: str,
        description: str = "",
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def get_policy_engine(self, engine_id: str) -> dict[str, Any]: ...

    @abstractmethod
    async def delete_policy_engine(self, engine_id: str) -> None: ...

    @abstractmethod
    async def list_policy_engines(
        self,
        max_results: int = 100,
        next_token: str | None = None,
    ) -> dict[str, Any]: ...

    # ── Policies ──────────────────────────────────────────────────────

    @abstractmethod
    async def create_policy(
        self,
        engine_id: str,
        policy_body: str,
        description: str = "",
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def get_policy(
        self,
        engine_id: str,
        policy_id: str,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def update_policy(
        self,
        engine_id: str,
        policy_id: str,
        policy_body: str,
        description: str | None = None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def delete_policy(
        self,
        engine_id: str,
        policy_id: str,
    ) -> None: ...

    @abstractmethod
    async def list_policies(
        self,
        engine_id: str,
        max_results: int = 100,
        next_token: str | None = None,
    ) -> dict[str, Any]: ...

    # ── Policy generation (optional) ──────────────────────────────────

    async def start_policy_generation(
        self,
        engine_id: str,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def get_policy_generation(
        self,
        engine_id: str,
        generation_id: str,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def list_policy_generations(
        self,
        engine_id: str,
        max_results: int = 100,
        next_token: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def list_policy_generation_assets(
        self,
        engine_id: str,
        generation_id: str,
        max_results: int = 100,
        next_token: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    # ── Health ────────────────────────────────────────────────────────

    async def healthcheck(self) -> bool | str:
        return True
