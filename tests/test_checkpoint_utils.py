"""Tests for checkpoint_utils: auth context serialization and provider overrides."""

from __future__ import annotations

import pytest

from agentic_primitives_gateway.agents.checkpoint_utils import (
    apply_provider_overrides,
    restore_auth_context,
    restore_provider_overrides,
    serialize_auth_context,
)
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import (
    AWSCredentials,
    _service_credentials,
    get_authenticated_principal,
    get_aws_credentials,
    get_provider_override,
    set_authenticated_principal,
    set_aws_credentials,
    set_provider_overrides,
    set_service_credentials,
)
from agentic_primitives_gateway.models.agents import AgentSpec

# ── Helpers ──────────────────────────────────────────────────────────


def _clear_context() -> None:
    """Reset all request-scoped contextvars to defaults."""
    set_authenticated_principal(None)  # type: ignore[arg-type]
    set_aws_credentials(None)
    set_service_credentials({})
    set_provider_overrides({})


_ALICE = AuthenticatedPrincipal(
    id="alice",
    type="user",
    groups=frozenset({"engineering", "ml"}),
    scopes=frozenset({"read", "write"}),
)

_AWS = AWSCredentials(
    access_key_id="AKIA123",
    secret_access_key="secret",
    session_token="tok",
    region="us-west-2",
)

_SVC_CREDS = {"langfuse": {"public_key": "pk", "secret_key": "sk"}}


# ── serialize_auth_context ───────────────────────────────────────────


class TestSerializeAuthContext:
    def setup_method(self) -> None:
        _clear_context()

    def test_all_fields_present(self) -> None:
        """When principal, AWS creds, and service creds are all set, all appear in output."""
        set_authenticated_principal(_ALICE)
        set_aws_credentials(_AWS)
        set_service_credentials(_SVC_CREDS)

        data = serialize_auth_context()

        assert data["principal"]["id"] == "alice"
        assert data["principal"]["type"] == "user"
        assert set(data["principal"]["groups"]) == {"engineering", "ml"}
        assert set(data["principal"]["scopes"]) == {"read", "write"}

        assert data["aws_credentials"]["access_key_id"] == "AKIA123"
        assert data["aws_credentials"]["secret_access_key"] == "secret"
        assert data["aws_credentials"]["session_token"] == "tok"
        assert data["aws_credentials"]["region"] == "us-west-2"

        assert data["service_credentials"] == _SVC_CREDS

    def test_only_principal_set(self) -> None:
        """When only principal is set, no aws/service keys appear."""
        set_authenticated_principal(_ALICE)

        data = serialize_auth_context()

        assert "principal" in data
        assert "aws_credentials" not in data
        assert "service_credentials" not in data

    def test_nothing_set(self) -> None:
        """When nothing is set, returns empty dict."""
        data = serialize_auth_context()
        assert data == {}

    def test_aws_without_session_token(self) -> None:
        """AWS credentials without session token serialize correctly."""
        set_authenticated_principal(_ALICE)
        set_aws_credentials(AWSCredentials(access_key_id="AK", secret_access_key="SK"))

        data = serialize_auth_context()
        assert data["aws_credentials"]["session_token"] is None
        assert data["aws_credentials"]["region"] is None


# ── restore_auth_context ─────────────────────────────────────────────


