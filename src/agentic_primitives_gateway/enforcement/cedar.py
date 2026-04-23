from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
from typing import Any

import cedarpy

from agentic_primitives_gateway.audit.emit import emit_audit_event
from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType
from agentic_primitives_gateway.enforcement.base import PolicyEnforcer
from agentic_primitives_gateway.primitives._sync import SyncRunnerMixin
from agentic_primitives_gateway.registry import registry

logger = logging.getLogger(__name__)


class CedarPolicyEnforcer(SyncRunnerMixin, PolicyEnforcer):
    """Local Cedar policy evaluation via ``cedarpy``.

    Reads policies from a **single** policy engine (via the registry's
    ``PolicyProvider``) and evaluates authorization requests locally using
    the Rust-backed ``cedarpy.is_authorized()`` function.

    Engine provisioning:
    - If ``engine_id`` is provided in config, that engine is used directly.
    - If omitted, a gateway-managed engine named ``gateway_enforcement``
      is auto-provisioned at startup via :meth:`ensure_engine`. This
      isolates the gateway's policies from other engines in the provider
      (e.g., engines created by other services or test runs).

    Config::

        enforcement:
          backend: "agentic_primitives_gateway.enforcement.cedar.CedarPolicyEnforcer"
          config:
            policy_refresh_interval: 30
            engine_id: "my-engine"   # optional — auto-provisioned if omitted

    Behavior:
    - Default-deny: if enforcement is active but no policies are loaded,
      all requests are denied.
    - On refresh failure, the existing policy set is kept.
    - An initial ``load_policies()`` is called during lifespan startup
      (before traffic), so there is no race condition.
    """

    AUTO_ENGINE_NAME = "gateway_enforcement"

    def __init__(
        self,
        policy_refresh_interval: int = 30,
        engine_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._refresh_interval = policy_refresh_interval
        self._engine_id = engine_id
        self._policies: list[str] = []
        self._policy_fingerprint: str | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        logger.info(
            "CedarPolicyEnforcer initialized (refresh=%ds, engine_id=%s)",
            policy_refresh_interval,
            engine_id or "auto-provision",
        )

    @property
    def engine_id(self) -> str | None:
        """The policy engine ID this enforcer reads from."""
        return self._engine_id

    async def ensure_engine(self) -> str:
        """Ensure a policy engine exists for this enforcer.

        If ``engine_id`` was provided in config, returns it as-is.
        Otherwise, looks for an existing engine named ``gateway_enforcement``
        or creates one. Stores the resolved ID for future use.

        Returns:
            The policy engine ID.
        """
        if self._engine_id:
            emit_audit_event(
                action=AuditAction.POLICY_LOAD,
                outcome=AuditOutcome.SUCCESS,
                resource_type=ResourceType.POLICY_ENGINE,
                resource_id=self._engine_id,
                reason="engine_id_configured",
                metadata={"source": "configured"},
            )
            return self._engine_id

        policy_provider = registry.policy

        try:
            engines_result = await policy_provider.list_policy_engines()
        except Exception as e:
            self._emit_engine_provision_failure("engine_list_failed", e)
            logger.exception("Failed to list existing enforcement engines")
            raise

        for eng in engines_result.get("policy_engines", []):
            if eng.get("name") == self.AUTO_ENGINE_NAME:
                eid: str = eng["policy_engine_id"]
                self._engine_id = eid
                logger.info(
                    "Found existing enforcement engine: %s (%s)",
                    self.AUTO_ENGINE_NAME,
                    eid,
                )
                self._emit_engine_ready(eid, source="reused")
                return eid

        try:
            result = await policy_provider.create_policy_engine(
                name=self.AUTO_ENGINE_NAME,
                description="Auto-provisioned by the gateway for Cedar enforcement",
            )
            eid = str(result["policy_engine_id"])
            self._engine_id = eid
            logger.info(
                "Created enforcement engine: %s (%s)",
                self.AUTO_ENGINE_NAME,
                eid,
            )
            self._emit_engine_ready(eid, source="created")
            return eid
        except Exception as e:
            self._emit_engine_provision_failure("engine_provision_failed", e)
            logger.exception("Failed to auto-provision enforcement engine")
            raise

    def _emit_engine_ready(self, engine_id: str, *, source: str) -> None:
        """Emit a durable record that enforcement is wired to an engine
        whose name the gateway controls (auto-provisioned, either reused
        or just created).

        The configured path does NOT go through this helper — we don't
        know that engine's name without a describe call, and we don't
        claim the engine is "ready" (see ensure_engine for that rationale).
        """
        emit_audit_event(
            action=AuditAction.POLICY_LOAD,
            outcome=AuditOutcome.SUCCESS,
            resource_type=ResourceType.POLICY_ENGINE,
            resource_id=engine_id,
            reason="engine_ready",
            metadata={
                "engine_name": self.AUTO_ENGINE_NAME,
                "source": source,
            },
        )

    def _emit_engine_provision_failure(self, reason: str, exc: BaseException) -> None:
        """Emit a durable failure record before a provisioning exception
        propagates.

        - Only the error_type class name is recorded — exception str() from
          boto3 can include endpoint URLs, partial ARNs, or env var names
          that hint at credential configuration.
        - ``resource_id`` is deliberately absent (unlike ``load_policies``
          failure emits which carry ``self._engine_id``): at this point no
          engine has been confirmed to exist, so there's no honest ID to
          record. Refusing to invent one keeps the emit truthful.
        - ``engine_name`` is the engine we were *trying* to provision, not
          an engine that's been confirmed to exist. On ``engine_list_failed``
          this means "we failed while looking for this name" rather than
          "this engine errored."
        """
        emit_audit_event(
            action=AuditAction.POLICY_LOAD,
            outcome=AuditOutcome.FAILURE,
            resource_type=ResourceType.POLICY_ENGINE,
            reason=reason,
            metadata={
                "engine_name": self.AUTO_ENGINE_NAME,
                "error_type": type(exc).__name__,
            },
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
        cedar_resource = resource if "::" in resource else f'AgentCore::Gateway::"{resource}"'

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
        """Fetch policies from the enforcer's engine."""
        try:
            if not self._engine_id:
                logger.debug("No engine_id set yet; skipping policy load")
                return

            policy_provider = registry.policy
            result = await policy_provider.list_policies(self._engine_id)
            new_policies: list[str] = []
            for p in result.get("policies", []):
                definition = p.get("definition", "")
                if isinstance(definition, str) and definition.strip():
                    new_policies.append(definition)

            # Only emit the audit event when the policy set *actually changed*;
            # the background refresh runs every 30s by default and re-emitting
            # on every tick would drown compliance dashboards.
            fingerprint = hashlib.sha256("\n\n".join(sorted(new_policies)).encode("utf-8")).hexdigest()
            if fingerprint != self._policy_fingerprint:
                emit_audit_event(
                    action=AuditAction.POLICY_LOAD,
                    outcome=AuditOutcome.SUCCESS,
                    resource_type=ResourceType.POLICY_ENGINE,
                    resource_id=self._engine_id,
                    metadata={
                        "policy_count": len(new_policies),
                        "previous_count": len(self._policies),
                    },
                )
                self._policy_fingerprint = fingerprint

            self._policies = new_policies
            logger.info(
                "CedarPolicyEnforcer loaded %d policies from engine %s",
                len(new_policies),
                self._engine_id,
            )

        except Exception as e:
            emit_audit_event(
                action=AuditAction.POLICY_LOAD,
                outcome=AuditOutcome.FAILURE,
                resource_type=ResourceType.POLICY_ENGINE,
                resource_id=self._engine_id,
                metadata={"error_type": type(e).__name__},
            )
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
