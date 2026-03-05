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
    async def test_load_policies_all_engines(self):
        """load_policies fetches from all engines when engine_id is not set."""
        enforcer = CedarPolicyEnforcer()

        mock_policy_provider = AsyncMock()
        mock_policy_provider.list_policy_engines.return_value = {
            "policy_engines": [
                {"policy_engine_id": "eng-1"},
                {"policy_engine_id": "eng-2"},
            ],
        }
        mock_policy_provider.list_policies.side_effect = [
            {"policies": [{"definition": "permit(principal, action, resource);"}]},
            {"policies": [{"definition": 'forbid(principal == Agent::"bad", action, resource);'}]},
        ]

        mock_registry = MagicMock()
        type(mock_registry).policy = property(lambda self: mock_policy_provider)

        with patch("agentic_primitives_gateway.enforcement.cedar.registry", mock_registry):
            await enforcer.load_policies()

        assert len(enforcer._policies) == 2
        assert "permit(principal, action, resource);" in enforcer._policies

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
