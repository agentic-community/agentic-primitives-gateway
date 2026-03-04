from __future__ import annotations

from typing import Any

from agentic_primitives_gateway_client.client import AgenticPlatformClient


class Policy:
    """Helper for the policy primitive — engine and policy management."""

    def __init__(self, client: AgenticPlatformClient) -> None:
        self._client = client

    # Engine operations
    async def create_engine(
        self, name: str, description: str = "", config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return await self._client.create_policy_engine(name=name, description=description, config=config)

    async def get_engine(self, engine_id: str) -> dict[str, Any]:
        return await self._client.get_policy_engine(engine_id)

    async def delete_engine(self, engine_id: str) -> None:
        return await self._client.delete_policy_engine(engine_id)

    async def list_engines(self, max_results: int = 100, next_token: str | None = None) -> dict[str, Any]:
        return await self._client.list_policy_engines(max_results=max_results, next_token=next_token)

    # Policy operations
    async def create_policy(self, engine_id: str, policy_body: str, description: str = "") -> dict[str, Any]:
        return await self._client.create_policy(engine_id, policy_body=policy_body, description=description)

    async def get_policy(self, engine_id: str, policy_id: str) -> dict[str, Any]:
        return await self._client.get_policy(engine_id, policy_id)

    async def update_policy(
        self,
        engine_id: str,
        policy_id: str,
        policy_body: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        return await self._client.update_policy(engine_id, policy_id, policy_body=policy_body, description=description)

    async def delete_policy(self, engine_id: str, policy_id: str) -> None:
        return await self._client.delete_policy(engine_id, policy_id)

    async def list_policies(
        self, engine_id: str, max_results: int = 100, next_token: str | None = None
    ) -> dict[str, Any]:
        return await self._client.list_policies(engine_id, max_results=max_results, next_token=next_token)
