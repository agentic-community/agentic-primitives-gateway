from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from agentic_primitives_gateway.primitives.evaluations.base import EvaluationsProvider

logger = logging.getLogger(__name__)


class NoopEvaluationsProvider(EvaluationsProvider):
    """No-op evaluations provider that maintains in-memory evaluators."""

    def __init__(self, **kwargs: Any) -> None:
        self._evaluators: dict[str, dict[str, Any]] = {}
        logger.info("NoopEvaluationsProvider initialized")

    async def create_evaluator(
        self,
        name: str,
        evaluator_type: str,
        config: dict[str, Any] | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        evaluator_id = uuid.uuid4().hex[:12]
        now = datetime.now(UTC).isoformat()
        evaluator: dict[str, Any] = {
            "evaluator_id": evaluator_id,
            "name": name,
            "evaluator_type": evaluator_type,
            "description": description,
            "status": "ACTIVE",
            "created_at": now,
            "config": config or {},
        }
        self._evaluators[evaluator_id] = evaluator
        logger.debug("noop create_evaluator: id=%s name=%s", evaluator_id, name)
        return evaluator

    async def get_evaluator(self, evaluator_id: str) -> dict[str, Any]:
        logger.debug("noop get_evaluator: id=%s", evaluator_id)
        evaluator = self._evaluators.get(evaluator_id)
        if evaluator is None:
            raise KeyError(f"Evaluator not found: {evaluator_id}")
        return evaluator

    async def update_evaluator(
        self,
        evaluator_id: str,
        config: dict[str, Any] | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        logger.debug("noop update_evaluator: id=%s", evaluator_id)
        evaluator = self._evaluators.get(evaluator_id)
        if evaluator is None:
            raise KeyError(f"Evaluator not found: {evaluator_id}")
        if config is not None:
            evaluator["config"] = config
        if description is not None:
            evaluator["description"] = description
        return evaluator

    async def delete_evaluator(self, evaluator_id: str) -> None:
        logger.debug("noop delete_evaluator: id=%s", evaluator_id)
        self._evaluators.pop(evaluator_id, None)

    async def list_evaluators(
        self,
        max_results: int = 100,
        next_token: str | None = None,
    ) -> dict[str, Any]:
        logger.debug("noop list_evaluators")
        evaluators = list(self._evaluators.values())[:max_results]
        return {"evaluators": evaluators, "next_token": None}

    async def evaluate(
        self,
        evaluator_id: str,
        target: str | None = None,
        input_data: str | None = None,
        output_data: str | None = None,
        expected_output: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        logger.debug("noop evaluate: evaluator_id=%s", evaluator_id)
        return {
            "results": [
                {
                    "score": 1.0,
                    "label": "PASS",
                    "reasoning": "No-op evaluation — always passes.",
                }
            ],
            "evaluator_id": evaluator_id,
        }

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
