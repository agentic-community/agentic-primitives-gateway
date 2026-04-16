"""Tests for route helper functions: require_user_scoped, require_admin, SessionOwnershipStore."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.routes._helpers import (
    SessionOwnershipStore,
    require_admin,
    require_user_scoped,
)


def _user(
    id: str = "alice",
    scopes: frozenset[str] | None = None,
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(id=id, type="user", scopes=scopes or frozenset())


def _admin(id: str = "admin-user") -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(id=id, type="user", scopes=frozenset({"admin"}))


# ── require_user_scoped ──────────────────────────────────────────────


class TestRequireUserScoped:
    def test_owner_matches(self):
        """No exception when the user_id in the value matches the principal."""
        require_user_scoped("agent:bot:u:alice", _user("alice"))

    def test_non_owner_denied(self):
        with pytest.raises(HTTPException) as exc_info:
            require_user_scoped("agent:bot:u:alice", _user("bob"))
        assert exc_info.value.status_code == 403

    def test_admin_bypasses(self):
        """Admin can access any user-scoped value."""
        require_user_scoped("agent:bot:u:alice", _admin())

    def test_unscoped_value_allowed(self):
        """Values without :u: marker are not user-scoped — allow through."""
        require_user_scoped("shared-namespace", _user("bob"))

    def test_empty_string(self):
        require_user_scoped("", _user("alice"))

    def test_namespace_with_user_scope(self):
        require_user_scoped("agent:my-agent:u:user-123", _user("user-123"))

    def test_namespace_wrong_user(self):
        with pytest.raises(HTTPException):
            require_user_scoped("agent:my-agent:u:user-123", _user("user-456"))


# ── require_admin ────────────────────────────────────────────────────


class TestRequirePrincipal:
    def test_no_principal_raises(self):
        from agentic_primitives_gateway.context import set_authenticated_principal
        from agentic_primitives_gateway.routes._helpers import require_principal

        set_authenticated_principal(None)  # type: ignore[arg-type]
        with pytest.raises(RuntimeError, match="No authenticated principal"):
            require_principal()


class TestRequireAdmin:
    def test_admin_returns_principal(self):
        from agentic_primitives_gateway.context import set_authenticated_principal

        principal = _admin()
        set_authenticated_principal(principal)
        result = require_admin()
        assert result is principal

    def test_non_admin_raises_403(self):
        from agentic_primitives_gateway.context import set_authenticated_principal

        set_authenticated_principal(_user("alice"))
        with pytest.raises(HTTPException) as exc_info:
            require_admin()
        assert exc_info.value.status_code == 403


# ── SessionOwnershipStore ────────────────────────────────────────────


class TestSessionOwnershipStore:
    @pytest.fixture
    def store(self) -> SessionOwnershipStore:
        return SessionOwnershipStore()

    @pytest.mark.asyncio
    async def test_set_and_get_owner(self, store: SessionOwnershipStore):
        await store.set_owner("sess-1", "alice")
        assert await store.get_owner("sess-1") == "alice"

    @pytest.mark.asyncio
    async def test_get_owner_missing(self, store: SessionOwnershipStore):
        assert await store.get_owner("nonexistent") is None

    @pytest.mark.asyncio
    async def test_delete(self, store: SessionOwnershipStore):
        await store.set_owner("sess-1", "alice")
        await store.delete("sess-1")
        assert await store.get_owner("sess-1") is None

    @pytest.mark.asyncio
    async def test_require_owner_passes_for_owner(self, store: SessionOwnershipStore):
        await store.set_owner("sess-1", "alice")
        await store.require_owner("sess-1", _user("alice"))  # should not raise

    @pytest.mark.asyncio
    async def test_require_owner_denies_other(self, store: SessionOwnershipStore):
        await store.set_owner("sess-1", "alice")
        with pytest.raises(HTTPException) as exc_info:
            await store.require_owner("sess-1", _user("bob"))
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_require_owner_admin_bypasses(self, store: SessionOwnershipStore):
        await store.set_owner("sess-1", "alice")
        await store.require_owner("sess-1", _admin())  # should not raise

    @pytest.mark.asyncio
    async def test_require_owner_unknown_session_passes(self, store: SessionOwnershipStore):
        """Unknown sessions are allowed (e.g., agent-created sessions not tracked here)."""
        await store.require_owner("unknown-sess", _user("bob"))  # should not raise

    @pytest.mark.asyncio
    async def test_overwrite_owner(self, store: SessionOwnershipStore):
        await store.set_owner("sess-1", "alice")
        await store.set_owner("sess-1", "bob")
        assert await store.get_owner("sess-1") == "bob"

    @pytest.mark.asyncio
    async def test_owned_session_ids(self, store: SessionOwnershipStore):
        await store.set_owner("s1", "alice")
        await store.set_owner("s2", "bob")
        await store.set_owner("s3", "alice")
        result = await store.owned_session_ids("alice")
        assert result == {"s1", "s3"}


class TestSessionOwnershipStoreRedis:
    """Test Redis-backed paths of SessionOwnershipStore."""

    @pytest.fixture
    def store(self) -> SessionOwnershipStore:
        from unittest.mock import AsyncMock

        s = SessionOwnershipStore()
        mock_redis = AsyncMock()
        s.set_redis(mock_redis)
        return s

    @pytest.mark.asyncio
    async def test_set_owner_writes_redis(self, store: SessionOwnershipStore):
        await store.set_owner("sess-1", "alice")
        store._redis.set.assert_called_once_with("session_owner:sess-1", "alice", ex=86400)

    @pytest.mark.asyncio
    async def test_get_owner_from_redis(self, store: SessionOwnershipStore):
        store._redis.get.return_value = "alice"
        owner = await store.get_owner("sess-1")
        assert owner == "alice"
        store._redis.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_deletes_redis(self, store: SessionOwnershipStore):
        await store.set_owner("sess-1", "alice")
        await store.delete("sess-1")
        store._redis.delete.assert_called_once_with("session_owner:sess-1")

    @pytest.mark.asyncio
    async def test_owned_session_ids_scans_redis(self, store: SessionOwnershipStore):
        async def _scan_iter(match: str):
            for k in ["session_owner:s1", "session_owner:s2"]:
                yield k

        store._redis.scan_iter = _scan_iter
        store._redis.get.side_effect = lambda k: "alice" if "s1" in k else "bob"

        result = await store.owned_session_ids("alice")
        assert "s1" in result
