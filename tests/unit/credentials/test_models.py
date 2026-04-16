"""Tests for credential resolution models."""

from __future__ import annotations

from agentic_primitives_gateway.credentials.models import (
    CredentialStatus,
    CredentialUpdateRequest,
    MaskedCredentials,
    ResolvedCredentials,
    mask_value,
)


class TestMaskValue:
    def test_short_value(self):
        assert mask_value("abc") == "****"

    def test_exactly_four_chars(self):
        assert mask_value("abcd") == "****"

    def test_longer_value(self):
        assert mask_value("my-secret-key") == "****-key"

    def test_empty_string(self):
        assert mask_value("") == "****"


class TestResolvedCredentials:
    def test_defaults(self):
        creds = ResolvedCredentials()
        assert creds.aws is None
        assert creds.service_credentials == {}

    def test_with_service_credentials(self):
        creds = ResolvedCredentials(service_credentials={"langfuse": {"public_key": "pk", "secret_key": "sk"}})
        assert creds.service_credentials["langfuse"]["public_key"] == "pk"


class TestCredentialUpdateRequest:
    def test_defaults(self):
        req = CredentialUpdateRequest()
        assert req.attributes == {}

    def test_with_attributes(self):
        req = CredentialUpdateRequest(attributes={"aws_role_arn": "arn:aws:iam::123:role/test"})
        assert req.attributes["aws_role_arn"] == "arn:aws:iam::123:role/test"


class TestCredentialStatus:
    def test_defaults(self):
        status = CredentialStatus(source="none")
        assert status.source == "none"
        assert status.aws_configured is False
        assert status.aws_credential_expiry is None


class TestMaskedCredentials:
    def test_defaults(self):
        masked = MaskedCredentials()
        assert masked.attributes == {}
        assert masked.services == {}
