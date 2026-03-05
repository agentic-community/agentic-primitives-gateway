from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import cedarpy

from agentic_primitives_gateway.enforcement.base import PolicyEnforcer
from agentic_primitives_gateway.primitives._sync import SyncRunnerMixin
from agentic_primitives_gateway.registry import registry

logger = logging.getLogger(__name__)


class CedarPolicyEnforcer(SyncRunnerMixin, PolicyEnforcer):
    """Local Cedar policy evaluation via ``cedarpy``.

    Reads policies from the configured ``PolicyProvider`` (via the registry)
    and evaluates authorization requests locally using the Rust-backed
    ``cedarpy.is_authorized()`` function.

    Config::

        enforcement:
          backend: "agentic_primitives_gateway.enforcement.cedar.CedarPolicyEnforcer"
          config:
            policy_refresh_interval: 30
            engine_id: "my-engine"   # optional — scope to a single engine

    Behavior:
    - Default-deny: if enforcement is active but no policies are loaded,
      all requests are denied.
    - On refresh failure, the existing policy set is kept.
    - An initial ``load_policies()`` is called during lifespan startup
      (before traffic), so there is no race condition.
    """

    def __init__(
        self,
        policy_refresh_interval: int = 30,
        engine_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._refresh_interval = policy_refresh_interval
        self._engine_id = engine_id
        self._policies: list[str] = []
        self._refresh_task: asyncio.Task[None] | None = None
        logger.info(
            "CedarPolicyEnforcer initialized (refresh=%ds, engine_id=%s)",
            policy_refresh_interval,
            engine_id or "all",
        )

    async def authorize(
        self,
        principal: str,
        action: str,
        resource: str,
        context: dict[str, Any] | None = None,
    ) -> bool:
        if not self._policies:
            return False

        policies = "\n".join(self._policies)
        entities = "[]"

        # Wrap action/resource in Cedar entity format if not already wrapped
        cedar_action = action if "::" in action else f'Action::"{action}"'
        cedar_resource = resource if "::" in resource else f'Resource::"{resource}"'

        request = {
            "principal": principal,
            "action": cedar_action,
            "resource": cedar_resource,
            "context": context or {},
        }

        decision = await self._run_sync(
            cedarpy.is_authorized,
            request,
            policies,
            entities,
        )
        allowed: bool = decision.decision == cedarpy.Decision.Allow  # type: ignore[union-attr]
        return allowed

    async def load_policies(self) -> None:
        """Fetch all policies from the registry's PolicyProvider."""
        try:
            policy_provider = registry.policy

            if self._engine_id:
                engine_ids = [self._engine_id]
            else:
                engines_result = await policy_provider.list_policy_engines()
                engine_ids = [e["policy_engine_id"] for e in engines_result.get("policy_engines", [])]

            new_policies: list[str] = []
            for eid in engine_ids:
                result = await policy_provider.list_policies(eid)
                for p in result.get("policies", []):
                    definition = p.get("definition", "")
                    if isinstance(definition, str) and definition.strip():
                        new_policies.append(definition)

            self._policies = new_policies
            logger.info("CedarPolicyEnforcer loaded %d policies", len(new_policies))

        except Exception:
            logger.exception("Failed to refresh Cedar policies; keeping existing set")

    def start_refresh(self) -> None:
        """Start the background refresh task."""
        if self._refresh_task is None:
            self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self._refresh_interval)
            await self.load_policies()

    async def close(self) -> None:
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._refresh_task
            self._refresh_task = None
        logger.info("CedarPolicyEnforcer closed")
