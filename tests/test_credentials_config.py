"""Tests for credential configuration and ServerCredentialMode."""

from __future__ import annotations

from agentic_primitives_gateway.config import CredentialsConfig, Settings
from agentic_primitives_gateway.models.enums import ServerCredentialMode


class TestServerCredentialMode:
    def test_string_fallback(self):
        s = Settings(allow_server_credentials="fallback")
        assert s.allow_server_credentials == ServerCredentialMode.FALLBACK

    def test_string_always(self):
        s = Settings(allow_server_credentials="always")
        assert s.allow_server_credentials == ServerCredentialMode.ALWAYS

    def test_string_never(self):
        s = Settings(allow_server_credentials="never")
        assert s.allow_server_credentials == ServerCredentialMode.NEVER

    def test_default_is_never(self):
        s = Settings()
        assert s.allow_server_credentials == ServerCredentialMode.NEVER


class TestCredentialsConfig:
    def test_defaults(self):
        cfg = CredentialsConfig()
        assert cfg.resolver == "noop"
        assert cfg.oidc.aws.enabled is False
        assert cfg.writer.backend == "noop"
        assert cfg.cache.ttl_seconds == 300
        assert cfg.cache.max_entries == 10000

    def test_oidc_config(self):
        cfg = CredentialsConfig(
            resolver="oidc",
            oidc={"aws": {"enabled": True, "sts_region": "eu-west-1"}},
        )
        assert cfg.resolver == "oidc"
        assert cfg.oidc.aws.enabled is True
        assert cfg.oidc.aws.sts_region == "eu-west-1"


class TestSettingsWithCredentials:
    def test_default_credentials_config(self):
        s = Settings()
        assert s.credentials.resolver == "noop"

    def test_credentials_from_dict(self):
        s = Settings(
            credentials={
                "resolver": "oidc",
                "cache": {"ttl_seconds": 120},
            }
        )
        assert s.credentials.resolver == "oidc"
        assert s.credentials.cache.ttl_seconds == 120
