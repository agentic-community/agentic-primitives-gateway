"""Intent-level test: YAML config env-var expansion lands the right
values in a fully-loaded ``Settings`` object.

Contract: ``${VAR}`` substitutes the env value; ``${VAR:=default}``
and ``${VAR:-default}`` substitute the default when VAR is unset;
missing plain ``${VAR}`` renders as empty string.  Operators rely
on this to write one YAML config that works across dev / staging /
production by injecting env vars — if expansion silently failed
(e.g., defaults worked but env override didn't), deployments would
run with the wrong values without warning.

Existing ``test_config.py::test_load_with_env_expansion_in_config``
covers one top-level expansion.  Nothing verifies:
- Expansion inside nested provider configs.
- Default-value substitution reaches the final Settings object.
- An env-set value overrides a default correctly.
- Values with special characters (slashes, colons, URLs) survive.

This file covers the end-to-end path: write a realistic YAML ->
Settings.load() -> assert the Settings values match what the
expansion rules say they should.
"""

from __future__ import annotations

import textwrap

from agentic_primitives_gateway.config import Settings


class TestEndToEndExpansion:
    def test_default_substituted_when_env_unset(self, monkeypatch, tmp_path):
        """``${VAR:=default}`` with VAR unset → the default reaches
        the Settings object.
        """
        monkeypatch.delenv("UNSET_HOST_VAR_123", raising=False)
        yaml = textwrap.dedent(
            """\
            host: "${UNSET_HOST_VAR_123:=0.0.0.0}"
            port: 9999
            """
        )
        cfg = tmp_path / "c.yaml"
        cfg.write_text(yaml)
        monkeypatch.setenv("AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE", str(cfg))
        s = Settings.load()
        assert s.host == "0.0.0.0"
        assert s.port == 9999

    def test_env_override_beats_default(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MY_HOST", "10.1.2.3")
        yaml = textwrap.dedent(
            """\
            host: "${MY_HOST:=0.0.0.0}"
            """
        )
        cfg = tmp_path / "c.yaml"
        cfg.write_text(yaml)
        monkeypatch.setenv("AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE", str(cfg))
        s = Settings.load()
        assert s.host == "10.1.2.3", f"Env var should override default; got {s.host!r}"

    def test_expansion_inside_nested_provider_config(self, monkeypatch, tmp_path):
        """Provider configs are nested dicts.  Expansion must
        recurse into every string value — a common pattern is
        ``redis_url: "${REDIS_URL:=redis://localhost:6379/0}"``.
        """
        monkeypatch.setenv("TEST_REDIS_URL", "redis://prod-cache:6379/2")
        yaml = textwrap.dedent(
            """\
            providers:
              memory:
                backend: "agentic_primitives_gateway.primitives.memory.in_memory.InMemoryProvider"
                config:
                  redis_url: "${TEST_REDIS_URL:=redis://localhost:6379/0}"
            """
        )
        cfg = tmp_path / "c.yaml"
        cfg.write_text(yaml)
        monkeypatch.setenv("AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE", str(cfg))
        s = Settings.load()
        # Expansion reaches the nested dict.
        # Settings normalizes single-provider format to multi-provider.
        memory_cfg = s.providers.memory.backends["default"].config
        assert memory_cfg["redis_url"] == "redis://prod-cache:6379/2"

    def test_expansion_preserves_url_special_chars(self, monkeypatch, tmp_path):
        """Values with colons, slashes, at-signs (e.g., Redis
        URLs with auth) must not be mangled by the regex.
        """
        url = "redis://user:p%40ss@host:6380/3"
        monkeypatch.setenv("COMPLEX_URL", url)
        yaml = textwrap.dedent(
            """\
            providers:
              memory:
                backend: "x.y.Z"
                config:
                  redis_url: "${COMPLEX_URL}"
            """
        )
        cfg = tmp_path / "c.yaml"
        cfg.write_text(yaml)
        monkeypatch.setenv("AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE", str(cfg))
        s = Settings.load()
        assert s.providers.memory.backends["default"].config["redis_url"] == url

    def test_unset_without_default_left_literal(self, monkeypatch, tmp_path):
        """``${VAR}`` with VAR unset is left as the literal string
        ``${VAR}`` (Python's ``os.path.expandvars`` behavior).  The
        _expand_vars docstring claims "empty string" but the actual
        implementation delegates to expandvars, which preserves
        unknown names.  This test pins the behavior so a future
        regression in either direction surfaces deliberately.

        If this becomes "" in a future refactor, update this test
        AND the _expand_vars docstring — both must agree.
        """
        monkeypatch.delenv("NEVER_SET_VAR", raising=False)
        yaml = textwrap.dedent(
            """\
            providers:
              memory:
                backend: "x.y.Z"
                config:
                  field: "${NEVER_SET_VAR}"
            """
        )
        cfg = tmp_path / "c.yaml"
        cfg.write_text(yaml)
        monkeypatch.setenv("AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE", str(cfg))
        s = Settings.load()
        # Current behavior: literal preserved.
        assert s.providers.memory.backends["default"].config["field"] == "${NEVER_SET_VAR}"

    def test_multiple_vars_in_one_value(self, monkeypatch, tmp_path):
        """Two separate ``${VAR}`` substitutions in one string value
        both expand correctly.  Guards against a greedy regex
        regression that consumed too much.
        """
        monkeypatch.setenv("H1", "host1")
        monkeypatch.setenv("P1", "9999")
        yaml = textwrap.dedent(
            """\
            providers:
              memory:
                backend: "x.y.Z"
                config:
                  endpoint: "https://${H1}:${P1}/api"
            """
        )
        cfg = tmp_path / "c.yaml"
        cfg.write_text(yaml)
        monkeypatch.setenv("AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE", str(cfg))
        s = Settings.load()
        assert s.providers.memory.backends["default"].config["endpoint"] == "https://host1:9999/api"

    def test_default_with_special_chars(self, monkeypatch, tmp_path):
        """A default containing slashes, colons, equals signs
        must be preserved verbatim.  Common real-world pattern:
        ``${REDIS_URL:=redis://localhost:6379/0}``.
        """
        monkeypatch.delenv("UNSET_X", raising=False)
        yaml = textwrap.dedent(
            """\
            providers:
              memory:
                backend: "x.y.Z"
                config:
                  redis_url: "${UNSET_X:=redis://localhost:6379/0}"
            """
        )
        cfg = tmp_path / "c.yaml"
        cfg.write_text(yaml)
        monkeypatch.setenv("AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE", str(cfg))
        s = Settings.load()
        assert s.providers.memory.backends["default"].config["redis_url"] == "redis://localhost:6379/0"
