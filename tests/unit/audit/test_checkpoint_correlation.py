"""Verify checkpoint serialize/restore round-trips correlation_id."""

from __future__ import annotations

from agentic_primitives_gateway.agents.checkpoint_utils import (
    restore_auth_context,
    serialize_auth_context,
)
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import (
    get_correlation_id,
    set_authenticated_principal,
    set_correlation_id,
)


def test_correlation_id_round_trips_through_checkpoint():
    set_authenticated_principal(AuthenticatedPrincipal(id="alice", type="user"))
    set_correlation_id("corr-abc-123")
    try:
        data = serialize_auth_context()
        assert data.get("correlation_id") == "corr-abc-123"

        # Simulate a fresh replica: clear context.
        set_correlation_id("")
        set_authenticated_principal(None)

        restore_auth_context(data)
        assert get_correlation_id() == "corr-abc-123"
    finally:
        set_correlation_id("")
        set_authenticated_principal(None)


def test_missing_correlation_id_omitted_from_checkpoint():
    set_authenticated_principal(AuthenticatedPrincipal(id="alice", type="user"))
    set_correlation_id("")
    try:
        data = serialize_auth_context()
        assert "correlation_id" not in data
    finally:
        set_authenticated_principal(None)
