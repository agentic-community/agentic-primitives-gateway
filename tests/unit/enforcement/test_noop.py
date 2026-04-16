from __future__ import annotations

import pytest

from agentic_primitives_gateway.enforcement.noop import NoopPolicyEnforcer


class TestNoopPolicyEnforcer:
    """Tests for the NoopPolicyEnforcer (default allow-all)."""

    @pytest.mark.asyncio
    async def test_authorize_always_true(self):
        enforcer = NoopPolicyEnforcer()
        result = await enforcer.authorize(
            principal='Agent::"test"',
            action="memory:store",
            resource="memory/ns1",
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_authorize_with_context(self):
        enforcer = NoopPolicyEnforcer()
        result = await enforcer.authorize(
            principal='Agent::"test"',
            action="llm:completions",
            resource="llm/completions",
            context={"ip": "127.0.0.1"},
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_authorize_anonymous(self):
        enforcer = NoopPolicyEnforcer()
        result = await enforcer.authorize(
            principal='Agent::"anonymous"',
            action="tools:invoke",
            resource="tools/my-tool/invoke",
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_load_policies_noop(self):
        enforcer = NoopPolicyEnforcer()
        await enforcer.load_policies()
        # Should not raise

    @pytest.mark.asyncio
    async def test_close_noop(self):
        enforcer = NoopPolicyEnforcer()
        await enforcer.close()
        # Should not raise

    def test_isinstance_policy_enforcer(self):
        from agentic_primitives_gateway.enforcement.base import PolicyEnforcer

        enforcer = NoopPolicyEnforcer()
        assert isinstance(enforcer, PolicyEnforcer)

    def test_init_accepts_kwargs(self):
        enforcer = NoopPolicyEnforcer(some_param="value")
        assert isinstance(enforcer, NoopPolicyEnforcer)
