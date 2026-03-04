from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from agentic_primitives_gateway.primitives.policy.base import PolicyProvider

logger = logging.getLogger(__name__)


class NoopPolicyProvider(PolicyProvider):
    """No-op policy provider that tracks engines and policies in memory."""

    def __init__(self, **kwargs: Any) -> None:
        self._engines: dict[str, dict[str, Any]] = {}
        self._policies: dict[tuple[str, str], dict[str, Any]] = {}
        logger.info("NoopPolicyProvider initialized")

    # ── Policy engines ────────────────────────────────────────────────

    async def create_policy_engine(
        self,
        name: str,
        description: str = "",
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        engine_id = uuid4().hex
        now = datetime.now(UTC).isoformat()
        engine = {
            "policy_engine_id": engine_id,
            "name": name,
            "description": description,
            "status": "ACTIVE",
            "created_at": now,
        }
        self._engines[engine_id] = engine
        logger.debug("noop create_policy_engine: %s", engine_id)
        return dict(engine)

    async def get_policy_engine(self, engine_id: str) -> dict[str, Any]:
        engine = self._engines.get(engine_id)
        if not engine:
            raise KeyError(f"Engine {engine_id} not found")
        return dict(engine)

    async def delete_policy_engine(self, engine_id: str) -> None:
        self._engines.pop(engine_id, None)
        # Also remove associated policies
        keys_to_remove = [k for k in self._policies if k[0] == engine_id]
        for key in keys_to_remove:
            del self._policies[key]
        logger.debug("noop delete_policy_engine: %s", engine_id)

    async def list_policy_engines(
        self,
        max_results: int = 100,
        next_token: str | None = None,
    ) -> dict[str, Any]:
        engines = list(self._engines.values())[:max_results]
        return {"policy_engines": engines, "next_token": None}

    # ── Policies ──────────────────────────────────────────────────────

    async def create_policy(
        self,
        engine_id: str,
        policy_body: str,
        description: str = "",
    ) -> dict[str, Any]:
        policy_id = uuid4().hex
        now = datetime.now(UTC).isoformat()
        policy = {
            "policy_id": policy_id,
            "policy_engine_id": engine_id,
            "definition": policy_body,
            "description": description,
            "created_at": now,
        }
        self._policies[(engine_id, policy_id)] = policy
        logger.debug("noop create_policy: %s/%s", engine_id, policy_id)
        return dict(policy)

    async def get_policy(
        self,
        engine_id: str,
        policy_id: str,
    ) -> dict[str, Any]:
        policy = self._policies.get((engine_id, policy_id))
        if not policy:
            raise KeyError(f"Policy {policy_id} not found in engine {engine_id}")
        return dict(policy)

    async def update_policy(
        self,
        engine_id: str,
        policy_id: str,
        policy_body: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        policy = self._policies.get((engine_id, policy_id))
        if not policy:
            raise KeyError(f"Policy {policy_id} not found in engine {engine_id}")
        policy["definition"] = policy_body
        if description is not None:
            policy["description"] = description
        return dict(policy)

    async def delete_policy(
        self,
        engine_id: str,
        policy_id: str,
    ) -> None:
        self._policies.pop((engine_id, policy_id), None)
        logger.debug("noop delete_policy: %s/%s", engine_id, policy_id)

    async def list_policies(
        self,
        engine_id: str,
        max_results: int = 100,
        next_token: str | None = None,
    ) -> dict[str, Any]:
        policies = [v for k, v in self._policies.items() if k[0] == engine_id][:max_results]
        return {"policies": policies, "next_token": None}
