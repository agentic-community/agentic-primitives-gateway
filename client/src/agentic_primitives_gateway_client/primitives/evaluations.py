from __future__ import annotations

from typing import Any

from agentic_primitives_gateway_client.client import AgenticPlatformClient


class Evaluations:
    """Helper for the evaluations primitive — evaluator management and evaluation."""

    def __init__(self, client: AgenticPlatformClient) -> None:
        self._client = client

    async def create_evaluator(
        self,
        name: str,
        evaluator_type: str,
        config: dict[str, Any] | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        return await self._client.create_evaluator(
            name=name, evaluator_type=evaluator_type, config=config, description=description
        )

    async def get_evaluator(self, evaluator_id: str) -> dict[str, Any]:
        return await self._client.get_evaluator(evaluator_id)

    async def update_evaluator(
        self,
        evaluator_id: str,
        config: dict[str, Any] | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        return await self._client.update_evaluator(evaluator_id, config=config, description=description)

    async def delete_evaluator(self, evaluator_id: str) -> None:
        return await self._client.delete_evaluator(evaluator_id)

    async def list_evaluators(self, max_results: int = 100, next_token: str | None = None) -> dict[str, Any]:
        return await self._client.list_evaluators(max_results=max_results, next_token=next_token)

    async def evaluate(self, evaluator_id: str, **kwargs: Any) -> dict[str, Any]:
        return await self._client.evaluate(evaluator_id=evaluator_id, **kwargs)
