from __future__ import annotations

import pytest

from agentic_primitives_gateway.auth.models import ANONYMOUS_PRINCIPAL, AuthenticatedPrincipal


class TestAuthenticatedPrincipal:
    def test_basic_creation(self):
        p = AuthenticatedPrincipal(id="user-1", type="user")
        assert p.id == "user-1"
        assert p.type == "user"
        assert p.groups == frozenset()
        assert p.scopes == frozenset()

    def test_with_groups_and_scopes(self):
        p = AuthenticatedPrincipal(
            id="user-1",
            type="user",
            groups=frozenset({"engineering", "admin"}),
            scopes=frozenset({"agents:write", "admin"}),
        )
        assert "engineering" in p.groups
        assert "admin" in p.scopes

    def test_is_anonymous(self):
        p = AuthenticatedPrincipal(id="anon", type="anonymous")
        assert p.is_anonymous is True

        p2 = AuthenticatedPrincipal(id="user-1", type="user")
        assert p2.is_anonymous is False

    def test_is_admin(self):
        p = AuthenticatedPrincipal(id="u", type="user", scopes=frozenset({"admin"}))
        assert p.is_admin is True

        p2 = AuthenticatedPrincipal(id="u", type="user", scopes=frozenset({"read"}))
        assert p2.is_admin is False

        p3 = AuthenticatedPrincipal(id="u", type="user")
        assert p3.is_admin is False

    def test_frozen(self):
        p = AuthenticatedPrincipal(id="u", type="user")
        with pytest.raises(AttributeError):
            p.id = "other"  # type: ignore[misc]

    def test_anonymous_principal_singleton(self):
        assert ANONYMOUS_PRINCIPAL.id == "anonymous"
        assert ANONYMOUS_PRINCIPAL.type == "anonymous"
        assert ANONYMOUS_PRINCIPAL.is_anonymous is True
        assert ANONYMOUS_PRINCIPAL.is_admin is False
