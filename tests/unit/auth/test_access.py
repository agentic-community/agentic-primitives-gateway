"""Tests for resource-level access control."""

from __future__ import annotations

import pytest

from agentic_primitives_gateway.auth.access import (
    check_access,
    check_owner_or_admin,
    require_access,
    require_owner_or_admin,
)
from agentic_primitives_gateway.auth.models import ANONYMOUS_PRINCIPAL, AuthenticatedPrincipal


def _user(
    id: str = "alice",
    groups: frozenset[str] | None = None,
    scopes: frozenset[str] | None = None,
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        id=id,
        type="user",
        groups=groups or frozenset(),
        scopes=scopes or frozenset(),
    )


class TestCheckAccess:
    def test_owner_has_access(self):
        assert check_access(_user("alice"), "alice", []) is True

    def test_non_owner_no_groups_denied(self):
        assert check_access(_user("bob"), "alice", []) is False

    def test_wildcard_grants_access(self):
        assert check_access(_user("bob"), "alice", ["*"]) is True

    def test_group_membership_grants_access(self):
        p = _user("bob", groups=frozenset({"engineering"}))
        assert check_access(p, "alice", ["engineering"]) is True

    def test_wrong_group_denied(self):
        p = _user("bob", groups=frozenset({"marketing"}))
        assert check_access(p, "alice", ["engineering"]) is False

    def test_admin_scope_grants_access(self):
        p = _user("bob", scopes=frozenset({"admin"}))
        assert check_access(p, "alice", []) is True

    def test_anonymous_with_wildcard(self):
        assert check_access(ANONYMOUS_PRINCIPAL, "alice", ["*"]) is True

    def test_anonymous_without_wildcard(self):
        assert check_access(ANONYMOUS_PRINCIPAL, "alice", []) is False

    def test_multiple_groups_one_matches(self):
        p = _user("bob", groups=frozenset({"marketing", "engineering"}))
        assert check_access(p, "alice", ["engineering"]) is True

    def test_multiple_shared_groups_one_matches(self):
        p = _user("bob", groups=frozenset({"engineering"}))
        assert check_access(p, "alice", ["marketing", "engineering"]) is True


class TestCheckOwnerOrAdmin:
    def test_owner(self):
        assert check_owner_or_admin(_user("alice"), "alice") is True

    def test_non_owner(self):
        assert check_owner_or_admin(_user("bob"), "alice") is False

    def test_admin(self):
        p = _user("bob", scopes=frozenset({"admin"}))
        assert check_owner_or_admin(p, "alice") is True


class TestRequireAccess:
    def test_allowed_returns_principal(self):
        p = _user("alice")
        result = require_access(p, "alice", [])
        assert result is p

    def test_denied_raises_403(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            require_access(_user("bob"), "alice", [])
        assert exc_info.value.status_code == 403

    def test_none_principal_raises_403(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            require_access(None, "alice", [])
        assert exc_info.value.status_code == 403


class TestRequireOwnerOrAdmin:
    def test_owner_returns_principal(self):
        p = _user("alice")
        result = require_owner_or_admin(p, "alice")
        assert result is p

    def test_non_owner_raises_403(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            require_owner_or_admin(_user("bob"), "alice")
        assert exc_info.value.status_code == 403

    def test_admin_returns_principal(self):
        p = _user("bob", scopes=frozenset({"admin"}))
        result = require_owner_or_admin(p, "alice")
        assert result is p

    def test_none_principal_raises_403(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            require_owner_or_admin(None, "alice")
        assert exc_info.value.status_code == 403
