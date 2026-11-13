"""Session ownership claims, cross-replica revocation, and route conflicts."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.models.browser import StartBrowserSessionRequest
from agentic_primitives_gateway.models.code_interpreter import StartSessionRequest
from agentic_primitives_gateway.routes import browser as browser_routes
from agentic_primitives_gateway.routes import code_interpreter as interpreter_routes
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


class SharedRedis:
    """Minimal shared Redis with SET NX and GET/DEL used by the owner store."""

    def __init__(self):
        self.values: dict[str, str] = {}

    async def set(self, key, value, *, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        self.values.pop(key, None)

    async def eval(self, _script, _keys, key, owner, released):
        if self.values.get(key) == owner:
            self.values[key] = released
            return 1
        return 0

    async def scan_iter(self, *, match):
        prefix = match.removesuffix("*")
        for key in list(self.values):
            if key.startswith(prefix):
                yield key


@pytest.mark.asyncio
async def test_redis_authority_rejects_revoked_local_owner():
    redis = SharedRedis()
    first = SessionOwnershipStore()
    second = SessionOwnershipStore()
    first.set_redis(redis)
    second.set_redis(redis)
    alice = AuthenticatedPrincipal(id="alice", type="user")
    bob = AuthenticatedPrincipal(id="bob", type="user")

    assert await first.set_owner("sess-1", "alice")
    assert not await second.set_owner("sess-1", "bob")
    assert await first.get_owner("sess-1") == "alice"
    await second.release_owner("sess-1", "bob")  # cannot erase Alice's claim
    await first.require_owner("sess-1", alice)
    await second.release_owner("sess-1", "alice")
    assert not await second.set_owner("sess-1", "bob")  # retired IDs cannot be recycled
    first._local["sess-1"] = "alice"  # stale cache entry from an older replica

    with pytest.raises(HTTPException) as exc:
        await first.require_owner("sess-1", alice)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException):
        await second.require_owner("sess-1", bob)
    await redis.delete("session_owner:sess-1")
    with pytest.raises(HTTPException) as exc:
        await first.require_owner("sess-1", bob)
    assert exc.value.status_code == 403
    assert await first.owned_session_ids("alice") == set()


@pytest.mark.parametrize(
    ("routes", "request_type", "primitive"),
    [
        (browser_routes, StartBrowserSessionRequest, "browser"),
        (interpreter_routes, StartSessionRequest, "code_interpreter"),
    ],
)
@pytest.mark.asyncio
async def test_start_cannot_replace_other_users_session(routes, request_type, primitive):
    store = SessionOwnershipStore()
    store.set_redis(SharedRedis())
    provider = SimpleNamespace(
        start_session=AsyncMock(return_value={"session_id": "shared", "status": "active"}),
        stop_session=AsyncMock(),
    )
    alice = AuthenticatedPrincipal(id="alice", type="user")
    bob = AuthenticatedPrincipal(id="bob", type="user")
    with (
        patch.object(routes, "registry", SimpleNamespace(**{primitive: provider})),
        patch.object(routes, f"{primitive}_session_owners", store),
        patch.object(routes, "require_principal", return_value=alice) as principal,
    ):
        await routes.start_session(request_type(session_id="shared"))
        principal.return_value = bob
        with pytest.raises(HTTPException) as exc:
            await routes.start_session(request_type(session_id="shared"))
        assert exc.value.status_code == 409
        assert provider.start_session.await_count == 1
        provider.stop_session.assert_not_awaited()
        await store.require_owner("shared", alice)
        with pytest.raises(HTTPException):
            await store.require_owner("shared", bob)


@pytest.mark.parametrize(
    ("routes", "request_type", "primitive"),
    [
        (browser_routes, StartBrowserSessionRequest, "browser"),
        (interpreter_routes, StartSessionRequest, "code_interpreter"),
    ],
)
@pytest.mark.asyncio
async def test_provider_returning_owned_id_does_not_stop_it(routes, request_type, primitive):
    store = SessionOwnershipStore()
    assert await store.set_owner("provider-id", "alice")
    provider = SimpleNamespace(
        start_session=AsyncMock(return_value={"session_id": "provider-id", "status": "active"}),
        stop_session=AsyncMock(),
    )
    with (
        patch.object(routes, "registry", SimpleNamespace(**{primitive: provider})),
        patch.object(routes, f"{primitive}_session_owners", store),
        patch.object(routes, "require_principal", return_value=AuthenticatedPrincipal(id="bob", type="user")),
    ):
        with pytest.raises(HTTPException) as exc:
            await routes.start_session(request_type(session_id="requested-id"))
        assert exc.value.status_code == 409
        provider.stop_session.assert_not_awaited()
        assert await store.get_owner("requested-id") is None
        assert await store.get_owner("provider-id") == "alice"


@pytest.mark.parametrize(
    ("routes", "request_type", "primitive"),
    [
        (browser_routes, StartBrowserSessionRequest, "browser"),
        (interpreter_routes, StartSessionRequest, "code_interpreter"),
    ],
)
@pytest.mark.asyncio
async def test_provider_failure_releases_claim(routes, request_type, primitive):
    store = SessionOwnershipStore()
    provider = SimpleNamespace(start_session=AsyncMock(side_effect=ValueError("unavailable")))
    with (
        patch.object(routes, "registry", SimpleNamespace(**{primitive: provider})),
        patch.object(routes, f"{primitive}_session_owners", store),
        patch.object(routes, "require_principal", return_value=AuthenticatedPrincipal(id="alice", type="user")),
    ):
        with pytest.raises(ValueError, match="unavailable"):
            await routes.start_session(request_type(session_id="requested-id"))
        assert await store.get_owner("requested-id") is None


@pytest.mark.parametrize(
    ("routes", "request_type", "primitive"),
    [
        (browser_routes, StartBrowserSessionRequest, "browser"),
        (interpreter_routes, StartSessionRequest, "code_interpreter"),
    ],
)
@pytest.mark.asyncio
async def test_provider_generated_distinct_id_becomes_owned(routes, request_type, primitive):
    store = SessionOwnershipStore()
    provider = SimpleNamespace(start_session=AsyncMock(return_value={"session_id": "generated", "status": "active"}))
    with (
        patch.object(routes, "registry", SimpleNamespace(**{primitive: provider})),
        patch.object(routes, f"{primitive}_session_owners", store),
        patch.object(routes, "require_principal", return_value=AuthenticatedPrincipal(id="alice", type="user")),
    ):
        result = await routes.start_session(request_type(session_id="requested-id"))
        assert result.session_id == "generated"
        assert await store.get_owner("requested-id") is None
        assert await store.get_owner("generated") == "alice"
