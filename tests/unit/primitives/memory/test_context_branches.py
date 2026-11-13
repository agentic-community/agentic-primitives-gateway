from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentic_primitives_gateway.config import settings
from agentic_primitives_gateway.context import (
    AWSCredentials,
    get_boto3_session,
    get_service_credentials,
    get_service_credentials_or_defaults,
    set_aws_credentials,
    set_service_credentials,
)
from agentic_primitives_gateway.models.enums import ServerCredentialMode


class _ModeOverride:
    """Context manager that swaps ``allow_server_credentials`` for a test."""

    def __init__(self, mode: ServerCredentialMode) -> None:
        self._mode = mode
        self._prev: object = None

    def __enter__(self):
        self._prev = settings.allow_server_credentials
        settings.allow_server_credentials = self._mode
        return self

    def __exit__(self, exc_type, exc, tb):
        settings.allow_server_credentials = self._prev


class TestGetServiceCredentialsOrDefaults:
    """Three-branch rule — mode determines the entire behavior.

    ``NEVER``: caller supplies the full shape; ``defaults`` values are
    not consulted.  ``ALWAYS``: caller is ignored; ``defaults`` is
    returned unchanged.  ``FALLBACK``: caller's set whole if any, else
    ``defaults`` whole (emitting ``provider.server_credentials_used``) —
    never a mix.
    """

    # ── NEVER ────────────────────────────────────────────────────

    def test_never_empty_client_creds_raises(self):
        set_service_credentials({})
        with (
            _ModeOverride(ServerCredentialMode.NEVER),
            pytest.raises(ValueError, match="No langfuse credentials provided"),
        ):
            get_service_credentials_or_defaults(
                "langfuse",
                {"public_key": "server-pk", "secret_key": "server-sk"},
            )

    def test_never_full_client_creds_returns_them_without_defaults(self):
        """Even when operator defaults exist, ``never`` ignores them for credentials."""
        set_service_credentials({"langfuse": {"public_key": "alice-pk", "secret_key": "alice-sk"}})
        with _ModeOverride(ServerCredentialMode.NEVER):
            result = get_service_credentials_or_defaults(
                "langfuse",
                {"public_key": "server-pk", "secret_key": "server-sk", "base_url": "https://server.com"},
            )
        assert result["public_key"] == "alice-pk"
        assert result["secret_key"] == "alice-sk"
        # Endpoint keys are operator config, not credentials — always filled.
        assert result["base_url"] == "https://server.com"

    def test_never_ignores_caller_endpoint_key(self):
        set_service_credentials(
            {"langfuse": {"public_key": "alice-pk", "secret_key": "alice-sk", "base_url": "http://redirect"}}
        )
        with _ModeOverride(ServerCredentialMode.NEVER):
            result = get_service_credentials_or_defaults(
                "langfuse",
                {"public_key": None, "secret_key": None, "base_url": "https://server.com"},
            )
        assert result["base_url"] == "https://server.com"

    def test_never_endpoint_only_client_creds_raises(self):
        """A caller that only vends an endpoint key has supplied no credentials."""
        set_service_credentials({"langfuse": {"base_url": "http://redirect"}})
        with (
            _ModeOverride(ServerCredentialMode.NEVER),
            pytest.raises(ValueError, match="No langfuse credentials provided"),
        ):
            get_service_credentials_or_defaults("langfuse", {"public_key": "server-pk", "base_url": "https://s"})

    def test_never_partial_client_creds_returns_shape_preserving_dict(self):
        """Partial client creds → shape-preserving dict with ``None`` for missing keys."""
        set_service_credentials({"langfuse": {"public_key": "alice-pk"}})
        with _ModeOverride(ServerCredentialMode.NEVER):
            result = get_service_credentials_or_defaults(
                "langfuse",
                {"public_key": "server-pk", "secret_key": "server-sk"},
            )
        assert result["public_key"] == "alice-pk"
        assert result["secret_key"] is None

    # ── ALWAYS ───────────────────────────────────────────────────

    def test_always_ignores_client_creds(self):
        """Under ``always``, operator controls credentials — caller values discarded."""
        set_service_credentials({"langfuse": {"public_key": "alice-pk", "secret_key": "alice-sk"}})
        with _ModeOverride(ServerCredentialMode.ALWAYS):
            result = get_service_credentials_or_defaults(
                "langfuse",
                {"public_key": "server-pk", "secret_key": "server-sk"},
            )
        assert result["public_key"] == "server-pk"
        assert result["secret_key"] == "server-sk"

    def test_always_empty_client_creds_returns_defaults(self):
        set_service_credentials({})
        with _ModeOverride(ServerCredentialMode.ALWAYS):
            result = get_service_credentials_or_defaults(
                "langfuse",
                {"public_key": "server-pk", "secret_key": "server-sk"},
            )
        assert result == {"public_key": "server-pk", "secret_key": "server-sk"}

    # ── FALLBACK ─────────────────────────────────────────────────

    def test_fallback_partial_caller_set_is_not_mixed_with_defaults(self):
        """A caller's partial set must not borrow operator values (e.g. realm + operator secret)."""
        set_service_credentials({"keycloak": {"realm": "other-realm"}})
        with _ModeOverride(ServerCredentialMode.FALLBACK):
            result = get_service_credentials_or_defaults(
                "keycloak",
                {"server_url": "https://kc", "realm": "op-realm", "client_id": "op", "client_secret": "op-secret"},
            )
        assert result == {"server_url": "https://kc", "realm": "other-realm", "client_id": None, "client_secret": None}

    def test_fallback_empty_client_creds_uses_defaults(self):
        set_service_credentials({})
        with _ModeOverride(ServerCredentialMode.FALLBACK):
            result = get_service_credentials_or_defaults(
                "langfuse",
                {"public_key": "server-pk", "secret_key": None},
            )
        assert result["public_key"] == "server-pk"

    def test_fallback_empty_values_dont_override(self):
        """Empty-string client values are ignored — a fall-through to defaults."""
        set_service_credentials({"svc": {"key_a": ""}})
        with _ModeOverride(ServerCredentialMode.FALLBACK):
            result = get_service_credentials_or_defaults(
                "svc",
                {"key_a": "default_a"},
            )
        assert result["key_a"] == "default_a"

    def test_fallback_unrelated_service_key_does_not_count_as_caller_set(self):
        """``agentcore.memory_id`` must not disable the gateway's operator ``gateway_token``."""
        set_service_credentials({"agentcore": {"memory_id": "m-1"}})
        with _ModeOverride(ServerCredentialMode.FALLBACK):
            result = get_service_credentials_or_defaults("agentcore", {"gateway_token": "op-tok"})
        assert result == {"gateway_token": "op-tok"}

    def test_fallback_partial_caller_set_does_not_emit(self):
        """No operator credential is used for a caller's set → no audit event."""
        set_service_credentials({"svc": {"key_a": "alice"}})
        with (
            _ModeOverride(ServerCredentialMode.FALLBACK),
            patch("agentic_primitives_gateway.context._emit_server_credentials_used") as emit,
        ):
            get_service_credentials_or_defaults(
                "svc",
                {"key_a": "default_a", "key_b": "default_b", "key_c": "default_c"},
            )
        emit.assert_not_called()

    def test_fallback_emit_for_empty_client_creds(self):
        set_service_credentials({})
        with (
            _ModeOverride(ServerCredentialMode.FALLBACK),
            patch("agentic_primitives_gateway.context._emit_server_credentials_used") as emit,
        ):
            get_service_credentials_or_defaults("svc", {"key_a": "default_a"})
        emit.assert_called_once_with("svc")

    def test_fallback_never_pairs_operator_secret_with_caller_endpoint(self):
        """The OIDC SSRF: caller vends only ``base_url``, operator fills the keys."""
        set_service_credentials({"langfuse": {"base_url": "http://redirect:8081"}})
        with _ModeOverride(ServerCredentialMode.FALLBACK):
            result = get_service_credentials_or_defaults(
                "langfuse",
                {"public_key": "server-pk", "secret_key": "server-sk", "base_url": "https://langfuse.internal"},
            )
        assert result == {
            "public_key": "server-pk",
            "secret_key": "server-sk",
            "base_url": "https://langfuse.internal",
        }

    def test_fallback_operator_endpoint_alone_does_not_emit(self):
        """Filling only the endpoint from config uses no operator secret."""
        set_service_credentials({"langfuse": {"public_key": "alice-pk", "secret_key": "alice-sk"}})
        with (
            _ModeOverride(ServerCredentialMode.FALLBACK),
            patch("agentic_primitives_gateway.context._emit_server_credentials_used") as emit,
        ):
            get_service_credentials_or_defaults(
                "langfuse",
                {"public_key": "server-pk", "secret_key": "server-sk", "base_url": "https://langfuse.internal"},
            )
        emit.assert_not_called()

    def test_always_emits_for_non_empty_defaults(self):
        """``always`` is also a "shared-cred" state — audit consumers want to see it."""
        set_service_credentials({})
        with (
            _ModeOverride(ServerCredentialMode.ALWAYS),
            patch("agentic_primitives_gateway.context._emit_server_credentials_used") as emit,
        ):
            get_service_credentials_or_defaults("svc", {"key_a": "default_a"})
        emit.assert_called_once_with("svc")


