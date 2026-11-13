"""Tests for credential configuration and ServerCredentialMode."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

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

    def test_default_helm_chart_rejects_noop_auth(self):
        helm = shutil.which("helm")
        if helm is None:
            pytest.skip("Helm is not installed")

        chart = Path(__file__).resolve().parents[3] / "deploy/helm/agentic-primitives-gateway"
        rendered = subprocess.run([helm, "template", "apg", str(chart)], capture_output=True, text=True)
        assert rendered.returncode != 0
        assert "auth.backend is required" in rendered.stderr
        assert "noop admin access is forbidden" in rendered.stderr


class TestHelmAuthentication:
    @staticmethod
    def render(*values: str) -> subprocess.CompletedProcess[str]:
        helm = shutil.which("helm")
        if helm is None:
            pytest.skip("Helm is not installed")
        chart = Path(__file__).resolve().parents[3] / "deploy/helm/agentic-primitives-gateway"
        return subprocess.run([helm, "template", "apg", str(chart), *values], capture_output=True, text=True)

    @pytest.mark.parametrize("backend", ["noop", "bogus"])
    def test_rejects_unauthorized_backends(self, backend):
        result = self.render("--set", f"auth.backend={backend}")
        assert result.returncode != 0
        assert "auth.backend is required" in result.stderr

    def test_jwt_requires_issuer_and_audience(self):
        result = self.render("--set", "auth.backend=jwt")
        assert result.returncode != 0
        assert "auth.jwt.issuer is required" in result.stderr

        result = self.render("--set", "auth.backend=jwt", "--set", "auth.jwt.issuer=https://issuer.example")
        assert result.returncode != 0
        assert "auth.jwt.audience is required" in result.stderr

    def test_jwt_renders_server_auth_config_without_secrets(self):
        result = self.render(
            "--set",
            "auth.backend=jwt",
            "--set-string",
            "auth.jwt.issuer=https://issuer.example",
            "--set-string",
            "auth.jwt.audience=gateway-client",
            "--set-string",
            "auth.jwt.jwks_url=https://issuer.example/jwks",
            "--set-string",
            "auth.jwt.client_secret=do-not-render-this-secret",
        )
        assert result.returncode == 0, result.stderr
        documents = [document for document in yaml.safe_load_all(result.stdout) if document]
        config = yaml.safe_load(next(d for d in documents if d["kind"] == "ConfigMap")["data"]["config.yaml"])
        settings = Settings.model_validate(config)
        assert settings.allow_server_credentials == ServerCredentialMode.NEVER
        assert settings.auth.backend == "jwt"
        assert settings.auth.jwt == {
            "issuer": "https://issuer.example",
            "audience": "gateway-client",
            "jwks_url": "https://issuer.example/jwks",
        }
        assert "do-not-render-this-secret" not in result.stdout
        deployment = next(d for d in documents if d["kind"] == "Deployment")
        assert not any(
            e["name"] == "APG_AUTH_API_KEYS" for e in deployment["spec"]["template"]["spec"]["containers"][0]["env"]
        )

    def test_api_keys_require_existing_secret(self):
        result = self.render("--set", "auth.backend=api_key")
        assert result.returncode != 0
        assert "auth.apiKey.existingSecret.name is required" in result.stderr

    def test_api_keys_are_loaded_from_secret_not_configmap(self, monkeypatch):
        result = self.render(
            "--set",
            "auth.backend=api_key",
            "--set",
            "auth.apiKey.existingSecret.name=gateway-auth",
        )
        assert result.returncode == 0, result.stderr
        documents = [document for document in yaml.safe_load_all(result.stdout) if document]
        config = next(d for d in documents if d["kind"] == "ConfigMap")["data"]["config.yaml"]
        assert "${APG_AUTH_API_KEYS}" in config
        assert "top-secret-key" not in result.stdout
        deployment = next(d for d in documents if d["kind"] == "Deployment")
        env = deployment["spec"]["template"]["spec"]["containers"][0]["env"]
        assert {
            "name": "APG_AUTH_API_KEYS",
            "valueFrom": {"secretKeyRef": {"name": "gateway-auth", "key": "api-keys.json"}},
        } in env

        monkeypatch.setenv("APG_AUTH_API_KEYS", json.dumps([{"key": "top-secret-key", "principal_id": "operator"}]))
        from agentic_primitives_gateway.config import _expand_vars

        settings = Settings.model_validate(yaml.safe_load(_expand_vars(config)))
        assert settings.auth.backend == "api_key"
        assert settings.auth.api_keys == [{"key": "top-secret-key", "principal_id": "operator"}]


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
