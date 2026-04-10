"""Tests for credential status endpoint and helpers in routes/credentials.py.

Covers:
- credential_status() endpoint — returns source, aws_configured, server_credentials, required_credentials
- _derive_required_credentials() — inspects provider config to determine required creds
- _invalidate_cache() — invalidates credential cache after write/delete
- set_credential_resolver() — sets the resolver module variable
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import set_authenticated_principal
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.routes import credentials as cred_module
from agentic_primitives_gateway.routes.credentials import (
    _derive_required_credentials,
    _invalidate_cache,
    set_credential_resolver,
)


def _admin_principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(id="admin", type="user", scopes=frozenset({"admin"}))


# ── _derive_required_credentials tests ───────────────────────────────


class TestDeriveRequiredCredentials:
    def test_no_providers_config(self):
        """Returns empty list when settings has no providers."""
        settings = SimpleNamespace()
        assert _derive_required_credentials(settings) == []

    def test_providers_empty_dict(self):
        """Returns empty list when providers is empty dict."""
        settings = SimpleNamespace(providers={})
        assert _derive_required_credentials(settings) == []

    def test_providers_none(self):
        """Returns empty list when providers is None."""
        settings = SimpleNamespace(providers=None)
        assert _derive_required_credentials(settings) == []

    def test_detects_aws_agentcore(self):
        """Detects AWS requirement from AgentCore provider."""
        settings = SimpleNamespace(
            providers={
                "memory": {
                    "backends": {
                        "default": {
                            "backend": "agentic_primitives_gateway.primitives.memory.agentcore.AgentCoreMemoryProvider",
                        }
                    }
                }
            }
        )
        result = _derive_required_credentials(settings)
        assert "aws" in result

    def test_detects_aws_bedrock(self):
        """Detects AWS requirement from Bedrock provider."""
        settings = SimpleNamespace(
            providers={
                "llm": {
                    "backends": {
                        "default": {
                            "backend": "agentic_primitives_gateway.primitives.llm.bedrock.BedrockConverseProvider",
                        }
                    }
                }
            }
        )
        result = _derive_required_credentials(settings)
        assert "aws" in result

    def test_detects_langfuse(self):
        """Detects langfuse service credential requirement."""
        settings = SimpleNamespace(
            providers={
                "observability": {
                    "backends": {
                        "default": {
                            "backend": "agentic_primitives_gateway.primitives.observability.langfuse.LangfuseObservabilityProvider",
                        }
                    }
                }
            }
        )
        result = _derive_required_credentials(settings)
        assert "langfuse" in result

    def test_detects_mem0(self):
        """Detects mem0 service credential requirement."""
        settings = SimpleNamespace(
            providers={
                "memory": {
                    "backends": {
                        "default": {
                            "backend": "agentic_primitives_gateway.primitives.memory.mem0.Mem0MemoryProvider",
                        }
                    }
                }
            }
        )
        result = _derive_required_credentials(settings)
        assert "mem0" in result

    def test_detects_multiple_requirements(self):
        """Detects multiple requirements from different providers."""
        settings = SimpleNamespace(
            providers={
                "memory": {
                    "backends": {
                        "default": {
                            "backend": "some.path.AgentCoreMemoryProvider",
                        }
                    }
                },
                "observability": {
                    "backends": {
                        "default": {
                            "backend": "some.path.LangfuseObservabilityProvider",
                        }
                    }
                },
            }
        )
        result = _derive_required_credentials(settings)
        assert "aws" in result
        assert "langfuse" in result
        # Result should be sorted
        assert result == sorted(result)

    def test_single_backend_format(self):
        """Handles legacy single-backend format (backend key at primitive level)."""
        settings = SimpleNamespace(
            providers={
                "memory": {
                    "backend": "some.path.AgentCoreMemoryProvider",
                }
            }
        )
        result = _derive_required_credentials(settings)
        assert "aws" in result

    def test_non_dict_primitive_config_skipped(self):
        """Non-dict values under providers are safely skipped."""
        settings = SimpleNamespace(
            providers={
                "memory": "not_a_dict",
            }
        )
        result = _derive_required_credentials(settings)
        assert result == []

    def test_non_dict_backend_config_skipped(self):
        """Non-dict values under backends are safely skipped."""
        settings = SimpleNamespace(
            providers={
                "memory": {
                    "backends": {
                        "default": "not_a_dict",
                    }
                }
            }
        )
        result = _derive_required_credentials(settings)
        assert result == []

    def test_empty_backend_path(self):
        """Handles backend with empty or missing backend path."""
        settings = SimpleNamespace(
            providers={
                "memory": {
                    "backends": {
                        "default": {
                            "backend": "",
                        }
                    }
                }
            }
        )
        result = _derive_required_credentials(settings)
        assert result == []

    def test_no_backend_key_in_config(self):
        """Handles backend config dict without a 'backend' key."""
        settings = SimpleNamespace(
            providers={
                "memory": {
                    "backends": {
                        "default": {
                            "config": {},
                        }
                    }
                }
            }
        )
        result = _derive_required_credentials(settings)
        assert result == []

    def test_deduplicates_aws(self):
        """Multiple AWS providers only add 'aws' once."""
        settings = SimpleNamespace(
            providers={
                "memory": {
                    "backends": {
                        "default": {
                            "backend": "some.AgentCoreMemoryProvider",
                        }
                    }
                },
                "llm": {
                    "backends": {
                        "default": {
                            "backend": "some.BedrockConverseProvider",
                        }
                    }
                },
            }
        )
        result = _derive_required_credentials(settings)
        assert result.count("aws") == 1

    def test_providers_not_dict_type(self):
        """Non-dict providers value returns empty list."""
        settings = SimpleNamespace(providers="not_a_dict")
        result = _derive_required_credentials(settings)
        assert result == []

    def test_selenium_grid_provider(self):
        """Detects selenium service credential requirement."""
        settings = SimpleNamespace(
            providers={
                "browser": {
                    "backends": {
                        "default": {
                            "backend": "some.path.SeleniumGridBrowserProvider",
                        }
                    }
                }
            }
        )
        result = _derive_required_credentials(settings)
        assert "selenium" in result


# ── _invalidate_cache tests ──────────────────────────────────────────


class TestInvalidateCache:
    def test_no_resolver_is_noop(self):
        """Does nothing when resolver is None."""
        original = cred_module._resolver
        try:
            cred_module._resolver = None
            # Should not raise
            _invalidate_cache("user-1")
        finally:
            cred_module._resolver = original

    def test_resolver_without_cache_is_noop(self):
        """Does nothing when resolver has no _cache attribute."""
        original = cred_module._resolver
        try:
            cred_module._resolver = SimpleNamespace()  # no _cache
            _invalidate_cache("user-1")
        finally:
            cred_module._resolver = original

    def test_calls_cache_invalidate(self):
        """Calls cache.invalidate(user_id) when resolver has _cache."""
        mock_cache = MagicMock()
        mock_resolver = SimpleNamespace(_cache=mock_cache)
        original = cred_module._resolver
        try:
            cred_module._resolver = mock_resolver
            _invalidate_cache("user-42")
            mock_cache.invalidate.assert_called_once_with("user-42")
        finally:
            cred_module._resolver = original


# ── set_credential_resolver tests ────────────────────────────────────


class TestSetCredentialResolver:
    def test_sets_module_variable(self):
        """set_credential_resolver sets the module-level _resolver."""
        original = cred_module._resolver
        try:
            mock_resolver = MagicMock()
            set_credential_resolver(mock_resolver)
            assert cred_module._resolver is mock_resolver
        finally:
            cred_module._resolver = original


# ── credential_status endpoint tests ─────────────────────────────────


class TestCredentialStatusEndpoint:
    def _client(self) -> TestClient:
        return TestClient(app, raise_server_exceptions=False)

    def test_status_noop_resolver(self):
        """Returns source='none' when resolver is noop."""
        from agentic_primitives_gateway.config import CredentialsConfig, Settings

        mock_settings = Settings(
            allow_server_credentials="never",
            credentials=CredentialsConfig(resolver="noop"),
        )
        with patch("agentic_primitives_gateway.config.settings", mock_settings):
            set_authenticated_principal(_admin_principal())
            client = self._client()
            resp = client.get("/api/v1/credentials/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "none"
        assert data["aws_configured"] is False
        assert data["server_credentials"] == "never"
        assert data["required_credentials"] == []

    def test_status_oidc_resolver_with_aws(self):
        """Returns correct values for OIDC resolver with AWS enabled."""
        from agentic_primitives_gateway.config import CredentialsConfig, Settings

        mock_settings = Settings(
            allow_server_credentials="fallback",
            credentials=CredentialsConfig(
                resolver="oidc",
                oidc={"aws": {"enabled": True}},
            ),
        )
        with patch("agentic_primitives_gateway.config.settings", mock_settings):
            set_authenticated_principal(_admin_principal())
            client = self._client()
            resp = client.get("/api/v1/credentials/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "oidc"
        assert data["aws_configured"] is True
        assert data["server_credentials"] == "fallback"

    def test_status_oidc_resolver_without_aws(self):
        """Returns aws_configured=False when OIDC resolver has AWS disabled."""
        from agentic_primitives_gateway.config import CredentialsConfig, Settings

        mock_settings = Settings(
            allow_server_credentials="always",
            credentials=CredentialsConfig(
                resolver="oidc",
                oidc={"aws": {"enabled": False}},
            ),
        )
        with patch("agentic_primitives_gateway.config.settings", mock_settings):
            set_authenticated_principal(_admin_principal())
            client = self._client()
            resp = client.get("/api/v1/credentials/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "oidc"
        assert data["aws_configured"] is False
        assert data["server_credentials"] == "always"

    def test_status_includes_required_credentials(self):
        """Status includes derived required credentials from provider config."""
        from agentic_primitives_gateway.config import CredentialsConfig, Settings

        mock_settings = Settings(
            allow_server_credentials="never",
            credentials=CredentialsConfig(resolver="noop"),
        )
        # _derive_required_credentials inspects settings.providers as a dict,
        # so we patch it to return a raw dict with a Bedrock provider.
        mock_settings.providers = {
            "llm": {
                "backends": {
                    "default": {
                        "backend": "some.path.BedrockConverseProvider",
                    }
                }
            }
        }
        with patch("agentic_primitives_gateway.config.settings", mock_settings):
            set_authenticated_principal(_admin_principal())
            client = self._client()
            resp = client.get("/api/v1/credentials/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "aws" in data["required_credentials"]

    def test_status_no_credentials_config(self):
        """Returns source='none' when credentials config is absent."""
        from agentic_primitives_gateway.config import Settings

        mock_settings = Settings()
        with patch("agentic_primitives_gateway.config.settings", mock_settings):
            set_authenticated_principal(_admin_principal())
            client = self._client()
            resp = client.get("/api/v1/credentials/status")
        assert resp.status_code == 200
        data = resp.json()
        # Default credentials config has resolver="noop" → source="none"
        assert data["source"] == "none"