class TestGetBoto3Session:
    """Test get_boto3_session() credential resolution branches."""

    @patch("boto3.Session")
    def test_with_context_credentials(self, mock_session_cls):
        set_aws_credentials(
            AWSCredentials(
                access_key_id="AKIA_TEST",
                secret_access_key="secret_test",
                session_token="token_test",
                region="us-west-2",
            )
        )
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        result = get_boto3_session()

        mock_session_cls.assert_called_once_with(
            aws_access_key_id="AKIA_TEST",
            aws_secret_access_key="secret_test",
            aws_session_token="token_test",
            region_name="us-west-2",
        )
        assert result is mock_session

    @patch("boto3.Session")
    def test_with_context_credentials_no_region(self, mock_session_cls):
        set_aws_credentials(AWSCredentials(access_key_id="AKIA", secret_access_key="secret"))
        mock_session_cls.return_value = MagicMock()
        get_boto3_session(default_region="eu-west-1")
        mock_session_cls.assert_called_once_with(
            aws_access_key_id="AKIA",
            aws_secret_access_key="secret",
            aws_session_token=None,
            region_name="eu-west-1",
        )

    @patch("boto3.Session")
    def test_no_context_server_fallback_allowed(self, mock_session_cls):
        set_aws_credentials(None)
        with patch("agentic_primitives_gateway.context._server_credentials_allowed", return_value=True):
            get_boto3_session(default_region="ap-south-1")
        mock_session_cls.assert_called_once_with(region_name="ap-south-1")

    def test_no_context_server_fallback_disabled_raises(self):
        set_aws_credentials(None)
        with (
            patch("agentic_primitives_gateway.context._server_credentials_allowed", return_value=False),
            pytest.raises(ValueError, match="No AWS credentials provided"),
        ):
            get_boto3_session()


class TestGetServiceCredentials:
    """Test get_service_credentials() edge cases."""

    def test_returns_none_for_unknown_service(self):
        set_service_credentials({})
        assert get_service_credentials("nonexistent") is None

    def test_returns_none_for_empty_dict_service(self):
        set_service_credentials({"svc": {}})
        assert get_service_credentials("svc") is None

    def test_returns_credentials(self):
        set_service_credentials({"svc": {"key": "value"}})
        result = get_service_credentials("svc")
        assert result == {"key": "value"}
