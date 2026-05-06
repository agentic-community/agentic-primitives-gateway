from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class EvaluationsProvider(ABC):
    """Abstract base class for evaluations providers.

    Evaluations providers manage evaluator lifecycle, run evaluations against
    agent outputs, record scores, and optionally support online evaluation
    configurations.

    Two distinct operations:
    - **evaluate**: Run an evaluation (e.g., LLM-as-a-judge) that computes
      scores from input/output data. The provider does the scoring.
    - **scores**: Record, retrieve, and manage pre-computed scores. The caller
      provides the scores.

    The ABC auto-wraps evaluator CRUD, score CRUD, and online-config CRUD
    on every subclass via ``__init_subclass__`` to emit
    ``evaluator.create`` / ``evaluator.update`` / ``evaluator.delete`` /
    ``evaluator.score.create`` / ``evaluator.score.delete`` /
    ``evaluator.online_config.create`` / ``evaluator.online_config.delete``
    audit events at the provider boundary.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        from agentic_primitives_gateway.primitives.evaluations._audit import (
            wrap_create_evaluator,
            wrap_create_online_evaluation_config,
            wrap_create_score,
            wrap_delete_evaluator,
            wrap_delete_online_evaluation_config,
            wrap_delete_score,
            wrap_update_evaluator,
        )

        own = cls.__dict__
        if "create_evaluator" in own:
            cls.create_evaluator = wrap_create_evaluator(own["create_evaluator"])  # type: ignore[method-assign]
        if "update_evaluator" in own:
            cls.update_evaluator = wrap_update_evaluator(own["update_evaluator"])  # type: ignore[method-assign]
        if "delete_evaluator" in own:
            cls.delete_evaluator = wrap_delete_evaluator(own["delete_evaluator"])  # type: ignore[method-assign]
        if "create_score" in own:
            cls.create_score = wrap_create_score(own["create_score"])  # type: ignore[method-assign]
        if "delete_score" in own:
            cls.delete_score = wrap_delete_score(own["delete_score"])  # type: ignore[method-assign]
        if "create_online_evaluation_config" in own:
            cls.create_online_evaluation_config = wrap_create_online_evaluation_config(  # type: ignore[method-assign]
                own["create_online_evaluation_config"]
            )
        if "delete_online_evaluation_config" in own:
            cls.delete_online_evaluation_config = wrap_delete_online_evaluation_config(  # type: ignore[method-assign]
                own["delete_online_evaluation_config"]
            )

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

    # ── Evaluate (compute scores) ──────────────────────────────────────

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

    # ── Score CRUD (record/retrieve pre-computed scores) ───────────────

    async def create_score(
        self,
        *,
        name: str,
        value: float | str,
        trace_id: str | None = None,
        observation_id: str | None = None,
        comment: str | None = None,
        data_type: str | None = None,
        config_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def get_score(self, score_id: str) -> dict[str, Any]:
        raise NotImplementedError

    async def delete_score(self, score_id: str) -> None:
        raise NotImplementedError

    async def list_scores(
        self,
        *,
        trace_id: str | None = None,
        name: str | None = None,
        config_id: str | None = None,
        data_type: str | None = None,
        page: int = 1,
        limit: int = 100,
    ) -> dict[str, Any]:
        raise NotImplementedError

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

    async def healthcheck(self) -> bool | str:
        return True
