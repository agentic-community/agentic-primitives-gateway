from __future__ import annotations

import textwrap

from agentic_primitives_gateway.config import (
    PrimitiveProvidersConfig,
    Settings,
    _expand_vars,
)


class TestExpandVars:
    """Tests for _expand_vars() shell-style variable expansion."""

    def test_plain_text_unchanged(self):
        assert _expand_vars("no variables here") == "no variables here"

    def test_simple_dollar_var(self, monkeypatch):
        monkeypatch.setenv("MY_VAR", "hello")
        assert _expand_vars("$MY_VAR") == "hello"

    def test_braced_var(self, monkeypatch):
        monkeypatch.setenv("MY_VAR", "hello")
        assert _expand_vars("${MY_VAR}") == "hello"

    def test_braced_var_unset_with_default(self, monkeypatch):
        monkeypatch.delenv("UNSET_VAR_XYZ", raising=False)
        # Without a default, os.path.expandvars may leave ${VAR} as-is on some platforms.
        # But with a :- default, the custom regex handles it.
        assert _expand_vars("${UNSET_VAR_XYZ:-}") == ""

    def test_default_with_colon_equals(self, monkeypatch):
        monkeypatch.delenv("MISSING_VAR", raising=False)
        assert _expand_vars("${MISSING_VAR:=fallback}") == "fallback"

    def test_default_with_colon_dash(self, monkeypatch):
        monkeypatch.delenv("MISSING_VAR", raising=False)
        assert _expand_vars("${MISSING_VAR:-fallback}") == "fallback"

    def test_default_not_used_when_var_set(self, monkeypatch):
        monkeypatch.setenv("MY_VAR", "real_value")
        assert _expand_vars("${MY_VAR:=fallback}") == "real_value"

    def test_multiple_vars(self, monkeypatch):
        monkeypatch.setenv("HOST", "localhost")
        monkeypatch.setenv("PORT", "8080")
        result = _expand_vars("http://${HOST}:${PORT}/api")
        assert result == "http://localhost:8080/api"

    def test_mixed_set_and_defaults(self, monkeypatch):
        monkeypatch.setenv("A", "alpha")
        monkeypatch.delenv("B", raising=False)
        result = _expand_vars("${A}/${B:-beta}")
        assert result == "alpha/beta"


class TestPrimitiveProvidersConfig:
    """Tests for multi-provider config."""

    def test_multi_provider_format_passthrough(self):
        cfg = PrimitiveProvidersConfig(
            **{
                "default": "provider_a",
                "backends": {
                    "provider_a": {"backend": "a.Module", "config": {}},
                    "provider_b": {"backend": "b.Module", "config": {}},
                },
            }
        )
        assert cfg.default == "provider_a"
        assert len(cfg.backends) == 2


class TestSettingsLoad:
    """Tests for Settings.load() with and without config file."""

    def test_load_without_config_file(self, monkeypatch):
        monkeypatch.delenv("AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE", raising=False)
        s = Settings.load()
        assert s.host == "0.0.0.0"
        assert s.port == 8000

    def test_load_with_config_file(self, monkeypatch, tmp_path):
        config_content = textwrap.dedent("""\
            host: "127.0.0.1"
            port: 9999
            log_level: "debug"
        """)
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text(config_content)
        monkeypatch.setenv("AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE", str(config_file))
        s = Settings.load()
        assert s.host == "127.0.0.1"
        assert s.port == 9999
        assert s.log_level == "debug"

    def test_load_with_env_expansion_in_config(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TEST_HOST", "10.0.0.1")
        config_content = textwrap.dedent("""\
            host: "${TEST_HOST}"
            port: 7777
        """)
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text(config_content)
        monkeypatch.setenv("AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE", str(config_file))
        s = Settings.load()
        assert s.host == "10.0.0.1"

    def test_load_with_nonexistent_config_file(self, monkeypatch):
        monkeypatch.setenv("AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE", "/nonexistent/path.yaml")
        s = Settings.load()
        # Falls back to defaults
        assert s.host == "0.0.0.0"

    def test_load_empty_yaml(self, monkeypatch, tmp_path):
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("")
        monkeypatch.setenv("AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE", str(config_file))
        s = Settings.load()
        assert s.host == "0.0.0.0"
