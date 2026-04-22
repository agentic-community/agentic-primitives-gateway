"""Intent-level test: Cedar forbid rules override permit rules.

Contract (Cedar spec): when multiple policies match a request, a
single ``forbid`` is sufficient to deny; ``forbid`` takes precedence
over any number of ``permit`` rules.  This is how Cedar's guarantee
"any forbid denies" works — and it's the basis for writing
least-privilege policies where broad permits are scoped by narrow
forbids.

``test_cedar.py`` covers:
- default-deny with no policies loaded
- permit-all allows
- principal-scoped permit denies non-matching principals

Nothing tests the **forbid-beats-permit** interaction.  A regression
where only permits were evaluated (ignoring forbids), or where the
last-matching-rule won, would silently break this contract and
allow actions the policy author intended to block.

Also covers resource-scoped permit rules — a separate gap from the
audit.
"""

from __future__ import annotations

import pytest

from agentic_primitives_gateway.enforcement.cedar import CedarPolicyEnforcer


@pytest.fixture
def enforcer() -> CedarPolicyEnforcer:
    return CedarPolicyEnforcer()


class TestForbidPrecedence:
    @pytest.mark.asyncio
    async def test_forbid_overrides_permit_all(self, enforcer: CedarPolicyEnforcer):
        """A blanket ``permit(principal, action, resource)`` plus a
        narrow ``forbid`` targeting alice → alice is denied, bob is
        allowed.  The forbid must win even though a permit matches.
        """
        enforcer._policies = [
            "permit(principal, action, resource);",
            'forbid(principal == User::"alice", action, resource);',
        ]
        alice_allowed = await enforcer.authorize(
            principal='User::"alice"',
            action="read",
            resource="doc-1",
        )
        bob_allowed = await enforcer.authorize(
            principal='User::"bob"',
            action="read",
            resource="doc-1",
        )
        assert alice_allowed is False, (
            "forbid(alice) should override permit(all).  If this passes, "
            "the enforcer is ignoring forbid rules entirely."
        )
        assert bob_allowed is True, (
            "bob has no matching forbid, so permit(all) should allow.  "
            "If this is False, the permit is being ignored even without a conflict."
        )

    @pytest.mark.asyncio
    async def test_forbid_without_matching_permit_still_denies(self, enforcer: CedarPolicyEnforcer):
        """Even if no permit matches, a forbid is irrelevant — default-
        deny applies.  But a forbid PLUS no permit must also produce
        deny (i.e., the forbid doesn't accidentally invert into a
        permit).  This guards a hypothetical regression where ``forbid``
        was mis-parsed as the opposite of permit.
        """
        enforcer._policies = ['forbid(principal == User::"alice", action, resource);']
        allowed = await enforcer.authorize(
            principal='User::"alice"',
            action="read",
            resource="doc-1",
        )
        assert allowed is False

    @pytest.mark.asyncio
    async def test_multiple_permits_one_forbid_still_denies(self, enforcer: CedarPolicyEnforcer):
        """Five permits + one forbid targeting alice → alice still
        denied.  "Any forbid denies" must apply regardless of permit
        count.
        """
        enforcer._policies = [
            "permit(principal, action, resource);",
            'permit(principal == User::"alice", action == Action::"read", resource);',
            'permit(principal == User::"alice", action, resource);',
            'permit(principal, action == Action::"read", resource);',
            "permit(principal, action, resource);",  # duplicate permit-all
            'forbid(principal == User::"alice", action, resource);',
        ]
        allowed = await enforcer.authorize(
            principal='User::"alice"',
            action="read",
            resource="doc-1",
        )
        assert allowed is False


class TestResourceScopedPermit:
    """A resource-scoped permit grants access to exactly that resource
    — a different resource with the same principal/action combination
    must be denied.
    """

    @pytest.mark.asyncio
    async def test_resource_scoped_permit_grants_specific_resource(self, enforcer: CedarPolicyEnforcer):
        enforcer._policies = [
            'permit(principal == User::"alice", action == Action::"read", '
            'resource == AgentCore::Gateway::"doc-alice");',
        ]
        allowed = await enforcer.authorize(
            principal='User::"alice"',
            action="read",
            resource="doc-alice",
        )
        assert allowed is True

    @pytest.mark.asyncio
    async def test_resource_scoped_permit_denies_other_resource(self, enforcer: CedarPolicyEnforcer):
        enforcer._policies = [
            'permit(principal == User::"alice", action == Action::"read", '
            'resource == AgentCore::Gateway::"doc-alice");',
        ]
        # Same principal + action, different resource → deny.
        allowed = await enforcer.authorize(
            principal='User::"alice"',
            action="read",
            resource="doc-bob",
        )
        assert allowed is False

    @pytest.mark.asyncio
    async def test_alice_cannot_access_bob_resource(self, enforcer: CedarPolicyEnforcer):
        """Classic multi-tenant test: policy grants each user access
        to their own resource; cross-user access is denied.
        """
        enforcer._policies = [
            'permit(principal == User::"alice", action, resource == AgentCore::Gateway::"alice-ns");',
            'permit(principal == User::"bob", action, resource == AgentCore::Gateway::"bob-ns");',
        ]

        # Alice on her own ns: allowed
        assert await enforcer.authorize('User::"alice"', "read", "alice-ns") is True
        # Alice on bob's ns: denied
        assert await enforcer.authorize('User::"alice"', "read", "bob-ns") is False
        # Bob on his own ns: allowed
        assert await enforcer.authorize('User::"bob"', "read", "bob-ns") is True
        # Bob on alice's ns: denied
        assert await enforcer.authorize('User::"bob"', "read", "alice-ns") is False
