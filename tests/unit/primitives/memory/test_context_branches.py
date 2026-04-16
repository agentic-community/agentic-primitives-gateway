from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentic_primitives_gateway.context import (
    AWSCredentials,
    get_boto3_session,
    get_service_credentials,
    get_service_credentials_or_defaults,
    set_aws_credentials,
    set_service_credentials,
)


class TestGetServiceCredentialsOrDefaults:
    """Test all branches of get_service_credentials_or_defaults()."""

    def test_client_creds_override_defaults(self):
        set_service_credentials({"langfuse": {"public_key": "client-pk", "secret_key": "client-sk"}})
        result = get_service_credentials_or_defaults(
            "langfuse",
            {"public_key": "server-pk", "secret_key": "server-sk", "base_url": "https://server.com"},
        )
        assert result["public_key"] == "client-pk"
        assert result["secret_key"] == "client-sk"
        assert result["base_url"] == "https://server.com"

    def test_no_client_creds_server_fallback_allowed(self):
        set_service_credentials({})
        with patch("agentic_primitives_gateway.context._server_credentials_allowed", return_value=True):
            result = get_service_credentials_or_defaults(
                "langfuse",
                {"public_key": "server-pk", "secret_key": None},
            )
        assert result["public_key"] == "server-pk"

    def test_no_client_creds_server_fallback_disabled_with_defaults(self):
        set_service_credentials({})
        with (
            patch("agentic_primitives_gateway.context._server_credentials_allowed", return_value=False),
            pytest.raises(ValueError, match="credential fallback is disabled"),
        ):
            get_service_credentials_or_defaults(
                "langfuse",
                {"public_key": "server-pk", "secret_key": "server-sk"},
            )

    def test_no_client_creds_no_server_fallback_no_defaults_returns_merged(self):
        set_service_credentials({})
        with patch("agentic_primitives_gateway.context._server_credentials_allowed", return_value=False):
            # defaults have no truthy values, so no error raised
            result = get_service_credentials_or_defaults(
                "langfuse",
                {"public_key": None, "secret_key": None},
            )
        assert result["public_key"] is None
        assert result["secret_key"] is None

    def test_client_creds_merge_with_defaults(self):
        set_service_credentials({"svc": {"key_a": "from_client"}})
        result = get_service_credentials_or_defaults(
            "svc",
            {"key_a": "default_a", "key_b": "default_b"},
        )
        assert result["key_a"] == "from_client"
        assert result["key_b"] == "default_b"

    def test_empty_client_cred_values_not_merged(self):
        """Client creds with empty string values should not override defaults."""
        set_service_credentials({"svc": {"key_a": ""}})
        result = get_service_credentials_or_defaults(
            "svc",
            {"key_a": "default_a"},
        )
        # client_creds is truthy (dict has entries), so it proceeds as client creds
        # but empty values aren't merged, so default stays
        assert result["key_a"] == "default_a"


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
