"""Vuln 8: SessionOwnershipStore.require_owner defaults to DENY when owner unknown."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.routes._helpers import SessionOwnershipStore


@pytest.mark.asyncio
async def test_require_owner_denies_when_owner_not_recorded():
    """A session with no recorded owner must not be accessible to a non-admin."""
    store = SessionOwnershipStore()
    non_admin = AuthenticatedPrincipal(id="alice", type="user")
    with pytest.raises(HTTPException) as exc:
        await store.require_owner("unowned-session", non_admin)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_owner_denies_when_owner_is_different_user():
    store = SessionOwnershipStore()
    await store.set_owner("sess-1", "bob")
    alice = AuthenticatedPrincipal(id="alice", type="user")
    with pytest.raises(HTTPException):
        await store.require_owner("sess-1", alice)


@pytest.mark.asyncio
async def test_require_owner_allows_owner():
    store = SessionOwnershipStore()
    await store.set_owner("sess-1", "alice")
    alice = AuthenticatedPrincipal(id="alice", type="user")
    await store.require_owner("sess-1", alice)  # must not raise


@pytest.mark.asyncio
async def test_require_owner_allows_admin_regardless_of_owner():
    store = SessionOwnershipStore()
    # No owner recorded.
    admin = AuthenticatedPrincipal(id="ops", type="user", scopes=frozenset({"admin"}))
    await store.require_owner("unowned-session", admin)  # must not raise
    # Owned by someone else.
    await store.set_owner("sess-1", "bob")
    await store.require_owner("sess-1", admin)  # must not raise
