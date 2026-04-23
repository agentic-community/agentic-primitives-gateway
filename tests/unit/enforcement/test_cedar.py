from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_primitives_gateway.enforcement.cedar import CedarPolicyEnforcer


class TestCedarPolicyEnforcer:
    """Tests for the CedarPolicyEnforcer (local Cedar evaluation)."""

    def test_init_defaults(self):
        enforcer = CedarPolicyEnforcer()
        assert enforcer._refresh_interval == 30
        assert enforcer._engine_id is None
        assert enforcer._policies == []

    def test_init_custom_config(self):
        enforcer = CedarPolicyEnforcer(policy_refresh_interval=60, engine_id="eng-1")
        assert enforcer._refresh_interval == 60
        assert enforcer._engine_id == "eng-1"

    @pytest.mark.asyncio
    async def test_authorize_no_policies_denies(self):
        """Default-deny: no policies loaded means all requests denied."""
        enforcer = CedarPolicyEnforcer()
        result = await enforcer.authorize(
            principal='Agent::"test"',
            action="memory:store",
            resource="memory/ns1",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_authorize_with_permit_all_policy(self):
        """A wildcard permit policy allows all requests."""
        enforcer = CedarPolicyEnforcer()
        enforcer._policies = ["permit(principal, action, resource);"]

        result = await enforcer.authorize(
            principal='Agent::"test"',
            action='Action::"memory:store"',
            resource='Resource::"memory/ns1"',
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_authorize_deny_when_no_matching_permit(self):
        """Requests that don't match any permit are denied."""
        enforcer = CedarPolicyEnforcer()
        enforcer._policies = ['permit(principal == Agent::"other", action, resource);']

        result = await enforcer.authorize(
            principal='Agent::"test"',
            action='Action::"memory:store"',
            resource='Resource::"memory/ns1"',
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_authorize_principal_scoped_permit(self):
        """A principal-scoped permit only allows that principal."""
        enforcer = CedarPolicyEnforcer()
        enforcer._policies = ['permit(principal == Agent::"allowed-agent", action, resource);']

        # Allowed principal
        result = await enforcer.authorize(
            principal='Agent::"allowed-agent"',
            action='Action::"memory:store"',
            resource='Resource::"memory/ns1"',
        )
        assert result is True

        # Denied principal
        result = await enforcer.authorize(
            principal='Agent::"other-agent"',
            action='Action::"memory:store"',
            resource='Resource::"memory/ns1"',
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_authorize_passes_context(self):
        """Context dict is forwarded to cedarpy."""
        enforcer = CedarPolicyEnforcer()
        enforcer._policies = ["permit(principal, action, resource);"]

        result = await enforcer.authorize(
            principal='Agent::"test"',
            action='Action::"memory:store"',
            resource='Resource::"memory/ns1"',
            context={"ip": "10.0.0.1"},
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_load_policies_skips_without_engine_id(self):
        """load_policies is a no-op when engine_id has not been set yet."""
        enforcer = CedarPolicyEnforcer()
        await enforcer.load_policies()
        assert len(enforcer._policies) == 0

    @pytest.mark.asyncio
    async def test_load_policies_from_scoped_engine(self):
        """load_policies fetches only from the enforcer's scoped engine."""
        enforcer = CedarPolicyEnforcer(engine_id="eng-1")

        mock_policy_provider = AsyncMock()
        mock_policy_provider.list_policies.return_value = {
            "policies": [
                {"definition": "permit(principal, action, resource);"},
                {"definition": 'forbid(principal == Agent::"bad", action, resource);'},
            ],
        }

        mock_registry = MagicMock()
        type(mock_registry).policy = property(lambda self: mock_policy_provider)

        with patch("agentic_primitives_gateway.enforcement.cedar.registry", mock_registry):
            await enforcer.load_policies()

        assert len(enforcer._policies) == 2
        assert "permit(principal, action, resource);" in enforcer._policies
        mock_policy_provider.list_policies.assert_called_once_with("eng-1")

    @pytest.mark.asyncio
    async def test_load_policies_single_engine(self):
        """load_policies fetches from a specific engine when engine_id is set."""
        enforcer = CedarPolicyEnforcer(engine_id="eng-1")

        mock_policy_provider = AsyncMock()
        mock_policy_provider.list_policies.return_value = {
            "policies": [{"definition": "permit(principal, action, resource);"}],
        }

        mock_registry = MagicMock()
        type(mock_registry).policy = property(lambda self: mock_policy_provider)

        with patch("agentic_primitives_gateway.enforcement.cedar.registry", mock_registry):
            await enforcer.load_policies()

        assert len(enforcer._policies) == 1
        mock_policy_provider.list_policy_engines.assert_not_called()

    @pytest.mark.asyncio
    async def test_load_policies_skips_empty_definitions(self):
        """Empty/missing definitions are skipped."""
        enforcer = CedarPolicyEnforcer(engine_id="eng-1")

        mock_policy_provider = AsyncMock()
        mock_policy_provider.list_policies.return_value = {
            "policies": [
                {"definition": "permit(principal, action, resource);"},
                {"definition": ""},
                {"definition": "   "},
                {},
            ],
        }

        mock_registry = MagicMock()
        type(mock_registry).policy = property(lambda self: mock_policy_provider)

        with patch("agentic_primitives_gateway.enforcement.cedar.registry", mock_registry):
            await enforcer.load_policies()

        assert len(enforcer._policies) == 1

    @pytest.mark.asyncio
    async def test_load_policies_failure_keeps_existing(self):
        """On failure, the existing policy set is preserved."""
        enforcer = CedarPolicyEnforcer(engine_id="eng-1")
        enforcer._policies = ["existing_policy;"]

        mock_policy_provider = AsyncMock()
        mock_policy_provider.list_policies.side_effect = RuntimeError("connection failed")

        mock_registry = MagicMock()
        type(mock_registry).policy = property(lambda self: mock_policy_provider)

        with patch("agentic_primitives_gateway.enforcement.cedar.registry", mock_registry):
            await enforcer.load_policies()

        assert enforcer._policies == ["existing_policy;"]

    @pytest.mark.asyncio
    async def test_close_cancels_refresh_task(self):
        """close() cancels the background refresh task."""
        enforcer = CedarPolicyEnforcer()

        # Start a real refresh task, then close it
        enforcer.start_refresh()
        assert enforcer._refresh_task is not None

        await enforcer.close()
        assert enforcer._refresh_task is None

    @pytest.mark.asyncio
    async def test_close_without_refresh_task(self):
        """close() is safe to call when no refresh task is running."""
        enforcer = CedarPolicyEnforcer()
        await enforcer.close()
        # Should not raise

    @pytest.mark.asyncio
    async def test_start_refresh_creates_task(self):
        """start_refresh creates an asyncio task."""
        enforcer = CedarPolicyEnforcer()

        enforcer.start_refresh()
        assert enforcer._refresh_task is not None
        enforcer._refresh_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await enforcer._refresh_task

    def test_isinstance_policy_enforcer(self):
        from agentic_primitives_gateway.enforcement.base import PolicyEnforcer

        enforcer = CedarPolicyEnforcer()
        assert isinstance(enforcer, PolicyEnforcer)


class TestEnsureEngineAudit:
    """Every exit path of ensure_engine() must leave a durable audit record.

    Engine provisioning is a security-posture transition; SIEMs need to
    distinguish configured vs. reused vs. created (so a surprise new
    engine is visible) and must see a failure event before the process
    dies on provisioning error.
    """

    @pytest.fixture
    def captured_events(self):
        events: list[dict] = []

        def _capture(**kwargs):
            events.append(kwargs)

        with patch(
            "agentic_primitives_gateway.enforcement.cedar.emit_audit_event",
            side_effect=_capture,
        ):
            yield events

    @pytest.mark.asyncio
    async def test_configured_engine_id_emits_engine_id_configured(self, captured_events):
        """engine_id supplied via config → emit records that an ID was
        configured, NOT that the engine is confirmed reachable. No
        describe call is made, so the emit deliberately does not claim
        engine_ready, and engine_name is omitted (the configured engine
        has a name we don't know without a network call).
        """
        enforcer = CedarPolicyEnforcer(engine_id="preset-engine")

        # registry.policy should never be touched on this path
        mock_registry = MagicMock()
        with patch("agentic_primitives_gateway.enforcement.cedar.registry", mock_registry):
            result = await enforcer.ensure_engine()

        assert result == "preset-engine"
        assert len(captured_events) == 1
        emitted = captured_events[0]
        assert emitted["outcome"].value == "success"
        assert emitted["reason"] == "engine_id_configured"
        assert emitted["resource_id"] == "preset-engine"
        assert emitted["metadata"]["source"] == "configured"
        # engine_name is deliberately absent — we don't know the configured
        # engine's name and must not fabricate one
        assert "engine_name" not in emitted["metadata"]

    @pytest.mark.asyncio
    async def test_existing_engine_emits_ready_with_source_reused(self, captured_events):
        """An existing engine with the auto name is reused → source=reused."""
        enforcer = CedarPolicyEnforcer()

        mock_policy_provider = AsyncMock()
        mock_policy_provider.list_policy_engines.return_value = {
            "policy_engines": [
                {"name": "some-other-engine", "policy_engine_id": "other-id"},
                {
                    "name": CedarPolicyEnforcer.AUTO_ENGINE_NAME,
                    "policy_engine_id": "existing-id",
                },
            ],
        }

        mock_registry = MagicMock()
        type(mock_registry).policy = property(lambda self: mock_policy_provider)

        with patch("agentic_primitives_gateway.enforcement.cedar.registry", mock_registry):
            result = await enforcer.ensure_engine()

        assert result == "existing-id"
        mock_policy_provider.create_policy_engine.assert_not_called()
        assert len(captured_events) == 1
        emitted = captured_events[0]
        assert emitted["outcome"].value == "success"
        assert emitted["reason"] == "engine_ready"
        assert emitted["resource_id"] == "existing-id"
        assert emitted["metadata"]["source"] == "reused"

    @pytest.mark.asyncio
    async def test_created_engine_emits_ready_with_source_created(self, captured_events):
        """No existing engine → create, emit source=created."""
        enforcer = CedarPolicyEnforcer()

        mock_policy_provider = AsyncMock()
        mock_policy_provider.list_policy_engines.return_value = {"policy_engines": []}
        mock_policy_provider.create_policy_engine.return_value = {
            "policy_engine_id": "new-id",
        }

        mock_registry = MagicMock()
        type(mock_registry).policy = property(lambda self: mock_policy_provider)

        with patch("agentic_primitives_gateway.enforcement.cedar.registry", mock_registry):
            result = await enforcer.ensure_engine()

        assert result == "new-id"
        assert len(captured_events) == 1
        emitted = captured_events[0]
        assert emitted["outcome"].value == "success"
        assert emitted["reason"] == "engine_ready"
        assert emitted["resource_id"] == "new-id"
        assert emitted["metadata"]["source"] == "created"

    @pytest.mark.asyncio
    async def test_create_failure_emits_failure_and_raises(self, captured_events):
        """create_policy_engine raising → one failure emit, then re-raise."""
        enforcer = CedarPolicyEnforcer()

        mock_policy_provider = AsyncMock()
        mock_policy_provider.list_policy_engines.return_value = {"policy_engines": []}
        mock_policy_provider.create_policy_engine.side_effect = RuntimeError("credentials expired")

        mock_registry = MagicMock()
        type(mock_registry).policy = property(lambda self: mock_policy_provider)

        with (
            patch("agentic_primitives_gateway.enforcement.cedar.registry", mock_registry),
            pytest.raises(RuntimeError, match="credentials expired"),
        ):
            await enforcer.ensure_engine()

        assert len(captured_events) == 1
        emitted = captured_events[0]
        assert emitted["outcome"].value == "failure"
        assert emitted["reason"] == "engine_provision_failed"
        assert emitted["metadata"]["error_type"] == "RuntimeError"
        assert emitted["metadata"]["engine_name"] == CedarPolicyEnforcer.AUTO_ENGINE_NAME
        # No resource_id since no engine was ever created
        assert emitted.get("resource_id") is None

    @pytest.mark.asyncio
    async def test_list_failure_propagates_does_not_fallback_to_create(self, captured_events):
        """list_policy_engines raising must propagate — not fall through
        to create_policy_engine. A silent fallback could duplicate engines
        when the list failure is transient and create then succeeds, and
        it misattributes the failure audit record to a downstream error.

        The failure emit must carry the LIST error_type, not whatever
        would have come from create, and create must never be called.
        """

        class ListSpecificError(Exception):
            pass

        enforcer = CedarPolicyEnforcer()

        mock_policy_provider = AsyncMock()
        mock_policy_provider.list_policy_engines.side_effect = ListSpecificError("list failed")
        # create is configured so that, if it were mistakenly called, the
        # test would see a different error_type in the emit — making the
        # failure loud.
        mock_policy_provider.create_policy_engine.side_effect = RuntimeError("create should never run")

        mock_registry = MagicMock()
        type(mock_registry).policy = property(lambda self: mock_policy_provider)

        with (
            patch("agentic_primitives_gateway.enforcement.cedar.registry", mock_registry),
            pytest.raises(ListSpecificError, match="list failed"),
        ):
            await enforcer.ensure_engine()

        mock_policy_provider.create_policy_engine.assert_not_called()
        assert len(captured_events) == 1
        emitted = captured_events[0]
        assert emitted["outcome"].value == "failure"
        assert emitted["reason"] == "engine_list_failed"
        assert emitted["metadata"]["error_type"] == "ListSpecificError"
        assert emitted.get("resource_id") is None
