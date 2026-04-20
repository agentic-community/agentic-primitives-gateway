"""Vuln 10: CORS wildcard + credentials combination is defused at startup."""

from __future__ import annotations

import pytest

from agentic_primitives_gateway.main import _resolve_cors_config


def test_wildcard_plus_explicit_origin_is_rejected():
    with pytest.raises(RuntimeError, match="cors_origins"):
        _resolve_cors_config(["*", "https://example.com"])


def test_wildcard_alone_disables_credentials():
    origins, creds = _resolve_cors_config(["*"])
    assert origins == ["*"]
    assert creds is False


def test_explicit_origin_list_keeps_credentials_enabled():
    origins, creds = _resolve_cors_config(["https://a.example", "https://b.example"])
    assert origins == ["https://a.example", "https://b.example"]
    assert creds is True


def test_empty_list_keeps_credentials_enabled():
    """An empty origins list is a locked-down config; no wildcard, no warnings."""
    origins, creds = _resolve_cors_config([])
    assert origins == []
    assert creds is True
