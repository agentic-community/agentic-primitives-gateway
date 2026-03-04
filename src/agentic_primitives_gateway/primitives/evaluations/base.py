from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class EvaluationsProvider(ABC):
    """Abstract base class for evaluations providers.

    Evaluations providers manage evaluator lifecycle, run evaluations against
    agent outputs, and optionally support online evaluation configurations.
    """

    # ── Evaluator CRUD ─────────────────────────────────────────────────

    @abstractmethod
    async def create_evaluator(
        self,
        name: str,
        evaluator_type: str,
        config: dict[str, Any] | None = None,
        description: str = "",
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def get_evaluator(self, evaluator_id: str) -> dict[str, Any]: ...

    @abstractmethod
    async def update_evaluator(
        self,
        evaluator_id: str,
        config: dict[str, Any] | None = None,
        description: str | None = None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def delete_evaluator(self, evaluator_id: str) -> None: ...

    @abstractmethod
    async def list_evaluators(
        self,
        max_results: int = 100,
        next_token: str | None = None,
    ) -> dict[str, Any]: ...

    # ── Evaluate ───────────────────────────────────────────────────────

    @abstractmethod
    async def evaluate(
        self,
        evaluator_id: str,
        target: str | None = None,
        input_data: str | None = None,
        output_data: str | None = None,
        expected_output: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    # ── Online evaluation configs (optional) ───────────────────────────

    async def create_online_evaluation_config(
        self,
        name: str,
        evaluator_ids: list[str],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def get_online_evaluation_config(
        self,
        config_id: str,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def delete_online_evaluation_config(
        self,
        config_id: str,
    ) -> None:
        raise NotImplementedError

    async def list_online_evaluation_configs(
        self,
        max_results: int = 100,
        next_token: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def healthcheck(self) -> bool:
        return True
