"""Vulns 3 & 4: JWT audience required; empty sub rejected."""

from __future__ import annotations

from unittest.mock import patch

import pytest


def _make_backend(**overrides):
    from agentic_primitives_gateway.auth.jwt import JwtAuthBackend

    defaults = {
        "issuer": "https://idp.example.com",
        "audience": "my-client-id",
    }
    defaults.update(overrides)
    with patch("agentic_primitives_gateway.auth.jwt.PyJWKClient"):
        return JwtAuthBackend(**defaults)


def test_missing_audience_rejected_at_init():
    from agentic_primitives_gateway.auth.jwt import JwtAuthBackend

    with (
        patch("agentic_primitives_gateway.auth.jwt.PyJWKClient"),
        pytest.raises(ValueError, match=r"audience.*required"),
    ):
        JwtAuthBackend(issuer="https://idp.example.com")


def test_empty_audience_rejected_at_init():
    from agentic_primitives_gateway.auth.jwt import JwtAuthBackend

    with (
        patch("agentic_primitives_gateway.auth.jwt.PyJWKClient"),
        pytest.raises(ValueError, match=r"audience.*required"),
    ):
        JwtAuthBackend(issuer="https://idp.example.com", audience="")


def test_whitespace_audience_rejected_at_init():
    from agentic_primitives_gateway.auth.jwt import JwtAuthBackend

    with (
        patch("agentic_primitives_gateway.auth.jwt.PyJWKClient"),
        pytest.raises(ValueError, match=r"audience.*required"),
    ):
        JwtAuthBackend(issuer="https://idp.example.com", audience="   ")


def test_empty_sub_produces_no_principal():
    backend = _make_backend()
    # Empty sub → _claims_to_principal returns None.
    assert backend._claims_to_principal({"sub": "", "groups": ["admin"]}) is None
    assert backend._claims_to_principal({}) is None
    assert backend._claims_to_principal({"sub": "   "}) is None


def test_valid_sub_produces_principal():
    backend = _make_backend()
    principal = backend._claims_to_principal({"sub": "alice", "groups": ["admin"], "scope": "read write"})
    assert principal is not None
    assert principal.id == "alice"
    assert "admin" in principal.groups
    assert {"read", "write"}.issubset(principal.scopes)
