from __future__ import annotations

import logging
import re
from typing import Any

from agentic_primitives_gateway.context import get_boto3_session
from agentic_primitives_gateway.primitives._sync import SyncRunnerMixin
from agentic_primitives_gateway.primitives.evaluations.base import EvaluationsProvider

logger = logging.getLogger(__name__)


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively convert camelCase dict keys to snake_case."""

    def _convert(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {re.sub(r"([A-Z])", r"_\1", k).lower().lstrip("_"): _convert(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_convert(item) for item in obj]
        return obj

    result: dict[str, Any] = _convert(data)
    return result


class AgentCoreEvaluationsProvider(SyncRunnerMixin, EvaluationsProvider):
    """Evaluations provider backed by AWS Bedrock AgentCore.

    Uses two boto3 clients:
    - ``bedrock-agentcore-control`` for evaluator CRUD and online eval configs
    - ``bedrock-agentcore`` for running evaluations

    Provider config example::

        backend: agentic_primitives_gateway.primitives.evaluations.agentcore.AgentCoreEvaluationsProvider
        config:
          region: "us-east-1"
    """

    def __init__(self, region: str = "us-east-1", **kwargs: Any) -> None:
        self._region = region
        logger.info("AgentCore evaluations provider initialized (region=%s)", region)

    def _get_control_client(self) -> Any:
        """Get the bedrock-agentcore-control boto3 client for CRUD operations."""
        session = get_boto3_session(default_region=self._region)
        return session.client(
            "bedrock-agentcore-control",
            region_name=session.region_name,
        )

    def _get_data_client(self) -> Any:
        """Get the bedrock-agentcore boto3 client for evaluate operations."""
        session = get_boto3_session(default_region=self._region)
        return session.client(
            "bedrock-agentcore",
            region_name=session.region_name,
        )

    # ── Evaluator CRUD ─────────────────────────────────────────────────

    async def create_evaluator(
        self,
        name: str,
        evaluator_type: str,
        config: dict[str, Any] | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        client = self._get_control_client()
        params: dict[str, Any] = {"evaluatorName": name}
        if description:
            params["description"] = description
        # evaluator_type maps to level (TOOL_CALL, TRACE, SESSION)
        # config should contain evaluatorConfig and optionally level
        if config:
            if "evaluatorConfig" in config:
                params["evaluatorConfig"] = config["evaluatorConfig"]
            if "level" in config:
                params["level"] = config["level"]
            # Pass through any other config keys
            for k, v in config.items():
                if k not in ("evaluatorConfig", "level"):
                    params[k] = v
        # Default level from evaluator_type if not in config
        if "level" not in params:
            params["level"] = evaluator_type if evaluator_type in ("TOOL_CALL", "TRACE", "SESSION") else "TRACE"
        # Default evaluatorConfig if not provided
        if "evaluatorConfig" not in params:
            params["evaluatorConfig"] = {"llmAsAJudge": {}}
        result = await self._run_sync(client.create_evaluator, **params)
        return _normalize(result)

    async def get_evaluator(self, evaluator_id: str) -> dict[str, Any]:
        client = self._get_control_client()
        result = await self._run_sync(client.get_evaluator, evaluatorId=evaluator_id)
        return _normalize(result)

    async def update_evaluator(
        self,
        evaluator_id: str,
        config: dict[str, Any] | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        client = self._get_control_client()
        params: dict[str, Any] = {"evaluatorId": evaluator_id}
        if config is not None:
            if "evaluatorConfig" in config:
                params["evaluatorConfig"] = config["evaluatorConfig"]
            if "level" in config:
                params["level"] = config["level"]
        if description is not None:
            params["description"] = description
        result = await self._run_sync(client.update_evaluator, **params)
        return _normalize(result)

    async def delete_evaluator(self, evaluator_id: str) -> None:
        client = self._get_control_client()
        await self._run_sync(client.delete_evaluator, evaluatorId=evaluator_id)

    async def list_evaluators(
        self,
        max_results: int = 100,
        next_token: str | None = None,
    ) -> dict[str, Any]:
        client = self._get_control_client()
        params: dict[str, Any] = {"maxResults": max_results}
        if next_token is not None:
            params["nextToken"] = next_token
        result = await self._run_sync(client.list_evaluators, **params)
        return _normalize(result)

    # ── Evaluate ───────────────────────────────────────────────────────

    async def evaluate(
        self,
        evaluator_id: str,
        target: str | None = None,
        input_data: str | None = None,
        output_data: str | None = None,
        expected_output: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = self._get_data_client()
        params: dict[str, Any] = {"evaluatorId": evaluator_id}
        # Build evaluationInput from input_data (pass through as sessionSpans)
        if input_data is not None:
            params["evaluationInput"] = {"sessionSpans": [input_data]}
        elif metadata and "evaluationInput" in metadata:
            params["evaluationInput"] = metadata["evaluationInput"]
        # Build evaluationTarget from target/output_data
        if target is not None or output_data is not None:
            eval_target: dict[str, Any] = {}
            if target is not None:
                eval_target["traceIds"] = [target]
            if output_data is not None:
                eval_target["spanIds"] = [output_data]
            params["evaluationTarget"] = eval_target
        elif metadata and "evaluationTarget" in metadata:
            params["evaluationTarget"] = metadata["evaluationTarget"]
        result = await self._run_sync(client.evaluate, **params)
        return _normalize(result)

    # ── Online evaluation configs ──────────────────────────────────────

    async def create_online_evaluation_config(
        self,
        name: str,
        evaluator_ids: list[str],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = self._get_control_client()
        params: dict[str, Any] = {
            "onlineEvaluationConfigName": name,
            "evaluators": [{"evaluatorId": eid} for eid in evaluator_ids],
        }
        if config:
            params.update(config)
        result = await self._run_sync(client.create_online_evaluation_config, **params)
        return _normalize(result)

    async def get_online_evaluation_config(
        self,
        config_id: str,
    ) -> dict[str, Any]:
        client = self._get_control_client()
        result = await self._run_sync(client.get_online_evaluation_config, onlineEvaluationConfigId=config_id)
        return _normalize(result)

    async def delete_online_evaluation_config(
        self,
        config_id: str,
    ) -> None:
        client = self._get_control_client()
        await self._run_sync(client.delete_online_evaluation_config, onlineEvaluationConfigId=config_id)

    async def list_online_evaluation_configs(
        self,
        max_results: int = 100,
        next_token: str | None = None,
    ) -> dict[str, Any]:
        client = self._get_control_client()
        params: dict[str, Any] = {"maxResults": max_results}
        if next_token is not None:
            params["nextToken"] = next_token
        result = await self._run_sync(client.list_online_evaluation_configs, **params)
        return _normalize(result)

    async def healthcheck(self) -> bool:
        return True