class TestRestoreAuthContext:
    def setup_method(self) -> None:
        _clear_context()

    def test_restores_principal(self) -> None:
        """Restores principal with id, type, groups, and scopes."""
        data = {
            "principal": {
                "id": "bob",
                "type": "service",
                "groups": ["ops", "sre"],
                "scopes": ["admin"],
            }
        }

        principal = restore_auth_context(data)

        assert principal.id == "bob"
        assert principal.type == "service"
        assert principal.groups == frozenset({"ops", "sre"})
        assert principal.scopes == frozenset({"admin"})
        # Also check the contextvar was set
        assert get_authenticated_principal() == principal

    def test_restores_aws_credentials(self) -> None:
        data = {
            "principal": {"id": "alice", "type": "user"},
            "aws_credentials": {
                "access_key_id": "AK",
                "secret_access_key": "SK",
                "session_token": "ST",
                "region": "eu-west-1",
            },
        }

        restore_auth_context(data)

        creds = get_aws_credentials()
        assert creds is not None
        assert creds.access_key_id == "AK"
        assert creds.secret_access_key == "SK"
        assert creds.session_token == "ST"
        assert creds.region == "eu-west-1"

    def test_restores_service_credentials(self) -> None:
        data = {
            "principal": {"id": "alice", "type": "user"},
            "service_credentials": {"openai": {"api_key": "sk-xxx"}},
        }

        restore_auth_context(data)

        svc = _service_credentials.get()
        assert svc == {"openai": {"api_key": "sk-xxx"}}

    def test_raises_when_principal_missing(self) -> None:
        with pytest.raises(ValueError, match="missing principal data"):
            restore_auth_context({})

    def test_raises_when_principal_has_no_id(self) -> None:
        with pytest.raises(ValueError, match="missing principal data"):
            restore_auth_context({"principal": {"type": "user"}})

    def test_defaults_for_missing_optional_fields(self) -> None:
        """Missing type/groups/scopes get sensible defaults."""
        data = {"principal": {"id": "minimal"}}

        principal = restore_auth_context(data)

        assert principal.type == "user"
        assert principal.groups == frozenset()
        assert principal.scopes == frozenset()

    def test_no_aws_or_service_creds_leaves_context_empty(self) -> None:
        """When checkpoint has no AWS or service creds, those contextvars stay default."""
        data = {"principal": {"id": "alice", "type": "user"}}

        restore_auth_context(data)

        assert get_aws_credentials() is None
        assert _service_credentials.get() == {}


# ── apply_provider_overrides ─────────────────────────────────────────


class TestApplyProviderOverrides:
    def setup_method(self) -> None:
        _clear_context()

    def test_returns_previous_overrides(self) -> None:
        """apply_provider_overrides returns the previous state."""
        set_provider_overrides({"memory": "mem0", "gateway": "bedrock"})

        spec = AgentSpec(
            name="test",
            model="m",
            provider_overrides={"memory": "in_memory"},
        )

        prev = apply_provider_overrides(spec)

        assert prev["memory"] == "mem0"
        assert prev["gateway"] == "bedrock"

    def test_applies_new_overrides(self) -> None:
        """Agent's overrides are applied (agent wins on conflict)."""
        set_provider_overrides({"memory": "mem0", "gateway": "bedrock"})

        spec = AgentSpec(
            name="test",
            model="m",
            provider_overrides={"memory": "in_memory", "observability": "langfuse"},
        )

        apply_provider_overrides(spec)

        # Agent override wins
        assert get_provider_override("memory") == "in_memory"
        # Agent adds new override
        assert get_provider_override("observability") == "langfuse"
        # Parent override preserved (not overridden by agent)
        assert get_provider_override("gateway") == "bedrock"

    def test_no_overrides_on_spec(self) -> None:
        """When spec has no overrides, existing overrides are unchanged."""
        set_provider_overrides({"memory": "mem0"})

        spec = AgentSpec(name="test", model="m")  # no provider_overrides

        prev = apply_provider_overrides(spec)

        assert prev == {"memory": "mem0"}
        # Original overrides untouched
        assert get_provider_override("memory") == "mem0"

    def test_empty_initial_state(self) -> None:
        """When no overrides exist initially, returns empty dict."""
        spec = AgentSpec(
            name="test",
            model="m",
            provider_overrides={"memory": "in_memory"},
        )

        prev = apply_provider_overrides(spec)

        assert prev == {}
        assert get_provider_override("memory") == "in_memory"


# ── restore_provider_overrides ───────────────────────────────────────


class TestRestoreProviderOverrides:
    def setup_method(self) -> None:
        _clear_context()

    def test_restores_previous_overrides(self) -> None:
        """After restoring, the overrides match the saved state."""
        # Start with some overrides
        set_provider_overrides({"memory": "mem0"})

        # Apply agent overrides
        spec = AgentSpec(
            name="test",
            model="m",
            provider_overrides={"memory": "in_memory", "gateway": "bedrock"},
        )
        prev = apply_provider_overrides(spec)

        # Memory was changed
        assert get_provider_override("memory") == "in_memory"

        # Restore
        restore_provider_overrides(prev)

        # Back to original
        assert get_provider_override("memory") == "mem0"
        assert get_provider_override("gateway") is None

    def test_restores_to_empty(self) -> None:
        """Restoring an empty dict clears all overrides."""
        set_provider_overrides({"memory": "in_memory"})

        restore_provider_overrides({})

        assert get_provider_override("memory") is None
