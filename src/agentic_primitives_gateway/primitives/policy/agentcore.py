from __future__ import annotations

import logging
import re
from typing import Any
from uuid import uuid4

from agentic_primitives_gateway.context import get_boto3_session
from agentic_primitives_gateway.primitives._sync import SyncRunnerMixin
from agentic_primitives_gateway.primitives.policy.base import PolicyProvider

logger = logging.getLogger(__name__)


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively convert camelCase dict keys to snake_case."""

    def _convert(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {re.sub(r"([A-Z])", r"_\1", k).lstrip("_").lower(): _convert(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_convert(item) for item in obj]
        return obj

    result: dict[str, Any] = _convert(data)
    return result


class AgentCorePolicyProvider(SyncRunnerMixin, PolicyProvider):
    """Policy provider backed by Amazon Bedrock AgentCore.

    Uses the ``bedrock-agentcore-control`` boto3 client for policy engine
    and policy management.

    Provider config example::

        backend: agentic_primitives_gateway.primitives.policy.agentcore.AgentCorePolicyProvider
        config:
          region: "us-east-1"
    """

    def __init__(self, region: str = "us-east-1", **kwargs: Any) -> None:
        self._region = region
        logger.info("AgentCorePolicyProvider initialized (region=%s)", region)

    def _get_client(self) -> Any:
        session = get_boto3_session(default_region=self._region)
        return session.client("bedrock-agentcore-control")

    # ── Policy engines ────────────────────────────────────────────────

    async def create_policy_engine(
        self,
        name: str,
        description: str = "",
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = self._get_client()
        params: dict[str, Any] = {"name": name}
        if description:
            params["description"] = description
        if config:
            params.update(config)
        result = await self._run_sync(client.create_policy_engine, **params)
        return _normalize(result)

    async def get_policy_engine(self, engine_id: str) -> dict[str, Any]:
        client = self._get_client()
        result = await self._run_sync(client.get_policy_engine, policyEngineId=engine_id)
        return _normalize(result)

    async def delete_policy_engine(self, engine_id: str) -> None:
        client = self._get_client()
        await self._run_sync(client.delete_policy_engine, policyEngineId=engine_id)

    async def list_policy_engines(
        self,
        max_results: int = 100,
        next_token: str | None = None,
    ) -> dict[str, Any]:
        client = self._get_client()
        params: dict[str, Any] = {"maxResults": max_results}
        if next_token is not None:
            params["nextToken"] = next_token
        result = await self._run_sync(client.list_policy_engines, **params)
        return _normalize(result)

    # ── Policies ──────────────────────────────────────────────────────

    async def create_policy(
        self,
        engine_id: str,
        policy_body: str,
        description: str = "",
    ) -> dict[str, Any]:
        client = self._get_client()
        params: dict[str, Any] = {
            "policyEngineId": engine_id,
            "name": f"policy_{uuid4().hex[:12]}",
            "definition": {"cedar": {"statement": policy_body}},
        }
        if description:
            params["description"] = description
        result = await self._run_sync(client.create_policy, **params)
        return _normalize(result)

    async def get_policy(
        self,
        engine_id: str,
        policy_id: str,
    ) -> dict[str, Any]:
        client = self._get_client()
        result = await self._run_sync(
            client.get_policy,
            policyEngineId=engine_id,
            policyId=policy_id,
        )
        return _normalize(result)

    async def update_policy(
        self,
        engine_id: str,
        policy_id: str,
        policy_body: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        client = self._get_client()
        params: dict[str, Any] = {
            "policyEngineId": engine_id,
            "policyId": policy_id,
            "definition": {"cedar": {"statement": policy_body}},
        }
        if description is not None:
            params["description"] = description
        result = await self._run_sync(client.update_policy, **params)
        return _normalize(result)

    async def delete_policy(
        self,
        engine_id: str,
        policy_id: str,
    ) -> None:
        client = self._get_client()
        await self._run_sync(
            client.delete_policy,
            policyEngineId=engine_id,
            policyId=policy_id,
        )

    async def list_policies(
        self,
        engine_id: str,
        max_results: int = 100,
        next_token: str | None = None,
    ) -> dict[str, Any]:
        client = self._get_client()
        params: dict[str, Any] = {"policyEngineId": engine_id, "maxResults": max_results}
        if next_token is not None:
            params["nextToken"] = next_token
        result = await self._run_sync(client.list_policies, **params)
        return _normalize(result)

    # ── Policy generation ─────────────────────────────────────────────

    async def start_policy_generation(
        self,
        engine_id: str,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = self._get_client()
        params: dict[str, Any] = {"policyEngineId": engine_id}
        if config:
            params.update(config)
        result = await self._run_sync(client.start_policy_generation, **params)
        return _normalize(result)

    async def get_policy_generation(
        self,
        engine_id: str,
        generation_id: str,
    ) -> dict[str, Any]:
        client = self._get_client()
        result = await self._run_sync(
            client.get_policy_generation,
            policyEngineId=engine_id,
            policyGenerationId=generation_id,
        )
        return _normalize(result)

    async def list_policy_generations(
        self,
        engine_id: str,
        max_results: int = 100,
        next_token: str | None = None,
    ) -> dict[str, Any]:
        client = self._get_client()
        params: dict[str, Any] = {"policyEngineId": engine_id, "maxResults": max_results}
        if next_token is not None:
            params["nextToken"] = next_token
        result = await self._run_sync(client.list_policy_generations, **params)
        return _normalize(result)

    async def list_policy_generation_assets(
        self,
        engine_id: str,
        generation_id: str,
        max_results: int = 100,
        next_token: str | None = None,
    ) -> dict[str, Any]:
        client = self._get_client()
        params: dict[str, Any] = {
            "policyEngineId": engine_id,
            "policyGenerationId": generation_id,
            "maxResults": max_results,
        }
        if next_token is not None:
            params["nextToken"] = next_token
        result = await self._run_sync(client.list_policy_generation_assets, **params)
        return _normalize(result)

    async def healthcheck(self) -> bool:
        try:
            client = self._get_client()
            await self._run_sync(client.list_policy_engines, maxResults=1)
            return True
        except Exception:
            logger.exception("AgentCore policy healthcheck failed")
            return False
