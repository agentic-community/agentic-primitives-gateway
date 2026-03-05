from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml


def _expand_vars(text: str) -> str:
    """Expand shell-style variables in text.

    Supports:
        ${VAR}            — replaced with env value, or empty string
        ${VAR:=default}   — replaced with env value, or 'default' if unset
        ${VAR:-default}   — same as :=
        $VAR              — simple form
    """

    def _replace(match: re.Match) -> str:
        var = match.group("var")
        default = match.group("default")
        value = os.environ.get(var)
        if value is not None:
            return value
        if default is not None:
            return str(default)
        return ""

    # Handle ${VAR:=default} and ${VAR:-default} first
    text = str(
        re.sub(
            r"\$\{(?P<var>[A-Za-z_][A-Za-z0-9_]*)[:][=\-](?P<default>[^}]*)\}",
            _replace,
            text,
        )
    )
    # Then handle plain ${VAR} and $VAR
    text = os.path.expandvars(text)
    return text


from pydantic import BaseModel, Field, model_validator  # noqa: E402
from pydantic_settings import BaseSettings, SettingsConfigDict  # noqa: E402

from agentic_primitives_gateway.models.enums import LogLevel, Primitive  # noqa: E402


class ProviderConfig(BaseModel):
    backend: str
    config: dict = Field(default_factory=dict)


class PrimitiveProvidersConfig(BaseModel):
    """Configuration for a single primitive that supports multiple backends.

    Supports two formats:

    Legacy (single provider)::

        memory:
          backend: "...InMemoryProvider"
          config: {}

    Multi-provider::

        memory:
          default: "mem0"
          backends:
            mem0:
              backend: "...Mem0MemoryProvider"
              config: { ... }
            agentcore:
              backend: "...AgentCoreMemoryProvider"
              config: { ... }
    """

    default: str
    backends: dict[str, ProviderConfig]

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if isinstance(data, dict) and "backend" in data and "backends" not in data:
            # Legacy single-provider format → convert to multi-provider
            return {
                "default": "default",
                "backends": {
                    "default": {
                        "backend": data["backend"],
                        "config": data.get("config", {}),
                    }
                },
            }
        return data


_DEFAULTS: dict[str, dict[str, Any]] = {
    Primitive.MEMORY: {
        "default": "in_memory",
        "backends": {
            "in_memory": {
                "backend": "agentic_primitives_gateway.primitives.memory.in_memory.InMemoryProvider",
            }
        },
    },
    Primitive.OBSERVABILITY: {
        "default": "noop",
        "backends": {
            "noop": {
                "backend": "agentic_primitives_gateway.primitives.observability.noop.NoopObservabilityProvider",
            }
        },
    },
    Primitive.GATEWAY: {
        "default": "noop",
        "backends": {
            "noop": {
                "backend": "agentic_primitives_gateway.primitives.gateway.noop.NoopGatewayProvider",
            }
        },
    },
    Primitive.TOOLS: {
        "default": "noop",
        "backends": {
            "noop": {
                "backend": "agentic_primitives_gateway.primitives.tools.noop.NoopToolsProvider",
            }
        },
    },
    Primitive.IDENTITY: {
        "default": "noop",
        "backends": {
            "noop": {
                "backend": "agentic_primitives_gateway.primitives.identity.noop.NoopIdentityProvider",
            }
        },
    },
    Primitive.CODE_INTERPRETER: {
        "default": "noop",
        "backends": {
            "noop": {
                "backend": "agentic_primitives_gateway.primitives.code_interpreter.noop.NoopCodeInterpreterProvider",
            }
        },
    },
    Primitive.BROWSER: {
        "default": "noop",
        "backends": {
            "noop": {
                "backend": "agentic_primitives_gateway.primitives.browser.noop.NoopBrowserProvider",
            }
        },
    },
    Primitive.POLICY: {
        "default": "noop",
        "backends": {
            "noop": {
                "backend": "agentic_primitives_gateway.primitives.policy.noop.NoopPolicyProvider",
            }
        },
    },
    Primitive.EVALUATIONS: {
        "default": "noop",
        "backends": {
            "noop": {
                "backend": "agentic_primitives_gateway.primitives.evaluations.noop.NoopEvaluationsProvider",
            }
        },
    },
}


class ProvidersConfig(BaseModel):
    memory: PrimitiveProvidersConfig = PrimitiveProvidersConfig(**_DEFAULTS[Primitive.MEMORY])
    observability: PrimitiveProvidersConfig = PrimitiveProvidersConfig(**_DEFAULTS[Primitive.OBSERVABILITY])
    gateway: PrimitiveProvidersConfig = PrimitiveProvidersConfig(**_DEFAULTS[Primitive.GATEWAY])
    tools: PrimitiveProvidersConfig = PrimitiveProvidersConfig(**_DEFAULTS[Primitive.TOOLS])
    identity: PrimitiveProvidersConfig = PrimitiveProvidersConfig(**_DEFAULTS[Primitive.IDENTITY])
    code_interpreter: PrimitiveProvidersConfig = PrimitiveProvidersConfig(**_DEFAULTS[Primitive.CODE_INTERPRETER])
    browser: PrimitiveProvidersConfig = PrimitiveProvidersConfig(**_DEFAULTS[Primitive.BROWSER])
    policy: PrimitiveProvidersConfig = PrimitiveProvidersConfig(**_DEFAULTS[Primitive.POLICY])
    evaluations: PrimitiveProvidersConfig = PrimitiveProvidersConfig(**_DEFAULTS[Primitive.EVALUATIONS])


class EnforcementConfig(BaseModel):
    """Configuration for the policy enforcement layer."""

    backend: str = "agentic_primitives_gateway.enforcement.noop.NoopPolicyEnforcer"
    config: dict[str, Any] = Field(default_factory=dict)


class AgentsConfig(BaseModel):
    """Configuration for the agents subsystem."""

    store_path: str = "agents.json"
    default_model: str = "us.anthropic.claude-sonnet-4-20250514-v1:0"
    max_turns: int = 20
    specs: dict[str, dict[str, Any]] = Field(default_factory=dict)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENTIC_PRIMITIVES_GATEWAY_",
        env_nested_delimiter="__",
    )

    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = LogLevel.INFO
    config_file: str | None = None
    allow_server_credentials: bool = False
    cors_origins: list[str] = ["*"]
    providers: ProvidersConfig = ProvidersConfig()
    enforcement: EnforcementConfig = EnforcementConfig()
    agents: AgentsConfig = AgentsConfig()

    @staticmethod
    def config_file_path() -> str | None:
        """Return the config file path from the environment, or None."""
        return os.environ.get("AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE")

    @classmethod
    def load(cls) -> Settings:
        """Load settings, merging config file values with env overrides."""
        config_path = os.environ.get("AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE")
        if config_path and Path(config_path).exists():
            with open(config_path) as f:
                raw = f.read()
            expanded = _expand_vars(raw)
            file_config = yaml.safe_load(expanded) or {}
            return cls(**file_config)
        return cls()


settings = Settings.load()
